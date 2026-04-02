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


def get_bedrock_client(region: str):
    """Create a Bedrock runtime client."""
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            retries={"max_attempts": 5, "mode": "adaptive"},
            read_timeout=60,
        ),
    )


def get_chat_model(model_id: str, region: str, temperature: float, max_tokens: int):
    """Create a ChatBedrockConverse model."""
    client = get_bedrock_client(region)
    full_model_id = f"us.{model_id}"
    return ChatBedrockConverse(
        model=full_model_id,
        client=client,
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def connect_qlik_mcp(tenant_url: str, api_key: str, app_id: str):
    """Connect to Qlik MCP server and return tools. Handles both native and community servers."""
    # Use Qlik's native remote MCP endpoint (SSE)
    mcp_url = f"{tenant_url.rstrip('/')}/api/ai/mcp"

    mcp_client = MultiServerMCPClient(
        {
            "qlik": {
                "url": mcp_url,
                "transport": "sse",
                "headers": {
                    "Authorization": f"Bearer {api_key}",
                },
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
    api_key = cl.user_session.get("qlik_api_key")
    app_id = cl.user_session.get("qlik_app_id")

    if not tenant_url or not api_key:
        return False

    try:
        await disconnect_qlik_mcp()
        mcp_client, tools = await connect_qlik_mcp(tenant_url, api_key, app_id)
        chat_model = cl.user_session.get("chat_model")
        agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)

        cl.user_session.set("mcp_client", mcp_client)
        cl.user_session.set("mcp_tools", tools)
        cl.user_session.set("agent", agent)

        await cl.Message(content=f"Reconnected to Qlik MCP. **{len(tools)} tools** available.").send()
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
    default_tenant = os.getenv("QLIK_TENANT_URL", "")
    default_api_key = os.getenv("QLIK_API_KEY", "")
    default_app_id = os.getenv("QLIK_APP_ID", "")

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
                id="qlik_api_key",
                label="Qlik API Key",
                initial=default_api_key,
                placeholder="your-api-key",
                description="API key for Qlik Cloud authentication",
            ),
            TextInput(
                id="qlik_app_id",
                label="Qlik App ID",
                initial=default_app_id,
                placeholder="your-app-id (optional)",
                description="Specific Qlik app to connect to",
            ),
            # --- Bedrock Settings ---
            Select(
                id="bedrock_model",
                label="Bedrock Model",
                values=list(BEDROCK_MODELS.keys()),
                initial_value="Claude 3.7 Sonnet",
                description="Select the foundation model to use",
            ),
            Select(
                id="aws_region",
                label="AWS Region",
                values=AWS_REGIONS,
                initial_value=default_region,
                description="AWS region for Bedrock API calls",
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
    chat_model = get_chat_model(model_id, default_region, 0.7, 4096)
    cl.user_session.set("chat_model", chat_model)
    cl.user_session.set("chat_messages", [])

    # Auto-connect to Qlik MCP if env vars are set
    if default_tenant and default_api_key:
        cl.user_session.set("qlik_tenant_url", default_tenant)
        cl.user_session.set("qlik_api_key", default_api_key)
        cl.user_session.set("qlik_app_id", default_app_id)
        try:
            mcp_client, tools = await connect_qlik_mcp(default_tenant, default_api_key, default_app_id)
            cl.user_session.set("mcp_client", mcp_client)
            cl.user_session.set("mcp_tools", tools)
            build_agent_if_ready()

            tool_names = [t.name for t in tools]
            actions = [cl.Action(name="reconnect_mcp", label="Reconnect to Qlik MCP", description="Re-establish the MCP connection", payload={})]
            await cl.Message(
                content=(
                    f"**Qlik AI Assistant** ready. Connected to Qlik MCP with **{len(tools)} tools**:\n"
                    + "\n".join(f"- `{name}`" for name in tool_names)
                ),
                actions=actions,
            ).send()
            return
        except Exception as e:
            logger.error(f"Auto-connect failed: {e}")
            await cl.Message(
                content=(
                    f"**Qlik AI Assistant** ready, but auto-connect to Qlik MCP failed:\n"
                    f"```\n{str(e)}\n```\n\n"
                    "Open **Settings** (gear icon) to configure your Qlik tenant URL and API key, then click Confirm."
                )
            ).send()
            return

    await cl.Message(
        content=(
            "**Qlik AI Assistant** ready.\n\n"
            "1. Open **Settings** (gear icon)\n"
            "2. Enter your **Qlik Tenant URL** and **API Key**\n"
            "3. Optionally configure the **Bedrock model** and **region**\n"
            "4. Click **Confirm** to connect\n\n"
            "The assistant will automatically connect to your Qlik MCP server."
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    """Handle settings changes — reconnect MCP and/or rebuild model."""
    # Extract settings
    model_name = settings.get("bedrock_model", "Claude 3.7 Sonnet")
    model_id = BEDROCK_MODELS[model_name]
    region = settings.get("aws_region", "us-west-2")
    temperature = settings.get("temperature", 0.7)
    max_tokens = int(settings.get("max_tokens", 4096))

    tenant_url = settings.get("qlik_tenant_url", "").strip()
    api_key = settings.get("qlik_api_key", "").strip()
    app_id = settings.get("qlik_app_id", "").strip()

    # Update Bedrock model
    chat_model = get_chat_model(model_id, region, temperature, max_tokens)
    cl.user_session.set("chat_model", chat_model)

    # Check if Qlik settings changed
    old_tenant = cl.user_session.get("qlik_tenant_url", "")
    old_key = cl.user_session.get("qlik_api_key", "")
    old_app = cl.user_session.get("qlik_app_id", "")

    qlik_changed = (tenant_url != old_tenant or api_key != old_key or app_id != old_app)

    # Save Qlik settings
    cl.user_session.set("qlik_tenant_url", tenant_url)
    cl.user_session.set("qlik_api_key", api_key)
    cl.user_session.set("qlik_app_id", app_id)

    # Reconnect MCP if Qlik settings changed
    if qlik_changed and tenant_url and api_key:
        await disconnect_qlik_mcp()
        try:
            mcp_client, tools = await connect_qlik_mcp(tenant_url, api_key, app_id)
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
                    "to enter your Qlik Tenant URL and API Key, then click Confirm."
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
