"""Chainlit chat app with AWS Bedrock LLM and Qlik MCP integration.

Plug icon → Qlik Cloud form (Tenant URL + Client ID) → OAuth redirect → connected.
Gear icon → AWS Bedrock settings (credentials, model, region).
"""

import asyncio
import os
import sys
import traceback as tb

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

from qlik_oauth import register_oauth_routes, completed_tokens

load_dotenv()

logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

# Register OAuth routes on Chainlit's FastAPI app
register_oauth_routes(fastapi_app)

BEDROCK_MODELS = {
    "Amazon Nova Pro": "amazon.nova-pro-v1:0",
    "Amazon Nova Lite": "amazon.nova-lite-v1:0",
    "Amazon Nova Micro": "amazon.nova-micro-v1:0",
    "Claude 4 Sonnet": "anthropic.claude-sonnet-4-20250514-v1:0",
    "Claude 4 Haiku": "anthropic.claude-haiku-4-20250514-v1:0",
}

AWS_REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1", "ap-northeast-1"]

SYSTEM_PROMPT = (
    "You are a helpful data analyst assistant connected to Qlik Cloud. "
    "Use the available tools to query and analyze data from Qlik applications. "
    "When presenting data, format it clearly using tables or bullet points. "
    "If you're unsure about available data, use the tools to explore what's available first."
)

QLIK_MCP_HELP_URL = "https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm"


# ---------------------------------------------------------------------------
# Bedrock
# ---------------------------------------------------------------------------

def get_chat_model(model_id, region, temperature, max_tokens, api_key=""):
    # Set bearer token env var so boto3 picks it up
    if api_key:
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key
    client = boto3.client(
        "bedrock-runtime", region_name=region,
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}, read_timeout=60),
    )
    return ChatBedrockConverse(model=f"us.{model_id}", client=client, temperature=temperature, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Qlik MCP
# ---------------------------------------------------------------------------

async def connect_qlik_mcp(tenant_url: str, access_token: str):
    """Connect to Qlik MCP using a Bearer token from OAuth."""
    mcp_url = f"{tenant_url.rstrip('/')}/api/ai/mcp"
    mcp_client = MultiServerMCPClient({
        "qlik": {"url": mcp_url, "transport": "sse", "headers": {"Authorization": f"Bearer {access_token}"}},
    })
    await mcp_client.__aenter__()
    return mcp_client, mcp_client.get_tools()


async def disconnect_qlik_mcp():
    mcp_client = cl.user_session.get("mcp_client")
    if mcp_client:
        try:
            await mcp_client.__aexit__(None, None, None)
        except Exception:
            pass
        cl.user_session.set("mcp_client", None)
        cl.user_session.set("mcp_tools", None)
        cl.user_session.set("agent", None)


def build_agent_if_ready():
    chat_model = cl.user_session.get("chat_model")
    tools = cl.user_session.get("mcp_tools")
    if chat_model and tools:
        agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)
        cl.user_session.set("agent", agent)
        return agent
    return None


# ---------------------------------------------------------------------------
# MCP Connect/Disconnect (plug icon triggers OAuth via custom JS)
# The JS form posts to /auth/qlik/start which redirects to Qlik OAuth.
# After OAuth, the callback stores the token in the session.
# We poll here for oauth_complete to finish the connection.
# ---------------------------------------------------------------------------

@cl.on_mcp_connect
async def on_mcp_connect(connection, session):
    """Called when plug icon dialog submits — but we handle auth via custom JS + OAuth.
    This is a fallback for direct SSE connections."""
    await session.initialize()
    from langchain_mcp_adapters.tools import load_mcp_tools
    tools = await load_mcp_tools(session)
    chat_model = cl.user_session.get("chat_model")
    agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)
    cl.user_session.set("agent", agent)
    cl.user_session.set("mcp_session", session)
    cl.user_session.set("mcp_tools", tools)
    tool_names = [t.name for t in tools]
    await cl.Message(content=f"Connected! **{len(tools)} tools** available:\n" + "\n".join(f"- `{n}`" for n in tool_names)).send()


@cl.on_mcp_disconnect
async def on_mcp_disconnect(name, session):
    try:
        await session.__aexit__(None, None, None)
    except Exception:
        pass
    cl.user_session.set("mcp_session", None)
    cl.user_session.set("mcp_tools", None)
    cl.user_session.set("agent", None)


