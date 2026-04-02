"""Chainlit chat app with AWS Bedrock LLM and Qlik MCP integration.

Plug icon → Qlik Cloud form → OAuth PKCE → streamable-http MCP connection.
Gear icon → AWS Bedrock settings.
"""

import os
import sys
import traceback as tb

import chainlit as cl
from chainlit.input_widget import Select, Slider, TextInput
from chainlit.server import app as fastapi_app
from langchain_aws.chat_models import ChatBedrockConverse
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from loguru import logger

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from typing import cast

from qlik_oauth import register_oauth_routes, pending_connections

load_dotenv()

logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

# Register OAuth routes
register_oauth_routes(fastapi_app)

BEDROCK_MODELS = {
    "Claude 4 Sonnet": "anthropic.claude-sonnet-4-20250514-v1:0",
    "Amazon Nova Pro": "amazon.nova-pro-v1:0",
    "Meta Llama 3.3 70B": "meta.llama3-3-70b-instruct-v1:0",
}

AWS_REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1", "ap-northeast-1"]

SYSTEM_PROMPT = """You are a Qlik Cloud data analyst assistant. Use the available tools to answer questions about the user's Qlik Cloud tenant. Always call tools — never guess or make up data.

QUESTION ROUTING — match the user's question to the right tool:

FINDING THINGS:
- "What apps/datasets/spaces/data products do I have?" → qlik_search(query='*', resourceType='app|dataset|space|dataproduct')
- "Find [something]" → qlik_search(query='search term')
- "Tell me about app X" → qlik_search to find ID, then qlik_describe_app(appId=ID)

APP EXPLORATION:
- "What sheets are in app X?" → qlik_list_sheets(appId=ID)
- "What fields are available?" → qlik_get_fields(appId=ID)
- "What dimensions/measures exist?" → qlik_list_dimensions(appId=ID) / qlik_list_measures(appId=ID)
- "Show me chart data" → qlik_get_chart_data(appId=ID, chartId=ID)

DATA ANALYSIS:
- "What values are in field X?" → qlik_get_field_values(appId=ID, fieldName='X')
- "Filter by X=Y" → qlik_select_values(appId=ID, selections=[{field:'X', values:['Y']}])
- "Clear filters" → qlik_clear_selections(appId=ID)
- "Calculate/aggregate something" → qlik_create_data_object with dimensions and measures

DATASETS:
- "Show me dataset schema/columns" → qlik_get_dataset_schema(datasetId=ID)
- "Preview dataset" → qlik_get_dataset_sample(datasetId=ID)
- "Data quality/trust score" → qlik_get_dataset_trust_score(datasetId=ID)
- "When was it last updated?" → qlik_get_dataset_freshness(datasetId=ID)
- "Data lineage" → qlik_get_lineage(qri=QRI)

DATA PRODUCTS:
- "List data products" → qlik_search(query='*', resourceType='dataproduct')
- "Data product details" → qlik_get_data_product(dataProductId=ID)
- "Create data product" → qlik_create_data_product(name='...')

GLOSSARY:
- "Search glossary terms" → qlik_search_glossary_terms(glossaryId=ID)
- "Create glossary term" → qlik_create_glossary_term(glossaryId=ID, name='...')

BUILDING VISUALIZATIONS:
- "Create a sheet" → qlik_create_sheet(appId=ID, title='...')
- "Add a bar/line/pie chart" → qlik_add_chart(appId=ID, sheetId=ID, chartType='barchart', ...)
- "Add a filter" → qlik_add_filter(appId=ID, sheetId=ID, ...)

RULES:
- Always call qlik_search FIRST to find IDs before using other tools
- The query parameter is REQUIRED for qlik_search — use '*' to match everything
- Present results clearly with counts, tables, and bullet points
- If a tool returns empty results, say so clearly
- Chain multiple tool calls when needed (search → describe → list sheets)"""

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
# Qlik MCP Connection (streamable-http + OAuth Bearer token)
# ---------------------------------------------------------------------------

async def connect_qlik_mcp(tenant_url: str, access_token: str, client_id: str):
    """Connect to Qlik MCP using streamable-http with Bearer token + X-Agent-Id."""
    mcp_url = f"{tenant_url.rstrip('/')}/api/ai/mcp"
    logger.info(f"Connecting to MCP: {mcp_url}")

    mcp_client = MultiServerMCPClient({
        "qlik": {
            "url": mcp_url,
            "transport": "streamable_http",
            "headers": {
                "Authorization": f"Bearer {access_token}",
                "X-Agent-Id": client_id,
            },
        },
    })
    try:
        tools = await mcp_client.get_tools()
    except ExceptionGroup as eg:
        msgs = [f"{type(e).__name__}: {e}" for e in eg.exceptions]
        raise RuntimeError("; ".join(msgs)) from eg
    logger.info(f"MCP connected with {len(tools)} tools")
    return mcp_client, tools


async def disconnect_qlik_mcp():
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
               initial_value="Claude 4 Sonnet", description="Select the foundation model to use"),
        Slider(id="temperature", label="Temperature", initial=0.2, min=0.0, max=1.0, step=0.1,
               description="Controls randomness in responses"),
        Slider(id="max_tokens", label="Max Tokens", initial=4096, min=256, max=32768, step=256,
               description="Maximum response length"),
    ])
    await settings.send()

    model_id = BEDROCK_MODELS["Claude 4 Sonnet"]
    chat_model = get_chat_model(model_id, default_region, 0.2, 4096, default_api_key)
    cl.user_session.set("chat_model", chat_model)

    await cl.Message(
        content=(
            "![Qlik](/public/qlik-logo.png)\n\n"
            "## Your Friendly Neighborhood AI Assistant\n\n"
            "Ask me anything — or connect to Qlik Cloud for data access."
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    api_key = (settings.get("bedrock_api_key") or "").strip()
    model_name = settings.get("bedrock_model") or "Claude 4 Sonnet"
    model_id = BEDROCK_MODELS[model_name]
    region = settings.get("aws_region") or "us-east-1"
    temperature = settings.get("temperature") or 0.2
    max_tokens = int(settings.get("max_tokens") or 4096)

    chat_model = get_chat_model(model_id, region, temperature, max_tokens, api_key)
    cl.user_session.set("chat_model", chat_model)
    build_agent_if_ready()
    await cl.Message(content=f"Settings updated: **{model_name}** in **{region}**").send()


@cl.on_message
async def on_message(message: cl.Message):
    # Check for pending MCP connection from OAuth flow
    pending = pending_connections.pop("default", None)
    if pending:
        token = pending["access_token"]
        tenant = pending["tenant_url"]
        cid = pending["client_id"]
        cl.user_session.set("qlik_access_token", token)
        cl.user_session.set("qlik_tenant_url", tenant)
        cl.user_session.set("qlik_client_id", cid)
        try:
            await disconnect_qlik_mcp()
            mcp_client, tools = await connect_qlik_mcp(tenant, token, cid)
            cl.user_session.set("mcp_client", mcp_client)
            cl.user_session.set("mcp_tools", tools)
            build_agent_if_ready()
            await cl.Message(
                content=f"Connected to Qlik MCP — **{len(tools)} tools** available. Ask me anything!"
            ).send()
        except Exception as e:
            logger.error(f"MCP connection failed: {e}")
            await cl.Message(content=f"Qlik MCP connection failed:\n```\n{e}\n```").send()

    agent = cast(CompiledStateGraph | None, cl.user_session.get("agent"))

    # No agent — use LLM directly
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

    # Agent with MCP tools
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
