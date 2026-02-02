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
from duckduckgo_search import DDGS
from pydub import AudioSegment
import speech_recognition as sr

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
HISTORY_FILE = 'chat_history.json'

# --- History Management ---
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                data = json.load(f)
                migrated_data = {}
                for chat_id, content in data.items():
                    if isinstance(content, list):
                        migrated_data[chat_id] = {
                            "current_session": content,
                            "saved_sessions": {}
                        }
                    else:
                        migrated_data[chat_id] = content
                return migrated_data
        except json.JSONDecodeError:
            return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

# Initialize Clients
if not all([GITHUB_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GOOGLE_API_KEY]):
    logger.error("Missing required environment variables.")
    exit(1)

genai.configure(api_key=GOOGLE_API_KEY)
# Using Gemma for text (High Quota)
model = genai.GenerativeModel('gemma-3-27b-it')

gh = Github(GITHUB_TOKEN)

# --- Helper Functions ---

def perform_web_search(query, max_results=3):
    """Searches the web using DuckDuckGo."""
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "No results found."
        
        formatted_results = ""
        for r in results:
            formatted_results += f"- [{r['title']}]({r['href']}): {r['body']}\n"
        return formatted_results
    except Exception as e:
        logger.error(f"Search error: {e}")
        return f"Error performing search: {e}"

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
    logger.info("Starting PR Check...")
    chat_id = manual_chat_id if manual_chat_id else TELEGRAM_CHAT_ID
    bot = context.bot if context else Bot(token=TELEGRAM_TOKEN)
    
    try:
        state = load_state()
        user = await asyncio.to_thread(gh.get_user)
        
        repos_to_scan = []
        if TARGET_REPOS and TARGET_REPOS[0]:
            for repo_name in TARGET_REPOS:
                try:
                    r = await asyncio.to_thread(gh.get_repo, repo_name.strip())
                    repos_to_scan.append(r)
                except Exception as e:
                    logger.error(f"Could not access {repo_name}: {e}")
        else:
            all_repos = await asyncio.to_thread(user.get_repos, type='owner', sort='updated', direction='desc')
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
                
                if state.get(pr_id) == last_commit:
                    continue
                
                changes_found = True
                await bot.send_message(chat_id=chat_id, text=f"🔎 Analyzing new changes in **{repo.full_name}** PR #{pr.number}...", parse_mode="Markdown")
                
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

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears the current chat session."""
    chat_id = str(update.effective_chat.id)
    history = load_history()
    if chat_id in history:
        history[chat_id]["current_session"] = []
        save_history(history)
        await update.message.reply_text("🧹 Current chat session cleared!")
    else:
        await update.message.reply_text("Chat history is already empty.")

async def clear_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Completely wipes all chat history for the user."""
    chat_id = str(update.effective_chat.id)
    history = load_history()
    if chat_id in history:
        del history[chat_id]
        save_history(history)
        await update.message.reply_text("🧨 All chat history (including saved sessions) has been obliterated.")
    else:
        await update.message.reply_text("You have no history to clear.")

async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /chat [save|load|remove|list] <name>")
        return

    subcommand = context.args[0].lower()
    chat_id = str(update.effective_chat.id)
    history_data = load_history()
    
    if chat_id not in history_data:
        history_data[chat_id] = {"current_session": [], "saved_sessions": {}}
    
    user_data = history_data[chat_id]

    if subcommand == "save":
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ Please provide a name to save the session.")
            return
        name = context.args[1]
        user_data["saved_sessions"][name] = list(user_data["current_session"])
        save_history(history_data)
        await update.message.reply_text(f"💾 Session saved as '{name}'.")

    elif subcommand == "load":
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ Please provide a name to load.")
            return
        name = context.args[1]
        if name in user_data["saved_sessions"]:
            user_data["current_session"] = list(user_data["saved_sessions"][name])
            save_history(history_data)
            await update.message.reply_text(f"📂 Loaded session '{name}'.")
        else:
            await update.message.reply_text(f"❌ Session '{name}' not found.")

    elif subcommand == "remove":
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ Please provide a name to remove.")
            return
        name = context.args[1]
        if name in user_data["saved_sessions"]:
            del user_data["saved_sessions"][name]
            save_history(history_data)
            await update.message.reply_text(f"🗑️ Session '{name}' removed.")
        else:
            await update.message.reply_text(f"❌ Session '{name}' not found.")

    elif subcommand == "list":
        sessions = list(user_data["saved_sessions"].keys())
        if sessions:
            msg = "**Saved Sessions:**\n" + "\n".join([f"- {s}" for s in sessions])
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("No saved sessions found.")
    
    else:
        await update.message.reply_text("Unknown command. Use: save, load, remove, list")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explicitly searches the web and returns a summary."""
    if not context.args:
        await update.message.reply_text("Usage: /search <query>")
        return
    
    query = " ".join(context.args)
    await update.message.chat.send_action(action="typing")
    results = perform_web_search(query)
    
    prompt = f"Summarize these search results for the query '{query}':\n\n{results}"
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        await update.message.reply_text(response.text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error summarizing search: {e}")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles voice messages by transcribing locally and passing to Gemma."""
    try:
        await update.message.chat.send_action(action="record_voice")
        file = await context.bot.get_file(update.message.voice.file_id)
        
        # Paths
        ogg_file = f"voice_{update.effective_chat.id}.ogg"
        wav_file = f"voice_{update.effective_chat.id}.wav"
        
        # Download
        await file.download_to_drive(ogg_file)
        
        # Convert OGG to WAV using Pydub (SpeechRecognition needs WAV)
        try:
            audio = AudioSegment.from_ogg(ogg_file)
            audio.export(wav_file, format="wav")
        except Exception as conv_err:
            logger.error(f"Audio conversion failed: {conv_err}")
            await update.message.reply_text("⚠️ Could not process audio format. Install ffmpeg?")
            if os.path.exists(ogg_file): os.remove(ogg_file)
            return

        # Transcribe
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_file) as source:
            audio_data = recognizer.record(source)
            try:
                # Uses Google Web Speech API (Free, Unofficial, Good for commands)
                text = recognizer.recognize_google(audio_data)
                await update.message.reply_text(f"🗣️ *Heard:* \"{text}\"", parse_mode="Markdown")
                
                # Hand off to text processor
                await process_text_message(update, context, override_text=text)
                
            except sr.UnknownValueError:
                await update.message.reply_text("🤔 Could not understand audio.")
            except sr.RequestError as e:
                await update.message.reply_text(f"⚠️ Speech Recognition Error: {e}")

        # Clean up
        if os.path.exists(ogg_file): os.remove(ogg_file)
        if os.path.exists(wav_file): os.remove(wav_file)
        
    except Exception as e:
        logger.error(f"Voice handling error: {e}")
        await update.message.reply_text(f"⚠️ Error processing voice: {e}")

