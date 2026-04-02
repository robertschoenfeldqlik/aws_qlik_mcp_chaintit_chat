![Qlik](/public/qlik-logo.png)

# Your Friendly Neighborhood AI Assistant

Chat with your **Qlik Cloud** data using **Anthropic Claude** on **AWS Bedrock** — powered by the Model Context Protocol (MCP).

---

## Getting Started

1. Click the **gear icon** in the top right to open Settings
2. Enter your **Qlik Cloud** credentials (Tenant URL + OAuth Client ID)
3. Enter your **AWS Bedrock** credentials (Access Key + Secret Key)
4. Select your preferred **model** and **region**
5. Click **Confirm** — you're connected!

## What Can I Do?

Once connected to your Qlik MCP server, you can ask questions like:

- *"What apps are available in my Qlik tenant?"*
- *"Show me the sales data from last quarter"*
- *"What dimensions and measures are in the Sales Dashboard app?"*
- *"Compare revenue across regions"*
- *"Summarize the key trends in my data"*

I use a **ReAct agent** — I reason about your question, call the right Qlik tools, and present the results clearly.

## Configuration

All settings are available in the **Settings panel** (gear icon):

| Setting | Description |
|---|---|
| **Qlik Tenant URL** | Your Qlik Cloud URL (e.g., `https://tenant.us.qlikcloud.com`) |
| **OAuth Client ID** | Created by your Qlik tenant admin |
| **OAuth Client Secret** | Leave empty if not required |
| **AWS Access Key ID** | IAM key with Bedrock permissions |
| **AWS Secret Access Key** | IAM secret key |
| **AWS Region** | us-east-1, us-west-2, eu-west-1, ap-southeast-1, ap-northeast-1 |
| **Bedrock Model** | Claude 4 Sonnet, 3.7 Sonnet, 3.5 Haiku, 3.5 Sonnet v2 |
| **Temperature** | 0.0 (precise) to 1.0 (creative) |
| **Max Tokens** | Response length limit (256 - 32,768) |

Changes take effect immediately — no restart needed.

## Qlik MCP Setup

Your Qlik Cloud tenant admin needs to create an OAuth client:

1. Go to **Administration** > **OAuth** > **Create new**
2. Select **Native** as the client type
3. Under Scopes, select **user_default** and **mcp:execute**
4. Click **Create** and share the **Client ID** with users

[Full Qlik MCP setup guide](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm)

## Troubleshooting

**Connection failed?** Check that your tenant URL includes `https://` and your OAuth Client ID is correct.

**Model access denied?** Enable Anthropic Claude models in the [AWS Bedrock console](https://console.aws.amazon.com/bedrock/home#/modelaccess).

**Slow responses?** Switch to **Claude 3.5 Haiku** (fastest) or reduce **Max Tokens**.

**Connection dropped?** Click the **Reconnect** button or re-open Settings and click Confirm.

## Links

- [Qlik MCP Documentation](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm)
- [AWS Bedrock Console](https://console.aws.amazon.com/bedrock/home)
- [Source Code on GitHub](https://github.com/robertschoenfeldqlik/aws_qlik_mcp_chaintit_chat)
