# Qlik AI Assistant — AWS Bedrock + MCP Chat Interface

A web-based chat application that connects **Qlik Cloud** data to **Anthropic Claude** foundation models via **AWS Bedrock**, using the **Model Context Protocol (MCP)** for tool-based data access. Built with [Chainlit](https://docs.chainlit.io) for the UI, [LangGraph](https://langchain-ai.github.io/langgraph/) for agentic reasoning, and packaged for Docker deployment.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Browser    │────▶│   Chainlit UI    │────▶│  AWS Bedrock    │
│   (User)     │◀────│   (Python)       │◀────│  (Claude LLM)   │
└─────────────┘     └────────┬─────────┘     └─────────────────┘
                             │
                             │ MCP Protocol
                             ▼
                    ┌──────────────────┐
                    │  Qlik MCP Server │
                    │  (Tools/Data)    │
                    └──────────────────┘
```

**How it works:**

1. User sends a question through the Chainlit web interface
2. The question is routed to a **LangGraph ReAct agent** backed by a Claude model on AWS Bedrock
3. The agent reasons about what data it needs and calls **Qlik MCP tools** to query, filter, and analyze data from Qlik Cloud applications
4. Results are streamed back to the user in real time

## Features

- **In-browser configuration** — Settings panel (gear icon) to select Bedrock model, AWS region, temperature, and max tokens at runtime without restarting the server
- **Plug-and-play MCP** — Chainlit's built-in MCP UI (plug icon) lets you connect to any MCP server, including Qlik's native and community servers
- **Streaming responses** — Token-by-token streaming for a responsive chat experience
- **Multiple Claude models** — Switch between Claude 4 Sonnet, 3.7 Sonnet, 3.5 Haiku, and 3.5 Sonnet v2
- **Multi-region support** — Connect to Bedrock in us-east-1, us-west-2, eu-west-1, ap-southeast-1, or ap-northeast-1
- **Docker-ready** — Single-command deployment with Docker Compose
- **ReAct agent** — LangGraph's ReAct pattern for multi-step reasoning and tool orchestration

## Prerequisites

- **AWS Account** with [Amazon Bedrock](https://aws.amazon.com/bedrock/) access enabled
- **Bedrock model access** — Request access to Anthropic Claude models in the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/modelaccess)
- **Qlik Cloud tenant** with either:
  - Native MCP enabled (tenant admin must enable it), or
  - An API key for the community MCP server
- **Python 3.12+** (for local development) or **Docker** (for containerized deployment)

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Clone the repo
git clone https://github.com/robertschoenfeldqlik/aws_qlik_mcp_chaintit_chat.git
cd aws_qlik_mcp_chaintit_chat

# Configure environment
cp .env.example .env
# Edit .env with your AWS credentials and Qlik configuration

# Build and run
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### Option 2: Local Python

```bash
# Clone the repo
git clone https://github.com/robertschoenfeldqlik/aws_qlik_mcp_chaintit_chat.git
cd aws_qlik_mcp_chaintit_chat

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your AWS credentials and Qlik configuration

# Run
chainlit run app.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and fill in your values:

| Variable | Required | Description |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | Yes | AWS access key with Bedrock permissions |
| `AWS_SECRET_ACCESS_KEY` | Yes | AWS secret key |
| `AWS_DEFAULT_REGION` | No | Default AWS region (default: `us-west-2`) |
| `QLIK_TENANT_URL` | No* | Qlik Cloud tenant URL (for community MCP server) |
| `QLIK_API_KEY` | No* | Qlik API key (for community MCP server) |
| `QLIK_APP_ID` | No* | Qlik app ID to connect to |
| `LOG_LEVEL` | No | Logging level: DEBUG, INFO, WARNING, ERROR (default: `INFO`) |
| `CHAINLIT_PORT` | No | Port to run the app on (default: `8000`) |

*Required only if using the community `@agentsbazaar/mcp` package via npx instead of Qlik's native MCP.

### In-App Settings (Gear Icon)

Once the app is running, click the **gear icon** in the chat header to configure:

| Setting | Options | Default |
|---|---|---|
| **Bedrock Model** | Claude 4 Sonnet, Claude 3.7 Sonnet, Claude 3.5 Haiku, Claude 3.5 Sonnet v2 | Claude 3.7 Sonnet |
| **AWS Region** | us-east-1, us-west-2, eu-west-1, ap-southeast-1, ap-northeast-1 | us-west-2 |
| **Temperature** | 0.0 - 1.0 | 0.7 |
| **Max Tokens** | 256 - 32,768 | 4,096 |

Changes take effect immediately — no restart required. If an MCP server is already connected, the agent is automatically rebuilt with the new model settings.

## Connecting to Qlik MCP

Click the **plug icon** in the chat header to open the MCP connection dialog. You have two options:

### Option A: Qlik Cloud Native MCP (OAuth)

This uses Qlik's built-in remote MCP server with OAuth authentication.

- **URL:** `https://your-tenant.us.qlikcloud.com/api/ai/mcp`
- **Transport:** SSE (Server-Sent Events)
- Your tenant admin must enable MCP and set up OAuth first. See [Qlik MCP documentation](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm).

### Option B: Community MCP Server (API Key)

This uses the `@agentsbazaar/mcp` npm package, which connects via Qlik REST APIs.

- **Transport:** stdio
- **Command:** `npx`
- **Args:** `-y @agentsbazaar/mcp`
- Requires `QLIK_TENANT_URL` and `QLIK_API_KEY` environment variables to be set.

> **Note:** The Docker image includes Node.js 20 so npx-based MCP servers work out of the box.

## AWS Bedrock Setup

### IAM Permissions

Your AWS credentials need the following permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.*"
        }
    ]
}
```

### Enabling Model Access

1. Go to the [Amazon Bedrock console](https://console.aws.amazon.com/bedrock/home)
2. Navigate to **Model access** in the left sidebar
3. Click **Manage model access**
4. Enable the Anthropic Claude models you want to use
5. Wait for access to be granted (usually immediate)

### Cross-Region Inference

The app uses [cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html) by default (model IDs are prefixed with `us.`). This routes requests to the nearest available region for better availability. If you need to disable this, modify the `get_chat_model()` function in `app.py`.

## Project Structure

```
.
├── app.py                   # Main Chainlit application
│                            #   - Settings panel (model, region, temperature, tokens)
│                            #   - MCP connect/disconnect handlers
│                            #   - LangGraph ReAct agent with streaming
├── requirements.txt         # Python dependencies
├── Dockerfile               # Container build (Python 3.12 + Node.js 20)
├── docker-compose.yml       # One-command deployment
├── .env.example             # Environment variable template
├── .chainlit/config.toml    # Chainlit UI configuration
├── chainlit.md              # Welcome page content shown in chat
└── .gitignore
```

## Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| **Chat UI** | [Chainlit](https://docs.chainlit.io) 2.4+ | Web interface with built-in MCP and settings support |
| **LLM** | [AWS Bedrock](https://aws.amazon.com/bedrock/) | Managed access to Anthropic Claude models |
| **Agent Framework** | [LangGraph](https://langchain-ai.github.io/langgraph/) | ReAct agent for reasoning and tool orchestration |
| **MCP Bridge** | [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) | Converts MCP tools into LangChain-compatible tools |
| **AWS SDK** | [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/) + [langchain-aws](https://python.langchain.com/docs/integrations/llms/bedrock/) | Bedrock client with adaptive retries |
| **Data Source** | [Qlik Cloud MCP](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm) | Tool-based access to Qlik applications and data |

## Troubleshooting

### "Error: Chat model not initialized"
AWS credentials are missing or invalid. Check your `.env` file and verify the `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are correct.

