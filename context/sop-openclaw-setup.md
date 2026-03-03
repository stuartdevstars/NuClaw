# SOP: Setting Up OpenClaw with Slack and Notion

**Version:** 1.0  
**Author:** Devstars  
**Last Updated:** March 2026  
**Audience:** Internal team members setting up a new OpenClaw instance

---

## Overview

This SOP covers the basics of getting OpenClaw installed and connected to Slack and Notion. By the end you'll have a running AI agent accessible via Slack that can read and write to your Notion workspace.

For adding sub-agents and skills, see the separate SOP (coming soon).

---

## Prerequisites

- A Linux or macOS machine (Ubuntu 24.04 recommended)
- Node.js v18+ installed
- An Anthropic API key (get one at console.anthropic.com)
- Admin access to your Slack workspace
- A Notion integration token

---

## Step 1 — Install OpenClaw

```bash
npm install -g openclaw
```

Verify the install:

```bash
openclaw --version
```

---

## Step 2 — Run the Setup Wizard

```bash
openclaw setup
```

The wizard will walk you through:

- Setting your primary AI model (use `anthropic/claude-sonnet-4-6` for best results)
- Entering your Anthropic API key
- Naming your agent and setting up the workspace

Your workspace will be created at `~/.openclaw/workspace`. This is where your agent's persona files, memory, and skills live.

---

## Step 3 — Configure Your Agent's Identity

Navigate to `~/.openclaw/workspace` and edit the following files:

**`IDENTITY.md`** — Give your agent a name, personality, and emoji.

**`SOUL.md`** — Define how your agent should behave: tone, values, boundaries. The default is a good starting point.

**`USER.md`** — Tell your agent about who they're working with: name, timezone, role, communication preferences.

---

## Step 4 — Connect Slack

### 4a — Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**
2. Choose **From scratch**, give it a name (e.g. your agent's name), pick your workspace
3. Under **Socket Mode**, enable it and generate an **App-Level Token** (scope: `connections:write`). Copy this — it's your `appToken`.
4. Under **OAuth & Permissions**, add these Bot Token Scopes:
   - `app_mentions:read`
   - `channels:history`
   - `channels:read`
   - `chat:write`
   - `files:write`
   - `groups:history`
   - `groups:read`
   - `im:history`
   - `im:read`
   - `im:write`
   - `mpim:history`
   - `reactions:write`
   - `users:read`
5. Under **Event Subscriptions**, enable and subscribe to:
   - `app_mention`
   - `message.channels`
   - `message.groups`
   - `message.im`
   - `message.mpim`
6. Under **App Home**, enable **Messages Tab** and tick **Allow users to send Slash commands and messages from the messages tab**
7. Click **Install to Workspace** and copy the **Bot User OAuth Token** — this is your `botToken`.

### 4b — Add to openclaw.json

Edit `~/.openclaw/openclaw.json` and add the Slack channel config:

```json
"channels": {
  "slack": {
    "mode": "socket",
    "enabled": true,
    "groupPolicy": "open",
    "dmPolicy": "open",
    "streaming": "partial",
    "nativeStreaming": true,
    "accounts": {
      "default": {
        "botToken": "xoxb-YOUR-BOT-TOKEN",
        "appToken": "xapp-YOUR-APP-TOKEN"
      }
    }
  }
}
```

### 4c — Invite your bot to channels

In Slack, invite your bot to any channels it should have access to:

```
/invite @YourBotName
```

### 4d — Restart the gateway

```bash
openclaw gateway restart
```

Your agent is now live on Slack. DM the bot or @ mention it in a channel to test.

---

## Step 5 — Connect Notion

### 5a — Create a Notion Integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **New integration**
3. Give it a name (e.g. your agent's name), select your workspace
4. Copy the **Internal Integration Token** — this starts with `secret_`

### 5b — Store the token

Create a `.env` file in your workspace:

```bash
echo "NOTION_TOKEN=secret_your_token_here" > ~/.openclaw/workspace/.env
chmod 600 ~/.openclaw/workspace/.env
```

> ⚠️ The `chmod 600` is important — this locks the file to your user only.

### 5c — Share pages with your integration

Notion integrations only have access to pages you explicitly share with them.

For each page or database your agent needs to access:

1. Open the page in Notion
2. Click **...** (top right) → **Connections**
3. Find your integration and click **Connect**

### 5d — Test it

Ask your agent in Slack:

> "Can you check my Notion and tell me what pages you have access to?"

---

## Step 6 — Start the Gateway as a Service

To keep OpenClaw running in the background and auto-start on reboot:

```bash
openclaw gateway service install
openclaw gateway service start
```

Verify it's running:

```bash
openclaw gateway status
```

---

## Step 7 — Basic Security Hardening

Before going live, do these:

```bash
# Lock down your workspace permissions
chmod -R o-r ~/.openclaw

# Harden SSH (run in terminal)
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

---

## Troubleshooting

**Agent not responding in Slack**
- Check gateway is running: `openclaw gateway status`
- Check the bot is invited to the channel
- Check your botToken and appToken are correct in `openclaw.json`

**Notion access denied**
- Make sure the integration is connected to the specific page/database
- Check the `.env` file contains the correct token and is readable by the gateway user

**Gateway won't start**
- Check for config errors: `openclaw gateway logs`
- Validate your `openclaw.json` is valid JSON

---

## Key Files Reference

| File | Purpose |
|---|---|
| `~/.openclaw/openclaw.json` | Main config — channels, agents, gateway |
| `~/.openclaw/workspace/SOUL.md` | Agent personality and behaviour |
| `~/.openclaw/workspace/IDENTITY.md` | Agent name, emoji, avatar |
| `~/.openclaw/workspace/USER.md` | Info about the human the agent serves |
| `~/.openclaw/workspace/MEMORY.md` | Agent's long-term memory |
| `~/.openclaw/workspace/.env` | API keys and secrets |

---

*For adding sub-agents, skills, and advanced configuration — see SOP: OpenClaw Advanced Setup (coming soon).*
