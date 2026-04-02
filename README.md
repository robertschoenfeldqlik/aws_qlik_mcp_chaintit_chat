# Qlik AI Assistant — AWS Bedrock + MCP Chat Interface

<p align="center">
  <img src="public/qlik-logo.png" alt="Qlik" width="300">
</p>

<p align="center">
  <strong>Your Friendly Neighborhood AI Assistant</strong><br>
  Chat with your Qlik Cloud data using Claude on AWS Bedrock
</p>

---

A branded web chat application that connects **Qlik Cloud** data to **Anthropic Claude Sonnet 4** via **AWS Bedrock**, using the **Model Context Protocol (MCP)** with **streamable-http** transport and **OAuth PKCE** authentication. Built with [Chainlit](https://docs.chainlit.io), [LangGraph](https://langchain-ai.github.io/langgraph/), and [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters).

## Architecture

```
┌─────────────────┐     ┌───────────────────────┐     ┌──────────────────┐
│    Browser       │────▶│    Chainlit UI         │────▶│   AWS Bedrock    │
│    (User)        │◀────│    (Python + Qlik CSS) │◀────│   Claude Sonnet 4│
└─────────────────┘     └──────────┬────────────┘     └──────────────────┘
                                   │
                                   │ streamable-http + OAuth PKCE
                                   ▼
                          ┌──────────────────┐
                          │  Qlik Cloud MCP  │
                          │  51 tools        │
                          └──────────────────┘
```

## How It Works

1. **Plug icon** → Enter Qlik Tenant URL + OAuth Client ID → OAuth redirect → Approve → **51 Qlik tools loaded**
2. **Gear icon** → Configure Bedrock API Key, region, model
3. **Ask questions** → Claude calls Qlik MCP tools → returns data from your tenant

## Cost Warning

> **This application uses AWS Bedrock which charges per API call.** Claude Sonnet 4 pricing on Bedrock is approximately **$3 per million input tokens** and **$15 per million output tokens**. Each question you ask makes at least one LLM call plus one or more MCP tool calls. For light demo/presales use this typically costs **$1-5/month**, but heavy usage with complex multi-step queries can add up. Monitor your costs in the [AWS Billing console](https://console.aws.amazon.com/billing/home). Consider setting up [AWS Budgets](https://console.aws.amazon.com/billing/home#/budgets) to alert you when spending exceeds a threshold.

## Prerequisites

### AWS Bedrock

1. Go to the [Amazon Bedrock console](https://console.aws.amazon.com/bedrock/home)
2. Navigate to **Model catalog** → search for **Claude**
3. **Submit the Anthropic use case details form** (required for first-time Anthropic model usage)
   - Company name and brief use case description
   - Approval takes ~15 minutes
4. Go to **API keys** in the left sidebar → **Create API key** (short-term or long-term)
5. Copy the API key — it starts with `bedrock-api-key-...`

### Qlik Cloud

1. Your tenant admin goes to **Administration** → **OAuth** → **Create new**
2. **Client type:** Native
3. **Scopes:** `user_default` and `mcp:execute`
4. **Redirect URL:** `http://localhost:8000/auth/qlik/callback`
5. Click **Create** and copy the **Client ID**
6. Share the Client ID with users

Full instructions: [Qlik MCP setup guide](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm) | [Deploying Qlik MCP](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Administering-Qlik-MCP.htm)

## Quick Start

### Local Python

```bash
git clone https://github.com/robertschoenfeldqlik/aws_qlik_mcp_chaintit_chat.git
cd aws_qlik_mcp_chaintit_chat

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your Bedrock API key and Qlik tenant URL

chainlit run app.py
```

### Docker

```bash
git clone https://github.com/robertschoenfeldqlik/aws_qlik_mcp_chaintit_chat.git
cd aws_qlik_mcp_chaintit_chat

cp .env.example .env
# Edit .env

docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000).

## Configuration

### Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `AWS_BEARER_TOKEN_BEDROCK` | Yes | Bedrock API key from console > API keys |
| `AWS_DEFAULT_REGION` | No | Region matching your API key (default: `us-east-1`) |
| `QLIK_TENANT_URL` | No | Pre-fills the Qlik connection form |
| `QLIK_OAUTH_CLIENT_ID` | No | Pre-fills the OAuth Client ID |
| `APP_BASE_URL` | No | Base URL for OAuth callback (default: `http://localhost:8000`) |
| `LOG_LEVEL` | No | DEBUG, INFO, WARNING, ERROR (default: `INFO`) |

### Gear Icon (Settings Panel)

| Setting | Default | Description |
|---|---|---|
| **Bedrock API Key** | From env | Bedrock API key |
| **AWS Region** | us-east-1 | Must match API key region |
| **Bedrock Model** | Claude 4 Sonnet | Claude Sonnet 4 recommended — best performance and cost for tool calling |
| **Temperature** | 0.2 | Lower = more deterministic tool calling |
| **Max Tokens** | 4096 | Response length limit |

### Plug Icon (Qlik MCP Connection)

The plug icon opens a Qlik-branded form:
- **Qlik Tenant URL** — `https://your-tenant.us.qlikcloud.com`
- **OAuth Client ID** — from your Qlik admin

Click **Connect** → redirects to Qlik Cloud OAuth → sign in → approve → **51 tools loaded**.

The OAuth flow uses **Authorization Code + PKCE (S256)** via the `streamable-http` MCP transport.

## Qlik MCP Documentation

For full details on the Qlik MCP server, available tools, connection parameters, and OAuth configuration, see the official Qlik documentation:

- [Connecting to the Qlik MCP server](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm)
- [Qlik MCP server tools](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Qlik-MCP-server-tools.htm)
- [Deploying Qlik MCP server for a tenant](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Administering-Qlik-MCP.htm)

## Project Structure

```
.
├── app.py                     # Main Chainlit application
├── qlik_oauth.py              # OAuth PKCE flow for Qlik Cloud
├── public/
│   ├── qlik-logo.png          # Qlik logo (transparent background)
│   ├── qlik-mcp.js            # Custom MCP dialog for Qlik
│   └── qlik-theme.css         # Qlik brand CSS
│   └── theme.json             # Chainlit theme (Qlik colors)
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container build
├── docker-compose.yml         # One-command deployment
├── .env.example               # Environment variable template
├── .chainlit/config.toml      # Chainlit configuration
└── chainlit.md                # In-app readme
```

## Technology Stack

| Component | Technology |
|---|---|
| **Chat UI** | [Chainlit](https://docs.chainlit.io) 2.10+ |
| **LLM** | [AWS Bedrock](https://aws.amazon.com/bedrock/) — Claude Sonnet 4 |
| **Agent** | [LangGraph](https://langchain-ai.github.io/langgraph/) ReAct agent |
| **MCP Bridge** | [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) |
| **MCP Transport** | streamable-http with OAuth PKCE |
| **Data Source** | [Qlik Cloud MCP](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm) — 51 tools |
| **Theming** | Qlik brand colors + Source Sans 3 font |

## Troubleshooting

### "Model use case details have not been submitted"
Submit the Anthropic use case form in the Bedrock console under Model catalog → Claude. Takes ~15 minutes to approve.

### MCP connection fails with "unhandled errors in a TaskGroup"
The MCP SSE transport doesn't work with Qlik — use `streamable-http`. This is handled automatically by the plug icon form.

### Claude lists tools but doesn't call them
Lower the temperature to 0.2 in Settings. The system prompt is tuned for tool calling.

### OAuth callback fails
Verify `http://localhost:8000/auth/qlik/callback` is registered as a redirect URL in your Qlik OAuth client configuration.

### Bedrock API key expired
Generate a new key from Bedrock console → API keys. Short-term keys last up to 12 hours.

## License

See [LICENSE](LICENSE) for details.
