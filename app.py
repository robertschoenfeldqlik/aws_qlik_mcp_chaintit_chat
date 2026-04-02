"""Chainlit chat app with AWS Bedrock LLM and Qlik MCP integration.

Uses OAuth Authorization Code + PKCE to authenticate with Qlik Cloud.
"""

import asyncio
import os
import sys
import traceback as tb
import urllib.parse

import chainlit as cl
from chainlit.input_widget import Select, Slider, TextInput
from chainlit.server import app as fastapi_app
from langchain_aws.chat_models import ChatBedrockConverse
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from loguru import logger

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from typing import cast

from qlik_oauth import (
    QlikTokens,
    is_token_valid,
    refresh_access_token,
    register_oauth_routes,
)

load_dotenv()

logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

# Register OAuth routes on Chainlit's FastAPI app
register_oauth_routes(fastapi_app)

# Available Bedrock models
BEDROCK_MODELS = {
    "Claude 4 Sonnet": "anthropic.claude-sonnet-4-20250514",
    "Claude 3.7 Sonnet": "anthropic.claude-3-7-sonnet-20250219-v1:0",
    "Claude 3.5 Haiku": "anthropic.claude-3-5-haiku-20241022-v1:0",
    "Claude 3.5 Sonnet v2": "anthropic.claude-3-5-sonnet-20241022-v2:0",
}

AWS_REGIONS = [
    "us-east-1",
    "us-west-2",
    "eu-west-1",
    "ap-southeast-1",
    "ap-northeast-1",
]

SYSTEM_PROMPT = (
    "You are a helpful data analyst assistant connected to Qlik Cloud. "
    "Use the available tools to query and analyze data from Qlik applications. "
    "When presenting data, format it clearly using tables or bullet points. "
    "If you're unsure about available data, use the tools to explore what's available first."
)

QLIK_MCP_HELP_URL = "https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm"


# ---------------------------------------------------------------------------
# AWS Bedrock Helpers
# ---------------------------------------------------------------------------


def get_bedrock_client(region: str, access_key: str = "", secret_key: str = ""):
    """Create a Bedrock runtime client with optional explicit credentials."""
    kwargs = {
        "service_name": "bedrock-runtime",
        "region_name": region,
        "config": Config(
            retries={"max_attempts": 5, "mode": "adaptive"},
            read_timeout=60,
        ),
    }
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


def get_chat_model(
    model_id: str,
    region: str,
    temperature: float,
    max_tokens: int,
    access_key: str = "",
    secret_key: str = "",
):
    """Create a ChatBedrockConverse model."""
    client = get_bedrock_client(region, access_key, secret_key)
    full_model_id = f"us.{model_id}"
    return ChatBedrockConverse(
        model=full_model_id,
        client=client,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# Qlik MCP Connection
# ---------------------------------------------------------------------------


async def connect_qlik_mcp(tenant_url: str, access_token: str):
    """Connect to Qlik MCP server via SSE using an OAuth Bearer token."""
    mcp_url = f"{tenant_url.rstrip('/')}/api/ai/mcp"

    mcp_client = MultiServerMCPClient(
        {
            "qlik": {
                "url": mcp_url,
                "transport": "sse",
                "headers": {"Authorization": f"Bearer {access_token}"},
            }
        }
    )
    await mcp_client.__aenter__()
    tools = mcp_client.get_tools()
    return mcp_client, tools


async def disconnect_qlik_mcp():
    """Safely disconnect from Qlik MCP."""
    mcp_client = cl.user_session.get("mcp_client")
    if mcp_client:
        try:
            await mcp_client.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"Error disconnecting MCP: {e}")
        cl.user_session.set("mcp_client", None)
        cl.user_session.set("mcp_tools", None)
        cl.user_session.set("agent", None)


