import os
import asyncio
import logging
import json
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from github import Github
import google.generativeai as genai

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load Environment Variables
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
INCLUDE_PRIVATE = os.getenv('INCLUDE_PRIVATE', 'false').lower() == 'true'
TARGET_REPOS = os.getenv('TARGET_REPOS', '').split(',') if os.getenv('TARGET_REPOS') else []
STATE_FILE = 'reviewed_state.json'

# Initialize Clients
if not all([GITHUB_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GOOGLE_API_KEY]):
    logger.error("Missing required environment variables.")
    exit(1)

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemma-3-27b-it')
gh = Github(GITHUB_TOKEN)

# --- State Management ---
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

# --- Core Logic ---

async def analyze_pr_content(pr, diff_content):
    issue_context = pr.body if pr.body else "No linked issue found."
    
    prompt = f"""
    You are a strict Senior Software Engineer reviewing a Pull Request.
    
    **Context:**
    Repo: {pr.base.repo.full_name}
    PR Title: {pr.title}
    PR Description: {issue_context}
    
    **Code Changes (Diff):**
    ```
    {diff_content[:30000]} 
    ```
    
    **Task:**
    Analyze the changes. Determine if this is a "Good Push" or "Bad Push".
    
    **Output Format (Markdown):**
    **Verdict:** [Good Push / Bad Push]
    
    **Summary:**
    [1-2 sentences]
    
    **Critique:**
    *   **Bad Practices:** [Security risks, dirty code, anti-patterns]
    *   **Issue Alignment:** [Does this solve the problem?]
    *   **Improvements:** [Specific suggestions]    
    
    If it's a perfect push, explicitly state "No issues found."
    """
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini API Error: {e}")
        return "Error analyzing PR with AI."

async def run_pr_check(context: ContextTypes.DEFAULT_TYPE = None, manual_chat_id=None):
    """
    Main Logic to check PRs. 
    Can be triggered by Scheduler (context provided) or Manually (manual_chat_id provided).
    """
    logger.info("Starting PR Check...")
    chat_id = manual_chat_id if manual_chat_id else TELEGRAM_CHAT_ID
    bot = context.bot if context else Bot(token=TELEGRAM_TOKEN)
    
    try:
        state = load_state()
        user = await asyncio.to_thread(gh.get_user)
        
        # Determine repos
        repos_to_scan = []
        if TARGET_REPOS and TARGET_REPOS[0]:
            for repo_name in TARGET_REPOS:
                try:
                    r = await asyncio.to_thread(gh.get_repo, repo_name.strip())
                    repos_to_scan.append(r)
                except Exception as e:
                    logger.error(f"Could not access {repo_name}: {e}")
        else:
            # Fetch all owned repos
            all_repos = await asyncio.to_thread(user.get_repos, type='owner', sort='updated', direction='desc')
            # Convert PaginatedList to list to iterate safely in async
            for repo in all_repos:
                if not INCLUDE_PRIVATE and repo.private:
                    continue
                repos_to_scan.append(repo)

        changes_found = False
        
        for repo in repos_to_scan:
            logger.info(f"Checking {repo.full_name}...")
            open_prs = await asyncio.to_thread(repo.get_pulls, state='open')
            
            for pr in open_prs:
                pr_id = f"{repo.full_name}#{pr.number}"
                last_commit = pr.head.sha
                
                # If we've seen this commit, skip
                if state.get(pr_id) == last_commit:
                    continue
                
                changes_found = True
                await bot.send_message(chat_id=chat_id, text=f"🔎 Analyzing new changes in **{repo.full_name}** PR #{pr.number}...", parse_mode="Markdown")
                
                # Fetch Diff
                diff_resp = await asyncio.to_thread(requests.get, pr.diff_url)
                if diff_resp.status_code != 200:
                    continue
                    
                analysis = await analyze_pr_content(pr, diff_resp.text)
                
                msg = f"**PR Analysis: {repo.full_name}**\n[#{pr.number}: {pr.title}]({pr.html_url})\n\n{analysis}"
                try:
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Markdown failed, sending plain text: {e}")
                    await bot.send_message(chat_id=chat_id, text=msg)
                
                state[pr_id] = last_commit
                save_state(state)
        
        if manual_chat_id and not changes_found:
             await bot.send_message(chat_id=chat_id, text="✅ No new PR updates found.")
             
    except Exception as e:
        logger.error(f"Error during PR check: {e}")
        if manual_chat_id:
            await bot.send_message(chat_id=chat_id, text=f"⚠️ Error running check: {e}")

