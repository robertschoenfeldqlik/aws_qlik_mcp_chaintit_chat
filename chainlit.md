# Qlik AI Assistant

Welcome! This assistant connects to your **Qlik Cloud** data through **AWS Bedrock** LLMs.

## Getting Started

1. **Configure the model** — Click the gear icon to select your Bedrock model and AWS region
2. **Connect to Qlik** — Click the plug icon to add your Qlik MCP server connection
3. **Ask questions** — Query your Qlik data using natural language

## MCP Server Options

**Qlik Cloud Native (OAuth):**
- URL: `https://your-tenant.us.qlikcloud.com/api/ai/mcp`

**Community Package (API Key):**
- Command: `npx -y @agentsbazaar/mcp`
- Requires `QLIK_TENANT_URL` and `QLIK_API_KEY` environment variables