# ---------------------------------------------------------------------------
# Background poller: watches for OAuth completion from the callback
# ---------------------------------------------------------------------------

async def poll_for_oauth_and_connect():
    """Poll completed_tokens for any new OAuth token, then connect to MCP.

    Runs as a background task per session. Checks every 2s for up to 3 minutes.
    When a token appears, consumes it, connects MCP, and stops.
    """
    while True:
        for _ in range(90):  # 3 minutes per cycle
            await asyncio.sleep(2)

            # Check if any OAuth flow completed
            for state, token_data in list(completed_tokens.items()):
                # Consume it
                completed_tokens.pop(state, None)
                access_token = token_data["access_token"]
                tenant_url = token_data["tenant_url"]

                # Save token for reconnection
                cl.user_session.set("qlik_access_token", access_token)
                cl.user_session.set("qlik_tenant_url", tenant_url)

                try:
                    await disconnect_qlik_mcp()
                    mcp_client, tools = await connect_qlik_mcp(tenant_url, access_token)
                    cl.user_session.set("mcp_client", mcp_client)
                    cl.user_session.set("mcp_tools", tools)
                    build_agent_if_ready()
                    tool_names = [t.name for t in tools]
                    actions = [cl.Action(name="reconnect_qlik", label="Reconnect to Qlik", description="Re-establish Qlik MCP connection", payload={})]
                    await cl.Message(
                        content=f"Authenticated! Connected to Qlik MCP with **{len(tools)} tools**:\n"
                        + "\n".join(f"- `{n}`" for n in tool_names),
                        actions=actions,
                    ).send()
                except Exception as e:
                    await cl.Message(content=f"OAuth succeeded but MCP connection failed:\n```\n{e}\n```").send()
                return  # Done — connected

        # After 3 min timeout, loop again (user might click Connect later)
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Reconnect Action
# ---------------------------------------------------------------------------


@cl.action_callback("reconnect_qlik")
async def on_reconnect_qlik(action: cl.Action):
    """Reconnect to Qlik MCP using the saved OAuth token."""
    access_token = cl.user_session.get("qlik_access_token")
    tenant_url = cl.user_session.get("qlik_tenant_url")

    if not access_token or not tenant_url:
        await cl.Message(content="No saved credentials. Click the **plug icon** to connect to Qlik Cloud.").send()
        return

    await cl.Message(content="Reconnecting to Qlik MCP...").send()

    try:
        await disconnect_qlik_mcp()
        mcp_client, tools = await connect_qlik_mcp(tenant_url, access_token)
        cl.user_session.set("mcp_client", mcp_client)
        cl.user_session.set("mcp_tools", tools)
        build_agent_if_ready()
        tool_names = [t.name for t in tools]
        actions = [cl.Action(name="reconnect_qlik", label="Reconnect to Qlik", description="Re-establish Qlik MCP connection", payload={})]
        await cl.Message(
            content=f"Reconnected! **{len(tools)} tools** available:\n" + "\n".join(f"- `{n}`" for n in tool_names),
            actions=actions,
        ).send()
    except Exception as e:
        await cl.Message(
            content=f"Reconnection failed:\n```\n{e}\n```\n\nYour token may have expired. Click the **plug icon** to re-authenticate."
        ).send()


