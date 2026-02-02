# AI-PR-Analyzer

An AI-powered GitHub Pull Request Analyzer and Developer Assistant.

## Features
- 🤖 **Automated PR Analysis**: Checks PRs for code quality, security, and best practices.
- 💬 **Developer Chatbot**: Ask questions about your code, repository, or general programming topics.
- 🔎 **Web Search**: Integrated DuckDuckGo search for up-to-date answers (`/search` or natural language).
- 🗣️ **Voice Interaction**: Send voice notes and get audio-aware responses (powered by Gemini Flash).
- 💾 **Session Management**: Save and load chat sessions (`/chat save/load`).
- ⏰ **Scheduled Checks**: Automatically scans your repos at 07:00, 13:00, and 19:00.

## Quick Start with Docker Compose

You can run this bot anywhere using Docker Compose. This configuration automatically downloads the latest code from GitHub on startup.

### `docker-compose.yml`

```yaml
services:
  ai-pr-analyzer:
    image: python:3.11-slim
    container_name: ai-pr-analyzer
    restart: unless-stopped
    working_dir: /app
    environment:
      - GITHUB_TOKEN=your_github_token
      - TELEGRAM_TOKEN=your_telegram_token
      - TELEGRAM_CHAT_ID=your_chat_id
      - GOOGLE_API_KEY=your_google_api_key
      # Optional:
      - INCLUDE_PRIVATE=false
      - TARGET_REPOS=owner/repo1,owner/repo2
    command: >
      /bin/sh -c "
      apt-get update && apt-get install -y wget &&
      wget -O requirements.txt https://raw.githubusercontent.com/abduznik/AI-PR-Analyzer/refs/heads/main/requirements.txt &&
      wget -O main.py https://raw.githubusercontent.com/abduznik/AI-PR-Analyzer/refs/heads/main/main.py &&
      pip install -r requirements.txt &&
      python3 -u main.py
      "
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | Your GitHub Personal Access Token (Repo scope). |
| `TELEGRAM_TOKEN` | Your Telegram Bot Token (from @BotFather). |
| `TELEGRAM_CHAT_ID` | Your Telegram User ID (get it from @userinfobot). |
| `GOOGLE_API_KEY` | Gemini API Key (from AI Studio). |
| `INCLUDE_PRIVATE` | Set to `true` to scan private repositories. |
| `TARGET_REPOS` | Comma-separated list of specific repos to check (e.g., `user/repo1,user/repo2`). |

## Commands

- `/start`: Check bot status.
- `/check`: Manually trigger a PR check.
- `/clear`: Clear current chat history.
- `/search <query>`: Perform a web search.
- `/chat`: Manage sessions.
    - `/chat save <name>`
    - `/chat load <name>`
    - `/chat list`
    - `/chat remove <name>`

## License
MIT