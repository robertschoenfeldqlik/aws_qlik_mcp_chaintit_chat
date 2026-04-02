"""Chainlit chat app with AWS Bedrock LLM and Qlik MCP integration."""

import os
import sys
import traceback as tb

import chainlit as cl
from chainlit.input_widget import Select, Slider, TextInput
from langchain_aws.chat_models import ChatBedrockConverse
from langchain_core.messages import AIMessageChunk
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


def get_bedrock_client(region: str) -> boto3.client:
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
    # Use cross-region inference prefix
    full_model_id = f"us.{model_id}"

    return ChatBedrockConverse(
        model=full_model_id,
        client=client,
        temperature=temperature,
        max_tokens=max_tokens,
    )


@cl.on_chat_start
async def on_chat_start():
    """Initialize chat session with settings panel."""
    default_region = os.getenv("AWS_DEFAULT_REGION", "us-west-2")

    settings = cl.ChatSettings(
        [
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

    # Initialize with defaults
    model_id = BEDROCK_MODELS["Claude 3.7 Sonnet"]
    chat_model = get_chat_model(model_id, default_region, 0.7, 4096)
    cl.user_session.set("chat_model", chat_model)
    cl.user_session.set("chat_messages", [])

    await cl.Message(
        content=(
            "**Qlik AI Assistant** ready.\n\n"
            "- Use the **Settings** (gear icon) to configure the Bedrock model and region.\n"
            "- Use the **MCP** (plug icon) to connect to your Qlik MCP server.\n\n"
            "Once connected to Qlik MCP, you can ask questions about your data."
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    """Rebuild the chat model when settings change."""
    model_name = settings.get("bedrock_model", "Claude 3.7 Sonnet")
    model_id = BEDROCK_MODELS[model_name]
    region = settings.get("aws_region", "us-west-2")
    temperature = settings.get("temperature", 0.7)
    max_tokens = int(settings.get("max_tokens", 4096))

    chat_model = get_chat_model(model_id, region, temperature, max_tokens)
    cl.user_session.set("chat_model", chat_model)

    # If MCP tools are already loaded, rebuild the agent
    tools = cl.user_session.get("mcp_tools")
    if tools:
        agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)
        cl.user_session.set("agent", agent)

    await cl.Message(
        content=f"Settings updated: **{model_name}** in **{region}** (temp={temperature}, max_tokens={max_tokens})"
    ).send()


@cl.on_mcp_connect
async def on_mcp_connect(connection, session: ClientSession):
    """Handle MCP server connection — works with any MCP server including Qlik."""
    await session.initialize()
    tools = await load_mcp_tools(session)

    chat_model = cl.user_session.get("chat_model")
    agent = create_react_agent(chat_model, tools, prompt=SYSTEM_PROMPT)

    cl.user_session.set("agent", agent)
    cl.user_session.set("mcp_session", session)
    cl.user_session.set("mcp_tools", tools)

    tool_names = [t.name for t in tools]
    await cl.Message(
        content=f"Connected to MCP server. **{len(tools)} tools** available:\n"
        + "\n".join(f"- `{name}`" for name in tool_names)
    ).send()


@cl.on_mcp_disconnect
async def on_mcp_disconnect(name: str, session: ClientSession):
    """Clean up MCP session on disconnect."""
    if isinstance(cl.user_session.get("mcp_session"), ClientSession):
        await session.__aexit__(None, None, None)
        cl.user_session.set("mcp_session", None)
        cl.user_session.set("mcp_tools", None)
        cl.user_session.set("agent", None)
        logger.info(f"Disconnected from MCP server: {name}")


@cl.on_message
async def on_message(message: cl.Message):
    """Process user messages through the agent."""
    agent = cast(CompiledStateGraph | None, cl.user_session.get("agent"))

    if not agent:
        await cl.Message(
            content=(
                "No MCP server connected yet. "
                "Click the **plug icon** in the header to connect to your Qlik MCP server."
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
        await cl.Message(content=f"Error: {str(e)}").send()
        logger.error(tb.format_exc())
