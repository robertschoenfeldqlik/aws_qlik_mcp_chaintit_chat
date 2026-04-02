"""Generate training data for fine-tuning Qwen3:8b on Qlik MCP tool calling.

Uses Claude Sonnet 4 on Bedrock + Qlik MCP (streamable-http) as the teacher.
Captures full tool-calling traces and formats them for Qwen3 fine-tuning.

Usage:
    # Generate from all questions (needs active Qlik MCP connection):
    python training/generate_training_data.py

    # Generate from first N questions only:
    python training/generate_training_data.py --limit 10

    # Dry run — just show questions without calling MCP:
    python training/generate_training_data.py --dry-run

Prerequisites:
    - AWS_BEARER_TOKEN_BEDROCK env var set
    - Qlik MCP OAuth token (run the app first, connect, then copy the token)
    - pip install langchain-aws langchain-mcp-adapters langgraph httpx
"""

import asyncio
import json
import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import boto3
from botocore.config import Config
from langchain_aws.chat_models import ChatBedrockConverse
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent


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

DATA ANALYSIS:
- "What values are in field X?" → qlik_get_field_values(appId=ID, fieldName='X')
- "Filter by X=Y" → qlik_select_values(appId=ID, selections=[{field:'X', values:['Y']}])
- "Clear filters" → qlik_clear_selections(appId=ID)
- "Calculate/aggregate something" → qlik_create_data_object with dimensions and measures

DATASETS:
- "Show me dataset schema/columns" → qlik_get_dataset_schema(datasetId=ID)
- "Preview dataset" → qlik_get_dataset_sample(datasetId=ID)
- "Data quality/trust score" → qlik_get_dataset_trust_score(datasetId=ID)

RULES:
- Always call qlik_search FIRST to find IDs before using other tools
- The query parameter is REQUIRED for qlik_search — use '*' to match everything
- Present results clearly with counts, tables, and bullet points
- Chain multiple tool calls when needed (search → describe → list sheets)"""


def get_model():
    """Create Claude Sonnet 4 model via Bedrock."""
    api_key = os.getenv("AWS_BEARER_TOKEN_BEDROCK", "")
    if api_key:
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key

    client = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}, read_timeout=120),
    )
    return ChatBedrockConverse(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        client=client,
        temperature=0.2,
        max_tokens=4096,
    )


async def connect_mcp():
    """Connect to Qlik MCP using saved OAuth token."""
    tenant_url = os.getenv("QLIK_TENANT_URL", "")
    client_id = os.getenv("QLIK_OAUTH_CLIENT_ID", "")
    access_token = os.getenv("QLIK_ACCESS_TOKEN", "")

    if not all([tenant_url, access_token]):
        print("ERROR: Set QLIK_TENANT_URL and QLIK_ACCESS_TOKEN in .env")
        print("To get the access token, connect via the app's plug icon first.")
        sys.exit(1)

    mcp_url = f"{tenant_url.rstrip('/')}/api/ai/mcp"
    print(f"Connecting to MCP: {mcp_url}")

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
    tools = await mcp_client.get_tools()
    print(f"Connected — {len(tools)} tools available")
    return mcp_client, tools


def extract_trace(messages):
    """Extract tool-calling trace from LangGraph message history.

    Returns a list of message dicts in Qwen3 training format.
    """
    trace = [{"role": "system", "content": SYSTEM_PROMPT}]

    for msg in messages:
        if isinstance(msg, HumanMessage):
            trace.append({"role": "user", "content": msg.content})

        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                # Tool call message
                tool_calls = []
                for tc in msg.tool_calls:
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"]),
                        },
                    })
                trace.append({
                    "role": "assistant",
                    "content": msg.content if msg.content else None,
                    "tool_calls": tool_calls,
                })
            else:
                # Final response
                content = msg.content
                if isinstance(content, list):
                    content = " ".join(
                        c["text"] for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                if content:
                    trace.append({"role": "assistant", "content": content})

        elif isinstance(msg, ToolMessage):
            content = msg.content
            # Truncate very long tool results to save tokens during training
            if isinstance(content, str) and len(content) > 2000:
                content = content[:2000] + "... [truncated]"
            trace.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "name": msg.name,
                "content": content,
            })

    return trace


async def generate_example(agent, question, category, expected_tools):
    """Run a single question through the agent and capture the trace."""
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": f"training-{time.time()}"}},
        )

        messages = result["messages"]
        trace = extract_trace(messages)

        # Collect which tools were actually called
        tools_called = []
        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                tools_called.extend([tc["name"] for tc in msg.tool_calls])

        return {
            "messages": trace,
            "metadata": {
                "question": question,
                "category": category,
                "expected_tools": expected_tools,
                "actual_tools": tools_called,
                "success": len(tools_called) > 0,
                "timestamp": datetime.now().isoformat(),
            },
        }

    except Exception as e:
        print(f"  ERROR: {e}")
        return {
            "messages": [],
            "metadata": {
                "question": question,
                "category": category,
                "expected_tools": expected_tools,
                "actual_tools": [],
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            },
        }


async def main():
    parser = argparse.ArgumentParser(description="Generate Qlik MCP training data")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N questions (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Show questions without running")
    parser.add_argument("--output", default="training/training_data.jsonl", help="Output file")
    args = parser.parse_args()

    # Load questions
    questions_file = Path(__file__).parent / "questions.json"
    with open(questions_file) as f:
        data = json.load(f)

    questions = data["questions"]
    if args.limit > 0:
        questions = questions[:args.limit]

    print(f"Loaded {len(questions)} questions")

    if args.dry_run:
        for i, q in enumerate(questions):
            print(f"  [{i+1}] [{q['category']}] {q['q']}")
        return

    # Connect to MCP and create agent
    model = get_model()
    mcp_client, tools = await connect_mcp()
    agent = create_react_agent(model, tools, prompt=SYSTEM_PROMPT)

    # Generate training data
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    success_count = 0
    fail_count = 0

    for i, q in enumerate(questions):
        print(f"[{i+1}/{len(questions)}] {q['q']}")

        example = await generate_example(
            agent, q["q"], q["category"], q["expected_tools"]
        )

        if example["metadata"]["success"]:
            success_count += 1
            tools_used = ", ".join(example["metadata"]["actual_tools"])
            print(f"  OK — tools: {tools_used}")
        else:
            fail_count += 1
            print(f"  FAILED")

        results.append(example)

        # Save incrementally
        with open(output_path, "w") as f:
            for r in results:
                if r["metadata"]["success"] and r["messages"]:
                    f.write(json.dumps({"messages": r["messages"]}) + "\n")

        # Rate limit to avoid overwhelming the API
        await asyncio.sleep(1)

    # Save full results with metadata
    meta_path = output_path.with_suffix(".meta.json")
    with open(meta_path, "w") as f:
        json.dump({
            "total": len(results),
            "success": success_count,
            "failed": fail_count,
            "timestamp": datetime.now().isoformat(),
            "results": results,
        }, f, indent=2)

    print(f"\nDone! {success_count}/{len(results)} successful")
    print(f"Training data: {output_path}")
    print(f"Full metadata: {meta_path}")


if __name__ == "__main__":
    asyncio.run(main())