async def ensure_mcp_connected():
    """Check MCP connection and reconnect if needed. Returns True if connected."""
    agent = cl.user_session.get("agent")
    if agent:
        # Check token validity
        tokens: QlikTokens | None = cl.user_session.get("qlik_tokens")
        if tokens and not is_token_valid(tokens):
            new_tokens = await refresh_access_token(tokens)
            if new_tokens:
                cl.user_session.set("qlik_tokens", new_tokens)
                await disconnect_qlik_mcp()
                mcp_client, tools = await connect_qlik_mcp(
                    new_tokens.tenant_url, new_tokens.access_token
                )
                cl.user_session.set("mcp_client", mcp_client)
                cl.user_session.set("mcp_tools", tools)
                build_agent_if_ready()
                return True
            else:
                cl.user_session.set("agent", None)
                return False
        return True

    # Try to reconnect using saved tokens
    tokens = cl.user_session.get("qlik_tokens")
    if not tokens:
        return False

    if not is_token_valid(tokens):
        new_tokens = await refresh_access_token(tokens)
        if not new_tokens:
            return False
        tokens = new_tokens
        cl.user_session.set("qlik_tokens", tokens)

    try:
        await disconnect_qlik_mcp()
        mcp_client, tools = await connect_qlik_mcp(tokens.tenant_url, tokens.access_token)
        chat_model = cl.user_session.get("chat_model")
        agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)

        cl.user_session.set("mcp_client", mcp_client)
        cl.user_session.set("mcp_tools", tools)
        cl.user_session.set("agent", agent)

        await cl.Message(
            content=f"Back in action! Reconnected to Qlik MCP with **{len(tools)} tools** available."
        ).send()
        return True
    except Exception as e:
        logger.error(f"Reconnection failed: {e}")
        cl.user_session.set("agent", None)
        return False


def build_agent_if_ready():
    """Build the agent if both model and tools are available."""
    chat_model = cl.user_session.get("chat_model")
    tools = cl.user_session.get("mcp_tools")
    if chat_model and tools:
        agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)
        cl.user_session.set("agent", agent)
        return agent
    return None


# ---------------------------------------------------------------------------
# UI Helpers
# ---------------------------------------------------------------------------


async def send_connect_button():
    """Send a message with the Connect to Qlik button."""
    actions = [
        cl.Action(
            name="connect_qlik",
            label="Connect to Qlik Cloud",
            description="Authenticate with Qlik Cloud via OAuth",
            payload={},
        )
    ]
    await cl.Message(
        content="Click the button below to authenticate with Qlik Cloud.",
        actions=actions,
    ).send()


async def send_reconnect_button():
    """Send a message with a Reconnect button."""
    actions = [
        cl.Action(
            name="connect_qlik",
            label="Reconnect to Qlik Cloud",
            description="Re-authenticate with Qlik Cloud",
            payload={},
        )
    ]
    await cl.Message(
        content="Use the button below to reconnect, or open **Settings** to change your configuration.",
        actions=actions,
    ).send()


# ---------------------------------------------------------------------------
# Action Callbacks
# ---------------------------------------------------------------------------


@cl.action_callback("connect_qlik")
async def on_connect_qlik(action: cl.Action):
    """Handle the Connect to Qlik button — initiate OAuth PKCE flow."""
    tenant_url = cl.user_session.get("qlik_tenant_url")
    client_id = cl.user_session.get("qlik_client_id")

    if not tenant_url or not client_id:
        await cl.Message(
            content="Please set **Qlik Tenant URL** and **OAuth Client ID** in Settings first."
        ).send()
        return

    session_id = cl.context.session.id
    base_url = os.getenv("APP_BASE_URL", "http://localhost:8000")

    oauth_url = (
        f"{base_url}/auth/qlik/start?"
        + urllib.parse.urlencode({
            "session_id": session_id,
            "tenant_url": tenant_url,
            "client_id": client_id,
        })
    )

    cl.user_session.set("oauth_complete", False)
    cl.user_session.set("oauth_error", None)

    await cl.Message(
        content=(
            f"**[Click here to sign in to Qlik Cloud]({oauth_url})**\n\n"
            "_Waiting for authentication..._"
        )
    ).send()

    # Poll for OAuth completion (max 120 seconds)
    for _ in range(60):
        await asyncio.sleep(2)
        if cl.user_session.get("oauth_complete"):
            break

    if not cl.user_session.get("oauth_complete"):
        await cl.Message(content="Authentication timed out. Please try again.").send()
        await send_connect_button()
        return

    # OAuth succeeded — connect MCP with the Bearer token
    tokens: QlikTokens = cl.user_session.get("qlik_tokens")

    try:
        await disconnect_qlik_mcp()
        mcp_client, tools = await connect_qlik_mcp(tenant_url, tokens.access_token)
        cl.user_session.set("mcp_client", mcp_client)
        cl.user_session.set("mcp_tools", tools)
        build_agent_if_ready()

        tool_names = [t.name for t in tools]
        actions = [
            cl.Action(
                name="connect_qlik",
                label="Reconnect to Qlik Cloud",
                description="Re-authenticate with Qlik Cloud",
                payload={},
            )
        ]
        await cl.Message(
            content=(
                f"Authenticated! Connected to Qlik MCP with **{len(tools)} tools**:\n"
                + "\n".join(f"- `{name}`" for name in tool_names)
            ),
            actions=actions,
        ).send()
    except Exception as e:
        await cl.Message(
            content=f"Authenticated with Qlik but MCP connection failed:\n```\n{str(e)}\n```"
        ).send()
        await send_reconnect_button()