# ---------------------------------------------------------------------------
# Chat Lifecycle
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_chat_start():
    default_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    default_api_key = os.getenv("AWS_BEARER_TOKEN_BEDROCK", "")

    settings = cl.ChatSettings([
        TextInput(id="bedrock_api_key", label="Bedrock API Key", initial=default_api_key,
                  placeholder="bedrock-api-key-...", description="Generate from Bedrock console > API keys"),
        Select(id="aws_region", label="AWS Region", values=AWS_REGIONS, initial_value=default_region,
               description="Must match the region where you generated the API key"),
        Select(id="bedrock_model", label="Bedrock Model", values=list(BEDROCK_MODELS.keys()),
               initial_value="Amazon Nova Pro", description="Select the foundation model to use"),
        Slider(id="temperature", label="Temperature", initial=0.7, min=0.0, max=1.0, step=0.1,
               description="Controls randomness in responses"),
        Slider(id="max_tokens", label="Max Tokens", initial=4096, min=256, max=32768, step=256,
               description="Maximum response length"),
    ])
    await settings.send()

    model_id = BEDROCK_MODELS["Amazon Nova Pro"]
    chat_model = get_chat_model(model_id, default_region, 0.7, 4096, default_api_key)
    cl.user_session.set("chat_model", chat_model)

    await cl.Message(
        content=(
            "![Qlik](/public/qlik-logo.png)\n\n"
            "## Your Friendly Neighborhood AI Assistant\n\n"
            "1. Click the **plug icon** to connect to Qlik Cloud\n"
            "2. Click the **gear icon** to configure AWS Bedrock\n\n"
            f"[Qlik MCP setup guide]({QLIK_MCP_HELP_URL})"
        )
    ).send()

    # Start background poller for OAuth completion
    asyncio.create_task(poll_for_oauth_and_connect())


@cl.on_settings_update
async def on_settings_update(settings: dict):
    access_key = (settings.get("aws_access_key_id") or "").strip()
    api_key = (settings.get("bedrock_api_key") or "").strip()
    model_name = settings.get("bedrock_model") or "Amazon Nova Pro"
    model_id = BEDROCK_MODELS[model_name]
    region = settings.get("aws_region") or "us-east-1"
    temperature = settings.get("temperature") or 0.7
    max_tokens = int(settings.get("max_tokens") or 4096)

    chat_model = get_chat_model(model_id, region, temperature, max_tokens, api_key)
    cl.user_session.set("chat_model", chat_model)
    build_agent_if_ready()

    await cl.Message(content=f"Settings updated: **{model_name}** in **{region}**").send()


@cl.on_message
async def on_message(message: cl.Message):
    agent = cast(CompiledStateGraph | None, cl.user_session.get("agent"))

    # If no agent (no MCP), use the LLM directly
    if not agent:
        chat_model = cl.user_session.get("chat_model")
        if not chat_model:
            await cl.Message(content="No LLM configured. Check your AWS Bedrock settings.").send()
            return
        try:
            from langchain_core.messages import HumanMessage
            resp = await chat_model.ainvoke([HumanMessage(content=message.content)])
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            text += "\n\n---\n*No Qlik MCP connected. Click the **plug icon** to access your Qlik data.*"
            await cl.Message(content=text).send()
        except Exception as e:
            await cl.Message(content=f"LLM Error: {str(e)}").send()
            logger.error(tb.format_exc())
        return

    config = RunnableConfig(configurable={"thread_id": cl.context.session.id})

    try:
        response_message = cl.Message(content="")
        async for msg, metadata in agent.astream(
            {"messages": message.content}, stream_mode="messages", config=config,
        ):
            if isinstance(msg, AIMessageChunk) and msg.content:
                if isinstance(msg.content, str):
                    await response_message.stream_token(msg.content)
                elif (isinstance(msg.content, list) and len(msg.content) > 0
                      and isinstance(msg.content[0], dict) and msg.content[0].get("type") == "text"):
                    await response_message.stream_token(msg.content[0]["text"])
        await response_message.send()

    except Exception as e:
        error_str = str(e).lower()
        if any(kw in error_str for kw in ["timeout", "closed", "connection", "eof", "reset"]):
            cl.user_session.set("agent", None)
            # Try auto-reconnect with saved token
            access_token = cl.user_session.get("qlik_access_token")
            tenant_url = cl.user_session.get("qlik_tenant_url")
            if access_token and tenant_url:
                try:
                    await disconnect_qlik_mcp()
                    mcp_client, tools = await connect_qlik_mcp(tenant_url, access_token)
                    cl.user_session.set("mcp_client", mcp_client)
                    cl.user_session.set("mcp_tools", tools)
                    build_agent_if_ready()
                    await cl.Message(content="Connection restored. Please resend your message.").send()
                    return
                except Exception:
                    pass
            actions = [cl.Action(name="reconnect_qlik", label="Reconnect to Qlik", description="Re-establish connection", payload={})]
            await cl.Message(content="Connection lost.", actions=actions).send()
        else:
            await cl.Message(content=f"Error: {str(e)}").send()
            logger.error(tb.format_exc())
