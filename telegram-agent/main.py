import os
import re
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, request, jsonify
from supabase import create_client

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
CRON_SECRET = os.environ["CRON_SECRET"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
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

AVAILABLE_MODELS = {
    "qwen": "qwen/qwen3.8-27b:free",
    "deepseek": "deepseek/deepseek-v4-flash-0731:free",
    "glm": "z-ai/glm-5.2:free",
    "gemma": "google/gemma-4-26b-a4b-it:free",
}
DEFAULT_MODEL_KEY = "deepseek"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=15)


def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json=payload, timeout=15)


def edit_message_reply_markup(chat_id, message_id, reply_markup):
    requests.post(
        f"{TELEGRAM_API}/editMessageReplyMarkup",
        json={"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        timeout=15,
    )


def build_model_keyboard(current_model):
    buttons = []
    for key, model_id in AVAILABLE_MODELS.items():
        label = f"{key} ✅" if model_id == current_model else key
        buttons.append([{"text": label, "callback_data": f"model:{key}"}])
    return {"inline_keyboard": buttons}


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


def get_model(chat_id):
    res = supabase.table("settings").select("model").eq("chat_id", chat_id).execute()
    if res.data:
        return res.data[0]["model"]
    return AVAILABLE_MODELS[DEFAULT_MODEL_KEY]


def set_model(chat_id, model_id):
    supabase.table("settings").upsert({"chat_id": chat_id, "model": model_id}).execute()


def ask_llm(chat_id, user_text):
    history = get_history(chat_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_text})

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={"model": get_model(chat_id), "messages": messages},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


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

    callback = update.get("callback_query")
    if callback:
        data = callback.get("data", "")
        chat_id = callback["message"]["chat"]["id"]
        message_id = callback["message"]["message_id"]
        if data.startswith("model:"):
            key = data.split(":", 1)[1]
            if key in AVAILABLE_MODELS:
                set_model(chat_id, AVAILABLE_MODELS[key])
                answer_callback_query(callback["id"], text=f"تم اختيار {key}")
                edit_message_reply_markup(chat_id, message_id, build_model_keyboard(AVAILABLE_MODELS[key]))
            else:
                answer_callback_query(callback["id"], text="خيار غير معروف")
        return jsonify(ok=True)

    message = update.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not chat_id or not text:
        return jsonify(ok=True)

    if text.startswith("/start"):
        send_message(chat_id, "أهلاً! تكلم معي عادي، أو استخدم:\n/remind 10m اشرب مويه\n/remind 2h اتصل بأحمد\n/model لتغيير الموديل")
    elif text.startswith("/help"):
        send_message(chat_id, "الصيغة: /remind <رقم><m أو h أو d> <النص>\nمثال: /remind 30m راجع الإيميل\n\n/model لعرض/تغيير الموديل")
    elif text.startswith("/model"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            current = get_model(chat_id)
            send_message(chat_id, "اختر الموديل:", reply_markup=build_model_keyboard(current))
        else:
            choice = parts[1].strip().lower()
            if choice not in AVAILABLE_MODELS:
                send_message(chat_id, "اسم غير معروف. اكتب /model لعرض القائمة.")
            else:
                set_model(chat_id, AVAILABLE_MODELS[choice])
                send_message(chat_id, f"تم تغيير الموديل إلى: {choice}")
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
        reply = ask_llm(chat_id, text)
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