# ---------------------------------------------------------------------------
# Chat Lifecycle
# ---------------------------------------------------------------------------


@cl.on_chat_start
async def on_chat_start():
    """Initialize chat session with settings panel."""
    default_region = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
    default_access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
    default_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    default_tenant = os.getenv("QLIK_TENANT_URL", "")
    default_client_id = os.getenv("QLIK_OAUTH_CLIENT_ID", "")

    settings = cl.ChatSettings(
        [
            # --- Qlik MCP Settings ---
            TextInput(
                id="qlik_tenant_url",
                label="Qlik Tenant URL",
                initial=default_tenant,
                placeholder="https://your-tenant.us.qlikcloud.com",
                description="Your Qlik Cloud tenant URL",
            ),
            TextInput(
                id="qlik_client_id",
                label="OAuth Client ID",
                initial=default_client_id,
                placeholder="Your OAuth client ID",
                description="OAuth client ID from your Qlik tenant admin (scopes: user_default, mcp:execute)",
            ),
            # --- AWS Bedrock Settings ---
            TextInput(
                id="aws_access_key_id",
                label="AWS Access Key ID",
                initial=default_access_key,
                placeholder="AKIA...",
                description="IAM access key with bedrock:InvokeModel permissions",
            ),
            TextInput(
                id="aws_secret_access_key",
                label="AWS Secret Access Key",
                initial=default_secret_key,
                placeholder="Your secret key",
                description="IAM secret access key (stored in session only, never logged)",
            ),
            Select(
                id="aws_region",
                label="AWS Region",
                values=AWS_REGIONS,
                initial_value=default_region,
                description="AWS region for Bedrock API calls",
            ),
            Select(
                id="bedrock_model",
                label="Bedrock Model",
                values=list(BEDROCK_MODELS.keys()),
                initial_value="Claude 3.7 Sonnet",
                description="Select the foundation model to use",
            ),
            Slider(
                id="temperature",
                label="Temperature",
                initial=0.7,
                min=0.0,
                max=1.0,
                step=0.1,
                description="Controls randomness in responses",
            ),
            Slider(
                id="max_tokens",
                label="Max Tokens",
                initial=4096,
                min=256,
                max=32768,
                step=256,
                description="Maximum response length",
            ),
        ]
    )
    await settings.send()

    # Initialize Bedrock model with defaults
    model_id = BEDROCK_MODELS["Claude 3.7 Sonnet"]
    chat_model = get_chat_model(model_id, default_region, 0.7, 4096, default_access_key, default_secret_key)
    cl.user_session.set("chat_model", chat_model)
    cl.user_session.set("chat_messages", [])

    # Save Qlik settings for the connect button
    cl.user_session.set("qlik_tenant_url", default_tenant)
    cl.user_session.set("qlik_client_id", default_client_id)

    # Welcome message with Connect button if tenant/client_id are set
    if default_tenant and default_client_id:
        actions = [
            cl.Action(
                name="connect_qlik",
                label="Connect to Qlik Cloud",
                description="Authenticate with Qlik Cloud via OAuth",
                payload={},
            )
        ]
        await cl.Message(
            content=(
                "![Qlik](/public/qlik-logo.png)\n\n"
                "## Your Friendly Neighborhood AI Assistant\n\n"
                "Click the button below to sign in to Qlik Cloud."
            ),
            actions=actions,
        ).send()
    else:
        await cl.Message(
            content=(
                "![Qlik](/public/qlik-logo.png)\n\n"
                "## Your Friendly Neighborhood AI Assistant\n\n"
                "Let's get you connected:\n\n"
                "1. Open **Settings** (gear icon)\n"
                "2. Enter your **Qlik Tenant URL** and **OAuth Client ID**\n"
                "3. Enter your **AWS Access Key ID** and **Secret Access Key**\n"
                "4. Click **Confirm**, then click **Connect to Qlik Cloud**\n\n"
                f"[Qlik MCP setup guide]({QLIK_MCP_HELP_URL})"
            )
        ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    """Handle settings changes — update model and show Connect button."""
    # AWS settings
    access_key = (settings.get("aws_access_key_id") or "").strip()
    secret_key = (settings.get("aws_secret_access_key") or "").strip()
    model_name = settings.get("bedrock_model") or "Claude 3.7 Sonnet"
    model_id = BEDROCK_MODELS[model_name]
    region = settings.get("aws_region") or "us-west-2"
    temperature = settings.get("temperature") or 0.7
    max_tokens = int(settings.get("max_tokens") or 4096)

    # Qlik settings
    tenant_url = (settings.get("qlik_tenant_url") or "").strip()
    client_id = (settings.get("qlik_client_id") or "").strip()

    # Update Bedrock model
    chat_model = get_chat_model(model_id, region, temperature, max_tokens, access_key, secret_key)
    cl.user_session.set("chat_model", chat_model)

    # Save Qlik settings
    cl.user_session.set("qlik_tenant_url", tenant_url)
    cl.user_session.set("qlik_client_id", client_id)

    # Rebuild agent if tools are already loaded (model change)
    build_agent_if_ready()

    await cl.Message(
        content=f"Settings updated: **{model_name}** in **{region}** (temp={temperature}, max_tokens={max_tokens})"
    ).send()

    # Show Connect button if Qlik settings are filled but not yet authenticated
    if tenant_url and client_id and not cl.user_session.get("qlik_tokens"):
        await send_connect_button()


@cl.on_message
async def on_message(message: cl.Message):
    """Process user messages through the agent."""
    agent = cast(CompiledStateGraph | None, cl.user_session.get("agent"))

    # If no agent, try to reconnect with existing token
    if not agent:
        connected = await ensure_mcp_connected()
        if connected:
            agent = cast(CompiledStateGraph | None, cl.user_session.get("agent"))
        else:
            await cl.Message(
                content=(
                    "Not connected to Qlik MCP. "
                    "Click **Connect to Qlik Cloud** to authenticate, "
                    "or open **Settings** to configure your credentials.\n\n"
                    f"[Qlik MCP setup guide]({QLIK_MCP_HELP_URL})"
                )
            ).send()
            await send_connect_button()
            return

    config = RunnableConfig(configurable={"thread_id": cl.context.session.id})

    try:
        response_message = cl.Message(content="")
        async for msg, metadata in agent.astream(
            {"messages": message.content},
            stream_mode="messages",
            config=config,
        ):
            if isinstance(msg, AIMessageChunk) and msg.content:
                if isinstance(msg.content, str):
                    await response_message.stream_token(msg.content)
                elif (
                    isinstance(msg.content, list)
                    and len(msg.content) > 0
                    and isinstance(msg.content[0], dict)
                    and msg.content[0].get("type") == "text"
                    and "text" in msg.content[0]
                ):
                    await response_message.stream_token(msg.content[0]["text"])

        await response_message.send()

    except Exception as e:
        error_str = str(e).lower()
        # Auto-reconnect on connection/timeout errors
        if any(keyword in error_str for keyword in ["timeout", "closed", "connection", "eof", "reset", "401", "unauthorized"]):
            logger.warning(f"MCP connection error, attempting reconnect: {e}")
            cl.user_session.set("agent", None)
            connected = await ensure_mcp_connected()
            if connected:
                await cl.Message(
                    content="Connection was lost and has been restored. Please resend your message."
                ).send()
            else:
                await cl.Message(
                    content=f"Connection lost and auto-reconnect failed:\n```\n{str(e)}\n```\n\nPlease re-authenticate."
                ).send()
                await send_connect_button()
        else:
            await cl.Message(content=f"Error: {str(e)}").send()
            logger.error(tb.format_exc())
