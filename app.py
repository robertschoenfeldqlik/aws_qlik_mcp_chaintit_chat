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

from qlik_oauth import register_oauth_routes

load_dotenv()

logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

# Register OAuth routes on Chainlit's FastAPI app
register_oauth_routes(fastapi_app)

BEDROCK_MODELS = {
    "Claude 4 Sonnet": "anthropic.claude-sonnet-4-20250514",
    "Claude 3.7 Sonnet": "anthropic.claude-3-7-sonnet-20250219-v1:0",
    "Claude 3.5 Haiku": "anthropic.claude-3-5-haiku-20241022-v1:0",
    "Claude 3.5 Sonnet v2": "anthropic.claude-3-5-sonnet-20241022-v2:0",
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

def get_chat_model(model_id, region, temperature, max_tokens, access_key="", secret_key=""):
    kwargs = {
        "service_name": "bedrock-runtime", "region_name": region,
        "config": Config(retries={"max_attempts": 5, "mode": "adaptive"}, read_timeout=60),
    }
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    client = boto3.client(**kwargs)
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
    """Poll for OAuth token, then connect to MCP. Called from on_chat_start."""
    for _ in range(90):  # 3 minutes max
        await asyncio.sleep(2)
        if cl.user_session.get("oauth_complete"):
            token = cl.user_session.get("qlik_access_token")
            tenant_url = cl.user_session.get("qlik_tenant_url")
            if token and tenant_url:
                try:
                    await disconnect_qlik_mcp()
                    mcp_client, tools = await connect_qlik_mcp(tenant_url, token)
                    cl.user_session.set("mcp_client", mcp_client)
                    cl.user_session.set("mcp_tools", tools)
                    build_agent_if_ready()
                    tool_names = [t.name for t in tools]
                    await cl.Message(
                        content=f"Authenticated! Connected to Qlik MCP with **{len(tools)} tools**:\n"
                        + "\n".join(f"- `{n}`" for n in tool_names)
                    ).send()
                except Exception as e:
                    await cl.Message(content=f"OAuth succeeded but MCP connection failed:\n```\n{e}\n```").send()
            cl.user_session.set("oauth_complete", False)
            return
    # Timeout — no action needed, user can retry via plug icon


# ---------------------------------------------------------------------------
# Chat Lifecycle
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_chat_start():
    default_region = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
    default_access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
    default_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")

    settings = cl.ChatSettings([
        TextInput(id="aws_access_key_id", label="AWS Access Key ID", initial=default_access_key,
                  placeholder="AKIA...", description="IAM access key with bedrock:InvokeModel permissions"),
        TextInput(id="aws_secret_access_key", label="AWS Secret Access Key", initial=default_secret_key,
                  placeholder="Your secret key", description="Stored in session only, never logged"),
        Select(id="aws_region", label="AWS Region", values=AWS_REGIONS, initial_value=default_region,
               description="AWS region for Bedrock API calls"),
        Select(id="bedrock_model", label="Bedrock Model", values=list(BEDROCK_MODELS.keys()),
               initial_value="Claude 3.7 Sonnet", description="Select the foundation model to use"),
        Slider(id="temperature", label="Temperature", initial=0.7, min=0.0, max=1.0, step=0.1,
               description="Controls randomness in responses"),
        Slider(id="max_tokens", label="Max Tokens", initial=4096, min=256, max=32768, step=256,
               description="Maximum response length"),
    ])
    await settings.send()

    model_id = BEDROCK_MODELS["Claude 3.7 Sonnet"]
    chat_model = get_chat_model(model_id, default_region, 0.7, 4096, default_access_key, default_secret_key)
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
    secret_key = (settings.get("aws_secret_access_key") or "").strip()
    model_name = settings.get("bedrock_model") or "Claude 3.7 Sonnet"
    model_id = BEDROCK_MODELS[model_name]
    region = settings.get("aws_region") or "us-west-2"
    temperature = settings.get("temperature") or 0.7
    max_tokens = int(settings.get("max_tokens") or 4096)

    chat_model = get_chat_model(model_id, region, temperature, max_tokens, access_key, secret_key)
    cl.user_session.set("chat_model", chat_model)
    build_agent_if_ready()

    await cl.Message(content=f"Settings updated: **{model_name}** in **{region}**").send()


@cl.on_message
async def on_message(message: cl.Message):
    agent = cast(CompiledStateGraph | None, cl.user_session.get("agent"))

    if not agent:
        await cl.Message(
            content="Not connected to Qlik yet. Click the **plug icon** to connect to Qlik Cloud."
        ).send()
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
            await cl.Message(content="Connection lost. Click the **plug icon** to reconnect.").send()
        else:
            await cl.Message(content=f"Error: {str(e)}").send()
            logger.error(tb.format_exc())
