# AI-PR-Analyzer

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Gemini](https://img.shields.io/badge/AI-Gemini-orange)

**AI-PR-Analyzer** is an automated background service that monitors your GitHub repositories for open Pull Requests. It uses Google's Gemini AI to analyze the code changes against the linked issue (or PR description) and sends a detailed critique directly to your Telegram.

It acts as an automated Senior Software Engineer, providing immediate feedback on "Good Pushes" vs "Bad Pushes", highlighting security risks, anti-patterns, and issue alignment.

## Features

-   **Automated Monitoring:** Checks for new PR updates daily at 07:00, 13:00, and 19:00.
-   **Interactive Chat:** Chat with the bot using Gemini. Ask general coding questions or about your repo issues.
-   **Manual Triggers:** Use `/check` to instantly run the PR analyzer.
-   **AI-Powered Code Review:** Uses Gemma 3 27B to analyze diffs and context.
-   **Telegram Notifications:** Delivers formatted verdicts (Good/Bad), summaries, and actionable critiques.
-   **State Management:** Tracks reviewed commits to avoid duplicate notifications.
-   **Docker Ready:** Designed to run easily via Docker Compose.

## Prerequisites

Before running, ensure you have the following:

1.  **GITHUB_TOKEN:** [Generate a Personal Access Token (Classic)](https://github.com/settings/tokens) with `repo` scopes.
2.  **TELEGRAM_TOKEN:** Create a bot via [@BotFather](https://t.me/BotFather) and get the Token.
3.  **TELEGRAM_CHAT_ID:** Get your Chat ID (you can use [@userinfobot](https://t.me/userinfobot)).
4.  **GOOGLE_API_KEY:** Get your free API key from [Google AI Studio](https://aistudio.google.com/).

## Configuration Options

| Variable | Description | Default |
| :--- | :--- | :--- |
| `INCLUDE_PRIVATE` | Set to `true` to include private repositories. | `false` |
| `TARGET_REPOS` | Comma-separated list of specific repos (e.g., `user/repo`). If empty, scans all owned repos. | `(empty)` |

## Commands

Once the bot is running, you can use these commands in Telegram:

*   `/start`: Confirm the bot is running.
*   `/check`: Manually trigger a PR scan immediately.
*   **Chat:** Send any message to ask Gemini questions. If you mention "issue" and a repo name (e.g., "Summarize issues in my-repo"), it will try to fetch context.

## Quick Start (Docker Compose)

This project is designed to be deployed using Docker Compose (perfect for tools like Dockge or Portainer).

1.  Clone this repository.
2.  Create a `compose.yaml` file using the template below.
3.  Fill in your environment variables.
4.  Run the stack.

### Docker Compose Template

Copy the following into your `compose.yaml` or Dockge stack configuration:

```yaml
services:
  ai-pr-analyzer:
    image: python:3.11-slim
    container_name: ai-pr-analyzer
    restart: unless-stopped
    working_dir: /app
    # Installs dependencies from requirements.txt and starts the analyzer
    command: /bin/sh -c "pip install -r requirements.txt && python3 -u main.py"
    environment:
      - GITHUB_TOKEN
      - TELEGRAM_TOKEN
      - TELEGRAM_CHAT_ID
      - GOOGLE_API_KEY
      - INCLUDE_PRIVATE
      - TARGET_REPOS
    volumes:
      - .:/app
    ipc: host

networks: {}
```

> **Note:** Ensure you have a `.env` file in the same directory as your `compose.yaml` containing the actual values for these variables, or define them in your Dockge environment UI.


## Manual Installation

If you prefer to run it locally without Docker:

1.  **Clone the repo:**
    ```bash
    git clone https://github.com/yourusername/AI-PR-Analyzer.git
    cd AI-PR-Analyzer
    ```

2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Environment:**
    Create a `.env` file or export the variables listed in the Docker Compose section above.

4.  **Run:**
    ```bash
    python src/main.py
    ```

## License

This project is licensed under the MIT License.
