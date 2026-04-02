![Qlik](/public/qlik-logo.png)

# Your Friendly Neighborhood AI Assistant

Chat with your **Qlik Cloud** data using **Claude Sonnet 4** on **AWS Bedrock** — powered by 51 MCP tools.

---

## Getting Started

1. Click the **plug icon** to connect to Qlik Cloud
2. Click the **gear icon** to configure AWS Bedrock
3. Ask questions about your data!

## What Can I Ask?

- "What apps do I have?"
- "Show me the sheets in the CycleParts app"
- "What fields are available in Customer Churn?"
- "List all data products"
- "Search for datasets"
- "What's the trust score of this dataset?"
- "Create a bar chart showing sales by region"

## Setup

**Qlik Cloud:** Your tenant admin creates a Native OAuth client with scopes `user_default` and `mcp:execute`, and registers `http://localhost:8000/auth/qlik/callback` as the redirect URL.

**AWS Bedrock:** Generate an API key from the Bedrock console. Submit the Anthropic use case form if using Claude models.

## Cost

AWS Bedrock charges per API call. Claude Sonnet 4: ~$3/M input tokens, ~$15/M output tokens. Light demo use is typically $1-5/month.
