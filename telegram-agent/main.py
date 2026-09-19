import os
import re
from datetime import datetime, timedelta, timezone

import google.generativeai as genai
import requests
from flask import Flask, request, jsonify
from supabase import create_client

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
CRON_SECRET = os.environ["CRON_SECRET"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
HISTORY_LIMIT = 20
SYSTEM_PROMPT = (
    "أنت مساعد شخصي ذكي يتحدث العربية بشكل طبيعي ومختصر. "
    "استخدم سياق المحادثة السابقة للرد بما يناسب المستخدم."
)
REMINDER_RE = re.compile(r"^/remind\s+(\d+)\s*([mhd]?)\s+(.+)$", re.IGNORECASE | re.DOTALL)
UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "": 60}

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=SYSTEM_PROMPT)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)


def send_message(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)


def save_message(chat_id, role, content):
    supabase.table("messages").insert({"chat_id": chat_id, "role": role, "content": content}).execute()


def get_history(chat_id):
    res = (
        supabase.table("messages")
        .select("role, content")
        .eq("chat_id", chat_id)
        .order("created_at", desc=True)
        .limit(HISTORY_LIMIT)
        .execute()
    )
    return list(reversed(res.data))


def ask_gemini(chat_id, user_text):
    history = get_history(chat_id)
    gemini_history = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [m["content"]]}
        for m in history
    ]
    chat = model.start_chat(history=gemini_history)
    reply = chat.send_message(user_text)
    return reply.text


def parse_reminder(text):
    match = REMINDER_RE.match(text.strip())
    if not match:
        return None
    amount, unit, reminder_text = match.groups()
    seconds = int(amount) * UNIT_SECONDS[unit.lower()]
    return seconds, reminder_text.strip()


def create_reminder(chat_id, seconds_from_now, text):
    due_at = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    supabase.table("reminders").insert(
        {"chat_id": chat_id, "due_at": due_at.isoformat(), "text": text}
    ).execute()
    return due_at


@app.get("/")
def health():
    return "OK"


@app.post(f"/webhook/{TELEGRAM_WEBHOOK_SECRET}")
def webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not chat_id or not text:
        return jsonify(ok=True)

    if text.startswith("/start"):
        send_message(chat_id, "أهلاً! تكلم معي عادي، أو استخدم:\n/remind 10m اشرب مويه\n/remind 2h اتصل بأحمد")
    elif text.startswith("/help"):
        send_message(chat_id, "الصيغة: /remind <رقم><m أو h أو d> <النص>\nمثال: /remind 30m راجع الإيميل")
    elif text.startswith("/remind"):
        parsed = parse_reminder(text)
        if not parsed:
            send_message(chat_id, "الصيغة غلط. مثال: /remind 30m راجع الإيميل")
        else:
            seconds, reminder_text = parsed
            due_at = create_reminder(chat_id, seconds, reminder_text)
            send_message(chat_id, f"تمام، بذكّرك الساعة {due_at.strftime('%H:%M UTC')} بـ: {reminder_text}")
    else:
        save_message(chat_id, "user", text)
        reply = ask_gemini(chat_id, text)
        save_message(chat_id, "assistant", reply)
        send_message(chat_id, reply)

    return jsonify(ok=True)


@app.get("/reminders/check")
def check_reminders():
    if request.args.get("token") != CRON_SECRET:
        return jsonify(error="forbidden"), 403

    now = datetime.now(timezone.utc).isoformat()
    due = (
        supabase.table("reminders")
        .select("id, chat_id, text")
        .eq("sent", False)
        .lte("due_at", now)
        .execute()
    )

    for reminder in due.data:
        send_message(reminder["chat_id"], f"تذكير: {reminder['text']}")
        supabase.table("reminders").update({"sent": True}).eq("id", reminder["id"]).execute()

    return jsonify(sent=len(due.data))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