async def process_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text=None):
    """Unified logic for text and voice-transcribed input."""
    user_text = override_text if override_text else update.message.text
    chat_id = str(update.effective_chat.id)
    logger.info(f"User message ({chat_id}): {user_text}")
    
    history_data = load_history()
    user_data = history_data.get(chat_id, {"current_session": [], "saved_sessions": {}})
    user_history = user_data["current_session"]
    
    context_str = ""
    
    # Check for "search" or "google" or "find" intent to auto-search
    if any(keyword in user_text.lower() for keyword in ["search for", "google for", "find out about"]):
        await update.message.chat.send_action(action="typing")
        search_query = user_text.lower().replace("search for", "").replace("google for", "").replace("find out about", "").strip()
        search_results = perform_web_search(search_query)
        context_str += f"**Web Search Results for '{search_query}':**\n{search_results}\n"

    # Check for "issue" context
    if "issue" in user_text.lower():
        try:
            await update.message.chat.send_action(action="typing")
            user = await asyncio.to_thread(gh.get_user)
            user_login = user.login
            found_repo = None
            words = user_text.split()
            for word in words:
                clean_word = word.strip("?,.!:'\"")
                if "/" in clean_word:
                    try:
                        found_repo = await asyncio.to_thread(gh.get_repo, clean_word)
                        if found_repo: break
                    except: continue
                else:
                    try:
                        potential_repo_name = f"{user_login}/{clean_word}"
                        found_repo = await asyncio.to_thread(gh.get_repo, potential_repo_name)
                        if found_repo: break
                    except: continue
            
            if found_repo:
                issues = await asyncio.to_thread(found_repo.get_issues, state='open')
                def get_issues_summary():
                    summary = []; count = 0
                    for i in issues:
                        if count >= 10: break
                        summary.append(f"- #{i.number}: {i.title}")
                        count += 1
                    return "\n".join(summary)
                issue_list = await asyncio.to_thread(get_issues_summary)
                context_str += f"**Open Issues in {found_repo.full_name}:**\n{issue_list}\n"
        except Exception as e:
            logger.error(f"Failed fetching context: {e}")

    relevant_history = user_history[-10:] 
    history_str = ""
    for msg in relevant_history:
        role = "User" if msg['role'] == 'user' else "Assistant"
        history_str += f"{role}: {msg['content']}\n"

    prompt = f"""
    You are a helpful AI Assistant integrated with GitHub and Web Search.
    
    **Chat History:**
    {history_str}
    
    **Current User Query:** {user_text}
    
    **Context Information (if any):**
    {context_str}
    
    Answer the user. 
    - Use the search results if available to provide up-to-date information.
    - If they asked about issues, summarize them.
    - If they asked about code, write code. 
    """
    
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        reply_text = response.text
        try:
            await update.message.reply_text(reply_text, parse_mode="Markdown")
        except:
            await update.message.reply_text(reply_text)
            
        user_history.append({"role": "user", "content": user_text})
        user_history.append({"role": "assistant", "content": reply_text})
        user_data["current_session"] = user_history[-20:]
        history_data[chat_id] = user_data
        save_history(history_data)
    except Exception as e:
        await update.message.reply_text(f"Error getting AI response: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for text messages."""
    await process_text_message(update, context)

async def on_startup(application: ApplicationBuilder):
    try:
        msg = await application.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="🟢 AI-PR-Analyzer Online with Web Search & Voice!")
        await asyncio.sleep(5)
        await application.bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=msg.message_id)
    except Exception as e:
        logger.error(f"Startup error: {e}")

def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_pr_check, CronTrigger(hour='7,13,19', minute=0))
    scheduler.start()
    
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(on_startup).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("clearall", clear_all_command))
    application.add_handler(CommandHandler("chat", chat_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    application.run_polling()

if __name__ == "__main__":
    main()
