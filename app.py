"""Chainlit chat app with AWS Bedrock LLM and Qlik MCP integration."""

import os
import sys
import traceback as tb

import chainlit as cl
from chainlit.input_widget import Select, Slider, TextInput
from langchain_aws.chat_models import ChatBedrockConverse
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from loguru import logger

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from typing import cast

load_dotenv()

logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

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
    # Use explicit credentials if provided, otherwise fall back to env/IAM role
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


async def connect_qlik_mcp(tenant_url: str, client_id: str, client_secret: str):
    """Connect to Qlik MCP server via SSE using OAuth credentials.

    See: https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm
    """
    mcp_url = f"{tenant_url.rstrip('/')}/api/ai/mcp"

    headers = {"X-Qlik-OAuth-Client-Id": client_id}
    if client_secret:
        headers["X-Qlik-OAuth-Client-Secret"] = client_secret

    mcp_client = MultiServerMCPClient(
        {
            "qlik": {
                "url": mcp_url,
                "transport": "sse",
                "headers": headers,
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
        return True

    # Try to reconnect using saved settings
    tenant_url = cl.user_session.get("qlik_tenant_url")
    client_id = cl.user_session.get("qlik_client_id")
    client_secret = cl.user_session.get("qlik_client_secret")

    if not tenant_url or not client_id:
        return False

    try:
        await disconnect_qlik_mcp()
        mcp_client, tools = await connect_qlik_mcp(tenant_url, client_id, client_secret)
        chat_model = cl.user_session.get("chat_model")
        agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)

        cl.user_session.set("mcp_client", mcp_client)
        cl.user_session.set("mcp_tools", tools)
        cl.user_session.set("agent", agent)

        await cl.Message(content=f"Back in action! Reconnected to Qlik MCP with **{len(tools)} tools** available.").send()
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


async def send_reconnect_button():
    """Send a message with a Reconnect button."""
    actions = [
        cl.Action(
            name="reconnect_mcp",
            label="Reconnect to Qlik MCP",
            description="Re-establish the connection to Qlik MCP server",
            payload={},
        )
    ]
    await cl.Message(
        content="Use the button below to reconnect, or open **Settings** to change your configuration.",
        actions=actions,
    ).send()


@cl.action_callback("reconnect_mcp")
async def on_reconnect_action(action: cl.Action):
    """Handle the Reconnect button click."""
    await cl.Message(content="Reconnecting to Qlik MCP...").send()
    await disconnect_qlik_mcp()
    connected = await ensure_mcp_connected()
    if not connected:
        await cl.Message(
            content="Reconnection failed. Check your Qlik settings (gear icon) and try again."
        ).send()
        await send_reconnect_button()


@cl.on_chat_start
async def on_chat_start():
    """Initialize chat session with settings panel."""
    default_region = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
    default_access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
    default_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    default_tenant = os.getenv("QLIK_TENANT_URL", "")
    default_client_id = os.getenv("QLIK_OAUTH_CLIENT_ID", "")
    default_client_secret = os.getenv("QLIK_OAUTH_CLIENT_SECRET", "")

    settings = cl.ChatSettings(
        [
            # --- Qlik MCP Settings ---
            # See: https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm
            TextInput(
                id="qlik_tenant_url",
                label="Qlik Tenant URL",
                initial=default_tenant,
                placeholder="https://your-tenant.us.qlikcloud.com",
                description="Your Qlik Cloud tenant URL (MCP endpoint: <tenant>/api/ai/mcp)",
            ),
            TextInput(
                id="qlik_client_id",
                label="OAuth Client ID",
                initial=default_client_id,
                placeholder="Your OAuth client ID",
                description="OAuth client ID created by your Qlik tenant admin (scopes: user_default, mcp:execute)",
            ),
            TextInput(
                id="qlik_client_secret",
                label="OAuth Client Secret",
                initial=default_client_secret,
                placeholder="Leave empty if not required",
                description="OAuth client secret (leave empty for most configurations)",
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

    # Auto-connect to Qlik MCP if env vars are set
    if default_tenant and default_client_id:
        cl.user_session.set("qlik_tenant_url", default_tenant)
        cl.user_session.set("qlik_client_id", default_client_id)
        cl.user_session.set("qlik_client_secret", default_client_secret)
        try:
            mcp_client, tools = await connect_qlik_mcp(default_tenant, default_client_id, default_client_secret)
            cl.user_session.set("mcp_client", mcp_client)
            cl.user_session.set("mcp_tools", tools)
            build_agent_if_ready()

            tool_names = [t.name for t in tools]
            actions = [cl.Action(name="reconnect_mcp", label="Reconnect to Qlik MCP", description="Re-establish the MCP connection", payload={})]
            await cl.Message(
                content=(
                    f"Your friendly neighborhood **Qlik AI Assistant** is ready! Connected to Qlik MCP with **{len(tools)} tools**:\n"
                    + "\n".join(f"- `{name}`" for name in tool_names)
                ),
                actions=actions,
            ).send()
            return
        except Exception as e:
            logger.error(f"Auto-connect failed: {e}")
            await cl.Message(
                content=(
                    f"Your friendly neighborhood **Qlik AI Assistant** is here, but auto-connect to Qlik MCP failed:\n"
                    f"```\n{str(e)}\n```\n\n"
                    f"Open **Settings** (gear icon) to configure your Qlik Tenant URL and OAuth Client ID, then click Confirm.\n\n"
                    f"[Qlik MCP setup guide]({QLIK_MCP_HELP_URL})"
                )
            ).send()
            return

    await cl.Message(
        content=(
            "Your friendly neighborhood **Qlik AI Assistant** is here!\n\n"
            "Let's get you connected:\n\n"
            "**Qlik Cloud:**\n"
            "1. Enter your **Qlik Tenant URL** and **OAuth Client ID**\n"
            "2. Optionally enter the **OAuth Client Secret** (leave empty if not required)\n\n"
            "**AWS Bedrock:**\n"
            "3. Enter your **AWS Access Key ID** and **Secret Access Key**\n"
            "4. Select your **Region** and **Model**\n\n"
            "5. Click **Confirm** to connect!\n\n"
            f"[Qlik MCP setup guide]({QLIK_MCP_HELP_URL})"
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    """Handle settings changes — reconnect MCP and/or rebuild model."""
    # Extract settings
    # AWS settings
    access_key = settings.get("aws_access_key_id", "").strip()
    secret_key = settings.get("aws_secret_access_key", "").strip()
    model_name = settings.get("bedrock_model", "Claude 3.7 Sonnet")
    model_id = BEDROCK_MODELS[model_name]
    region = settings.get("aws_region", "us-west-2")
    temperature = settings.get("temperature", 0.7)
    max_tokens = int(settings.get("max_tokens", 4096))

    # Qlik settings
    tenant_url = settings.get("qlik_tenant_url", "").strip()
    client_id = settings.get("qlik_client_id", "").strip()
    client_secret = settings.get("qlik_client_secret", "").strip()

    # Update Bedrock model with credentials
    chat_model = get_chat_model(model_id, region, temperature, max_tokens, access_key, secret_key)
    cl.user_session.set("chat_model", chat_model)

    # Check if Qlik settings changed
    old_tenant = cl.user_session.get("qlik_tenant_url", "")
    old_id = cl.user_session.get("qlik_client_id", "")
    old_secret = cl.user_session.get("qlik_client_secret", "")

    qlik_changed = (tenant_url != old_tenant or client_id != old_id or client_secret != old_secret)

    # Save Qlik settings
    cl.user_session.set("qlik_tenant_url", tenant_url)
    cl.user_session.set("qlik_client_id", client_id)
    cl.user_session.set("qlik_client_secret", client_secret)

    # Reconnect MCP if Qlik settings changed
    if qlik_changed and tenant_url and client_id:
        await disconnect_qlik_mcp()
        try:
            mcp_client, tools = await connect_qlik_mcp(tenant_url, client_id, client_secret)
            cl.user_session.set("mcp_client", mcp_client)
            cl.user_session.set("mcp_tools", tools)
            build_agent_if_ready()

            tool_names = [t.name for t in tools]
            actions = [cl.Action(name="reconnect_mcp", label="Reconnect to Qlik MCP", description="Re-establish the MCP connection", payload={})]
            await cl.Message(
                content=(
                    f"Settings updated. Connected to Qlik MCP with **{len(tools)} tools**:\n"
                    + "\n".join(f"- `{name}`" for name in tool_names)
                    + f"\n\nModel: **{model_name}** in **{region}**"
                ),
                actions=actions,
            ).send()
            return
        except Exception as e:
            await cl.Message(
                content=f"Settings updated but Qlik MCP connection failed:\n```\n{str(e)}\n```"
            ).send()
            return

    # Just rebuild agent with new model if MCP already connected
    build_agent_if_ready()
    await cl.Message(
        content=f"Settings updated: **{model_name}** in **{region}** (temp={temperature}, max_tokens={max_tokens})"
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Process user messages through the agent, with auto-reconnect on timeout."""
    agent = cast(CompiledStateGraph | None, cl.user_session.get("agent"))

    # If no agent, try to reconnect
    if not agent:
        connected = await ensure_mcp_connected()
        if connected:
            agent = cast(CompiledStateGraph | None, cl.user_session.get("agent"))
        else:
            await cl.Message(
                content=(
                    "Not connected to Qlik MCP. Open **Settings** (gear icon) "
                    "to enter your Qlik Tenant URL and OAuth Client ID, then click Confirm.\n\n"
                    f"[Qlik MCP setup guide]({QLIK_MCP_HELP_URL})"
                )
            ).send()
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
        if any(keyword in error_str for keyword in ["timeout", "closed", "connection", "eof", "reset"]):
            logger.warning(f"MCP connection error, attempting reconnect: {e}")
            cl.user_session.set("agent", None)
            connected = await ensure_mcp_connected()
            if connected:
                await cl.Message(
                    content="Connection was lost and has been restored. Please resend your message."
                ).send()
            else:
                await cl.Message(
                    content=f"Connection lost and auto-reconnect failed:\n```\n{str(e)}\n```"
                ).send()
                await send_reconnect_button()
        else:
            await cl.Message(content=f"Error: {str(e)}").send()
            logger.error(tb.format_exc())