# --- Bot Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("I am running! I will check your PRs at 07:00, 13:00, and 19:00.")

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Manually starting PR check...")
    await run_pr_check(context=context, manual_chat_id=update.effective_chat.id)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Uses Gemini to answer user questions, with optional GitHub context.
    """
    user_text = update.message.text
    logger.info(f"User message: {user_text}")
    
    context_str = ""
    
    # 1. Check for "issue" context
    if "issue" in user_text.lower():
        try:
            await update.message.chat.send_action(action="typing")
            
            # Helper to find repo
            found_repo = None
            user = await asyncio.to_thread(gh.get_user)
            
            # Strategy A: Check for "owner/repo" string directly
            words = user_text.split()
            for word in words:
                clean_word = word.strip("?,.!:'\"")
                if "/" in clean_word:
                    try:
                        found_repo = await asyncio.to_thread(gh.get_repo, clean_word)
                        break
                    except:
                        continue
            
            # Strategy B: Check against user's owned repos
            if not found_repo:
                # limited to recently updated to avoid API limits on massive accounts
                repos = await asyncio.to_thread(user.get_repos, type='owner', sort='updated', direction='desc')
                
                # We need to iterate carefully. PyGithub PaginatedList is sync.
                # We'll fetch the first ~30 to check names.
                def find_in_repos():
                    count = 0
                    for r in repos:
                        if r.name.lower() in user_text.lower():
                            return r
                        count += 1
                        if count > 50: break
                    return None
                
                found_repo = await asyncio.to_thread(find_in_repos)

            if found_repo:
                await update.message.reply_text(f"🔍 Found repository: {found_repo.full_name}. Fetching issues...")
                
                issues = await asyncio.to_thread(found_repo.get_issues, state='open')
                
                def get_issues_summary():
                    summary = []
                    count = 0
                    for i in issues:
                        if count >= 10: break
                        summary.append(f"- #{i.number}: {i.title} (assigned: {i.assignee.login if i.assignee else 'None'})")
                        count += 1
                    return "\n".join(summary)
                
                issue_list = await asyncio.to_thread(get_issues_summary)
                context_str = f"**Open Issues in {found_repo.full_name}:**\n{issue_list}\n"
            else:
                 # Optional: Tell user we couldn't find a repo if they specifically asked for issues?
                 # For now, we just proceed to Gemini without context if no repo matches.
                 pass

        except Exception as e:
            logger.error(f"Failed fetching context: {e}")
            # Don't crash, just continue to Gemini

    # 2. Query Gemini
    prompt = f"""
    You are a helpful AI Assistant integrated with the user's GitHub.
    User Query: {user_text}
    
    Context Information (if any):
    {context_str}
    
    Answer the user. 
    - If they asked about issues and you have the list, summarize them.
    - If they asked about code, write code. 
    - If context is missing, ask them to specify the repository name.
    """
    
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        try:
            await update.message.reply_text(response.text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Markdown failed, sending plain text: {e}")
            await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"Error getting AI response: {e}")

async def on_startup(application: ApplicationBuilder):
    """
    Runs once when the bot starts.
    Sends 'Service is online!', waits 5s, deletes it.
    """
    try:
        msg = await application.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="🟢 Service is online!")
        await asyncio.sleep(5)
        await application.bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=msg.message_id)
    except Exception as e:
        logger.error(f"Startup message error: {e}")

# --- Main Application ---

def main():
    logger.info("Starting AI-PR-Analyzer Bot...")
    
    # 1. Setup Scheduler
    scheduler = AsyncIOScheduler()
    # Schedules: 7am, 1pm (13), 7pm (19)
    scheduler.add_job(run_pr_check, CronTrigger(hour='7,13,19', minute=0))
    scheduler.start()
    
    # 2. Setup Telegram Bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(on_startup).build()
    
    # 3. Register Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    # 4. Run
    # Note: run_polling is blocking, which is what we want for the main process
    application.run_polling()

if __name__ == "__main__":
    main()