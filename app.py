"""Chainlit chat app with AWS Bedrock LLM and Qlik MCP integration.

Plug icon → Qlik Cloud SSE connection (MCP protocol handles OAuth).
Gear icon → AWS Bedrock settings.
"""

import os
import sys
import traceback as tb

import chainlit as cl
from chainlit.input_widget import Select, Slider, TextInput
from langchain_aws.chat_models import ChatBedrockConverse
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from loguru import logger
from mcp import ClientSession

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from typing import cast

load_dotenv()

# Register defaults endpoint — insert before Chainlit's catch-all route
from chainlit.server import app as fastapi_app
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.routing import Route

_qlik_router = APIRouter()

@_qlik_router.get("/auth/qlik/defaults")
async def qlik_defaults(request: Request):
    tenant_url = os.getenv("QLIK_TENANT_URL", "")
    client_id = os.getenv("QLIK_OAUTH_CLIENT_ID", "")
    return JSONResponse({"tenant_url": tenant_url, "client_id": client_id})

# Insert routes at position 0 so they run before Chainlit's catch-all
for route in reversed(_qlik_router.routes):
    fastapi_app.routes.insert(0, route)

logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

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
    if api_key:
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key
    client = boto3.client(
        "bedrock-runtime", region_name=region,
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}, read_timeout=60),
    )
    return ChatBedrockConverse(model=f"us.{model_id}", client=client, temperature=temperature, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def build_agent_if_ready():
    chat_model = cl.user_session.get("chat_model")
    tools = cl.user_session.get("mcp_tools")
    if chat_model and tools:
        agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)
        cl.user_session.set("agent", agent)
        return agent
    return None


# ---------------------------------------------------------------------------
# MCP Connect/Disconnect — Chainlit's native plug icon handles SSE + OAuth
# ---------------------------------------------------------------------------

@cl.on_mcp_connect
async def on_mcp_connect(connection, session: ClientSession):
    """Called when Chainlit's MCP dialog connects to an SSE server."""
    await session.initialize()
    tools = await load_mcp_tools(session)

    chat_model = cl.user_session.get("chat_model")
    agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)

    cl.user_session.set("agent", agent)
    cl.user_session.set("mcp_session", session)
    cl.user_session.set("mcp_tools", tools)

    tool_names = [t.name for t in tools]
    await cl.Message(
        content=f"Connected to Qlik MCP! **{len(tools)} tools** available:\n"
        + "\n".join(f"- `{name}`" for name in tool_names)
    ).send()


@cl.on_mcp_disconnect
async def on_mcp_disconnect(name: str, session: ClientSession):
    try:
        await session.__aexit__(None, None, None)
    except Exception:
        pass
    cl.user_session.set("mcp_session", None)
    cl.user_session.set("mcp_tools", None)
    cl.user_session.set("agent", None)
    logger.info(f"Disconnected from MCP server: {name}")


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

    tenant_url = os.getenv("QLIK_TENANT_URL", "your-tenant.us.qlikcloud.com")
    mcp_url = f"{tenant_url.rstrip('/')}/api/ai/mcp"

    await cl.Message(
        content=(
            "![Qlik](/public/qlik-logo.png)\n\n"
            "## Your Friendly Neighborhood AI Assistant\n\n"
            f"1. Click the **plug icon** → select **SSE** → enter URL: `{mcp_url}`\n"
            "2. Enter your **OAuth Client ID** (no secret needed)\n"
            "3. Click **Confirm** → sign in to Qlik Cloud → click **Approve**\n"
            "4. Click the **gear icon** to configure AWS Bedrock\n\n"
            f"[Qlik MCP setup guide]({QLIK_MCP_HELP_URL})"
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
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
            resp = await chat_model.ainvoke([HumanMessage(content=message.content)])
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            text += "\n\n---\n*No Qlik MCP connected. Click the **plug icon** to connect.*"
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
            await cl.Message(content="Connection lost. Click the **plug icon** to reconnect.").send()
        else:
            await cl.Message(content=f"Error: {str(e)}").send()
            logger.error(tb.format_exc())
