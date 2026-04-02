# Qlik AI Assistant — AWS Bedrock + MCP Chat Interface

<p align="center">
  <img src="public/qlik-logo.png" alt="Qlik" width="300">
</p>

<p align="center">
  <strong>Your Friendly Neighborhood AI Assistant</strong><br>
  Chat with your Qlik Cloud data using Anthropic Claude on AWS Bedrock
</p>

---

A branded, Docker-ready web chat application that connects **Qlik Cloud** data to **Anthropic Claude** foundation models via **AWS Bedrock**. Users configure everything — Qlik credentials, AWS credentials, model selection — directly in the browser. No code changes needed to switch tenants, models, or regions.

Built with [Chainlit](https://docs.chainlit.io) for the UI, [LangGraph](https://langchain-ai.github.io/langgraph/) for agentic reasoning, [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) for MCP bridging, and the [Model Context Protocol](https://modelcontextprotocol.io/) for tool-based data access.

## Architecture

```
┌─────────────────┐     ┌───────────────────────┐     ┌──────────────────┐
│    Browser       │────▶│    Chainlit UI         │────▶│   AWS Bedrock    │
│    (User)        │◀────│    (Python + Qlik CSS) │◀────│   (Claude LLM)   │
└─────────────────┘     └──────────┬────────────┘     └──────────────────┘
                                   │
                                   │ MCP (SSE)
                                   ▼
                          ┌──────────────────┐
                          │  Qlik Cloud MCP  │
                          │  <tenant>/api/   │
                          │  ai/mcp          │
                          └──────────────────┘
```

**How it works:**

1. User opens the app and sees the Qlik-branded welcome screen with setup instructions
2. User enters Qlik OAuth credentials and AWS credentials in the **Settings** panel
3. The app connects to the Qlik MCP server via SSE and loads available tools
4. User asks questions in natural language
5. A **LangGraph ReAct agent** backed by Claude on Bedrock reasons about the question, calls Qlik MCP tools to query data, and streams the response back in real time

## Features

### Configuration — All In-Browser

| Setting | Where | Description |
|---|---|---|
| **Qlik Tenant URL** | Settings panel | Your Qlik Cloud tenant (e.g., `https://tenant.us.qlikcloud.com`) |
| **OAuth Client ID** | Settings panel | Created by your Qlik tenant admin ([setup guide](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm)) |
| **OAuth Client Secret** | Settings panel | Leave empty for most configurations |
| **AWS Access Key ID** | Settings panel | IAM key with `bedrock:InvokeModel` permissions |
| **AWS Secret Access Key** | Settings panel | IAM secret key (session-only, never logged) |
| **AWS Region** | Settings panel | us-east-1, us-west-2, eu-west-1, ap-southeast-1, ap-northeast-1 |
| **Bedrock Model** | Settings panel | Claude 4 Sonnet, 3.7 Sonnet, 3.5 Haiku, 3.5 Sonnet v2 |
| **Temperature** | Settings panel | 0.0 - 1.0 (default: 0.7) |
| **Max Tokens** | Settings panel | 256 - 32,768 (default: 4,096) |

All settings take effect immediately — no restart required. Changing the model or credentials automatically rebuilds the agent.

### Connectivity

- **Auto-connect on startup** — If environment variables are set, connects to Qlik MCP automatically
- **Reconnect button** — One-click reconnect when the connection drops
- **Auto-reconnect** — Automatically retries on timeout/connection errors during chat
- **Settings-driven reconnect** — Changing Qlik credentials triggers an automatic reconnect

### Branding

- **Qlik logo** with transparent background on the welcome screen
- **Qlik brand colors** — Green (#009845), Gray (#54565A), Navy (#194268), Teal (#006580)
- **Source Sans 3** font via Google Fonts
- **Dark mode** by default with Qlik-themed light mode
- Custom CSS for buttons, links, scrollbars, message accents, and settings panel
- Green header border and green left-border on assistant messages

### AI / Agent

- **Streaming responses** — Token-by-token output for responsive UX
- **ReAct agent** — LangGraph's reasoning + acting pattern for multi-step tool use
- **Multiple Claude models** — Switch models on the fly without restarting
- **Cross-region inference** — Model IDs prefixed with `us.` for optimal availability
- **Adaptive retries** — boto3 client configured with exponential backoff (5 retries, 60s timeout)

## Prerequisites

- **AWS Account** with [Amazon Bedrock](https://aws.amazon.com/bedrock/) access enabled
- **Bedrock model access** — Request access to Anthropic Claude models in the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/modelaccess)
- **Qlik Cloud tenant** with MCP enabled by your tenant admin
- **OAuth client** created in Qlik Cloud Administration with scopes `user_default` and `mcp:execute` ([instructions](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Administering-Qlik-MCP.htm))
- **Python 3.12+** (for local development) or **Docker** (for containerized deployment)

## Quick Start

### Option 1: Docker (Recommended)

```bash
git clone https://github.com/robertschoenfeldqlik/aws_qlik_mcp_chaintit_chat.git
cd aws_qlik_mcp_chaintit_chat

# Optional: pre-fill credentials so they auto-load on startup
cp .env.example .env
# Edit .env with your values (or skip this and enter them in the UI)

docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### Option 2: Local Python

```bash
git clone https://github.com/robertschoenfeldqlik/aws_qlik_mcp_chaintit_chat.git
cd aws_qlik_mcp_chaintit_chat

python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

pip install -r requirements.txt

# Optional: pre-fill credentials
cp .env.example .env

chainlit run app.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### First Run

1. The Qlik logo and welcome message appear
2. Click the **gear icon** (Settings) in the header
3. Enter your **Qlik Tenant URL** and **OAuth Client ID**
4. Enter your **AWS Access Key ID** and **Secret Access Key**
5. Select your preferred **Region** and **Model**
6. Click **Confirm** — the app connects to Qlik MCP and lists available tools
7. Start asking questions about your data!

## Environment Variables

All credentials can be entered in the Settings UI at runtime. Environment variables are optional — use them to pre-fill the settings panel or for headless/Docker deployments.

| Variable | Required | Description |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | No | Pre-fills AWS Access Key ID in settings |
| `AWS_SECRET_ACCESS_KEY` | No | Pre-fills AWS Secret Access Key in settings |
| `AWS_DEFAULT_REGION` | No | Default AWS region (default: `us-west-2`) |
| `QLIK_TENANT_URL` | No | Pre-fills Qlik tenant URL; triggers auto-connect if set with client ID |
| `QLIK_OAUTH_CLIENT_ID` | No | Pre-fills OAuth Client ID; triggers auto-connect if set with tenant URL |
| `QLIK_OAUTH_CLIENT_SECRET` | No | Pre-fills OAuth Client Secret |
| `LOG_LEVEL` | No | DEBUG, INFO, WARNING, ERROR (default: `INFO`) |
| `CHAINLIT_PORT` | No | Port to run on (default: `8000`) |

## Qlik MCP Setup

Your Qlik Cloud tenant admin needs to:

1. Navigate to **Administration** > **OAuth**
2. Click **Create new** and select **Native** as the client type
3. Under Scopes, select **user_default** and **mcp:execute**
4. Add a redirect URL (this app uses SSE, so a callback URL may not be required for all configurations)
5. Click **Create** and copy the **Client ID**
6. Share the Client ID with users

The MCP endpoint is automatically constructed as `<tenant URL>/api/ai/mcp`.

Full instructions: [Connecting to the Qlik MCP server](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm) | [Deploying Qlik MCP server for a tenant](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Administering-Qlik-MCP.htm)

## AWS Bedrock Setup

### IAM Permissions

The AWS credentials need these permissions:

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

The app uses [cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html) by default (model IDs are prefixed with `us.`). This routes requests to the nearest available region for better availability and resilience.

## Project Structure

```
.
├── app.py                     # Main application
│                              #   - Settings panel (Qlik + AWS + model config)
│                              #   - MCP auto-connect with reconnect handling
│                              #   - LangGraph ReAct agent with streaming
├── public/
│   ├── qlik-logo.png          # Qlik logo (transparent background)
│   ├── qlik-theme.css         # Custom CSS for Qlik branding
│   └── theme.json             # Chainlit theme (HSL color variables)
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container build (Python 3.12 + Node.js 20)
├── docker-compose.yml         # One-command deployment
├── .env.example               # Environment variable template
├── .chainlit/config.toml      # Chainlit UI configuration
├── chainlit.md                # Pre-chat splash screen
└── .gitignore
```

## Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| **Chat UI** | [Chainlit](https://docs.chainlit.io) 2.10+ | Web interface with settings panel and MCP support |
| **LLM** | [AWS Bedrock](https://aws.amazon.com/bedrock/) | Managed access to Anthropic Claude models |
| **Agent** | [LangGraph](https://langchain-ai.github.io/langgraph/) | ReAct agent for reasoning and tool orchestration |
| **MCP Bridge** | [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) 0.2+ | MultiServerMCPClient for programmatic SSE connections |
| **AWS SDK** | [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/) + [langchain-aws](https://python.langchain.com/docs/integrations/llms/bedrock/) | Bedrock client with adaptive retries |
| **Data Source** | [Qlik Cloud MCP](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm) | Tool-based access to Qlik applications and data |
| **Theming** | Custom CSS + [Shadcn](https://ui.shadcn.com/) variables | Qlik brand colors, fonts, and dark mode |

## Troubleshooting

### Settings panel not showing all fields

Chainlit's `TextInput` widget has had rendering issues in some versions. The app uses Chainlit 2.10+ which resolves most of these. If fields are missing, try a hard refresh (Ctrl+Shift+R).

### "Not connected to Qlik MCP"

Open **Settings** (gear icon) and enter your Qlik Tenant URL and OAuth Client ID. Click **Confirm**. If it fails, check:
- The tenant URL includes the protocol (`https://...`)
- The OAuth Client ID is correct (get it from your Qlik admin)
- Your tenant admin has enabled MCP for the tenant

### AccessDeniedException from Bedrock

- You haven't enabled the selected model in the Bedrock console — go to **Model access** and enable it
- Your AWS credentials don't have `bedrock:InvokeModel` permissions
- You're trying to use a model in a region where it's not available

### Connection drops during chat

The app auto-reconnects on timeout/connection errors. If auto-reconnect fails, a **Reconnect** button appears. Click it to re-establish the connection with saved credentials.

### Slow responses

- **Claude 3.5 Haiku** is the fastest model — switch to it in Settings
- Reduce **Max Tokens** for shorter responses
- Choose an **AWS Region** geographically close to you
- Cross-region inference helps availability but may add minor latency

### Logo shows with gray background

Hard refresh (Ctrl+Shift+R) to clear cached CSS. The logo PNG has a transparent background, and custom CSS removes the Chainlit image container background.

### Docker: Node.js / npx issues

The Docker image includes Node.js 20 for npx-based MCP servers. If running locally without Docker, install Node.js 18+ separately.

## Development

```bash
# Development mode with auto-reload
chainlit run app.py --watch

# Debug logging
LOG_LEVEL=DEBUG chainlit run app.py
```

### Adding New Models

Edit `BEDROCK_MODELS` in `app.py`:

```python
BEDROCK_MODELS = {
    "Claude 4 Sonnet": "anthropic.claude-sonnet-4-20250514",
    "Claude 3.7 Sonnet": "anthropic.claude-3-7-sonnet-20250219-v1:0",
    # Add new models here:
    "Your Model": "model-id-from-bedrock",
}
```

### Adding New Regions

Edit `AWS_REGIONS` in `app.py`:

```python
AWS_REGIONS = [
    "us-east-1",
    "us-west-2",
    # Add new regions here
]
```

### Customizing the System Prompt

Edit `SYSTEM_PROMPT` in `app.py` to change how the agent interacts with Qlik data.

### Customizing the Theme

- **Colors:** Edit `public/theme.json` (HSL values for light/dark modes)
- **CSS overrides:** Edit `public/qlik-theme.css`
- **Logo:** Replace `public/qlik-logo.png` (use a transparent PNG)
- **Fonts:** Update the `custom_fonts` array in `public/theme.json`

## License

See [LICENSE](LICENSE) for details.