### "No MCP server connected yet"
Click the plug icon in the header to connect to your Qlik MCP server before asking questions.

### Model access denied / AccessDeniedException
You haven't enabled the selected model in the Bedrock console. Go to **Model access** in the Bedrock console and enable the Anthropic Claude models.

### MCP connection fails with community server
Ensure `QLIK_TENANT_URL` and `QLIK_API_KEY` are set in your `.env` file. The tenant URL should include the protocol (e.g., `https://your-tenant.us.qlikcloud.com`).

### Slow responses
- Try a smaller model (Claude 3.5 Haiku is the fastest)
- Reduce `max_tokens` in the settings panel
- Ensure your AWS region is geographically close to you
- Cross-region inference helps with availability but may add latency

### Docker: npx MCP server not found
The Docker image includes Node.js 20. If you're running locally without Docker, install Node.js 18+ separately.

## Development

To modify the app:

```bash
# Run in development mode with auto-reload
chainlit run app.py --watch

# Set debug logging
LOG_LEVEL=DEBUG chainlit run app.py
```

### Adding New Models

Edit the `BEDROCK_MODELS` dictionary in `app.py`:

```python
BEDROCK_MODELS = {
    "Claude 4 Sonnet": "anthropic.claude-sonnet-4-20250514",
    "Claude 3.7 Sonnet": "anthropic.claude-3-7-sonnet-20250219-v1:0",
    # Add new models here:
    "Your Model": "model-id-from-bedrock",
}
```

### Adding New Regions

Edit the `AWS_REGIONS` list in `app.py`:

```python
AWS_REGIONS = [
    "us-east-1",
    "us-west-2",
    # Add new regions here
]
```

### Customizing the System Prompt

Edit the `SYSTEM_PROMPT` constant in `app.py` to change how the agent behaves when interacting with Qlik data.

## License

See [LICENSE](LICENSE) for details.
