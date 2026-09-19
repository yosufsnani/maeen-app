import os
import re
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, request, jsonify
from supabase import create_client

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
CRON_SECRET = os.environ["CRON_SECRET"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
HISTORY_LIMIT = 20
SYSTEM_PROMPT = (
    "أنت مساعد شخصي ذكي يتحدث العربية بشكل طبيعي ومختصر. "
    "استخدم سياق المحادثة السابقة للرد بما يناسب المستخدم. "
    "عندك أمر تذكير حقيقي شغّال بهذا البوت: لو المستخدم طلب منك تذكيره بشي، "
    "وجّهه يكتب رسالة بصيغة 'تذكير <رقم> <دقيقة/ساعة/يوم> <النص>'، "
    "مثال: 'تذكير 10 دقايق اشرب مويه'. لا تقل إنك غير قادر على التذكير."
)
REMINDER_RE_EN = re.compile(r"^/remind\s+(\d+)\s*([mhd]?)\s+(.+)$", re.IGNORECASE | re.DOTALL)
UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "": 60}

REMINDER_RE_AR = re.compile(
    r"^(?:تذكير|ذكرني)\s+(?:بعد\s+)?(\d+)\s+(دقيقة|دقيقه|دقائق|دقايق|ساعة|ساعه|ساعات|يوم|ايام|أيام)\s+(.+)$",
    re.DOTALL,
)
AR_UNIT_SECONDS = {
    "دقيقة": 60, "دقيقه": 60, "دقائق": 60, "دقايق": 60,
    "ساعة": 3600, "ساعه": 3600, "ساعات": 3600,
    "يوم": 86400, "ايام": 86400, "أيام": 86400,
}
START_WORDS = ("/start", "ابدأ", "بدء", "البداية")
HELP_WORDS = ("/help", "مساعدة", "المساعدة", "الأوامر")
MODEL_WORDS = ("/model", "الموديل", "موديل")
REMIND_TRIGGER_WORDS = ("/remind", "تذكير", "ذكرني")

AVAILABLE_MODELS = {
    "gemini": {"provider": "gemini", "id": "gemini-2.0-flash"},
    "gptoss": {"provider": "groq", "id": "openai/gpt-oss-120b"},
}
DEFAULT_MODEL_KEY = "gemini"

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


def build_model_keyboard(current_key):
    buttons = []
    for key in AVAILABLE_MODELS:
        label = f"{key} ✅" if key == current_key else key
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


def get_model_key(chat_id):
    res = supabase.table("settings").select("model").eq("chat_id", chat_id).execute()
    if res.data and res.data[0]["model"] in AVAILABLE_MODELS:
        return res.data[0]["model"]
    return DEFAULT_MODEL_KEY


def set_model(chat_id, key):
    supabase.table("settings").upsert({"chat_id": chat_id, "model": key}).execute()


def ask_groq(chat_id, user_text, model_id):
    history = get_history(chat_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_text})

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={"model": model_id, "messages": messages},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def ask_gemini(chat_id, user_text, model_id):
    history = get_history(chat_id)
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in history
    ]
    contents.append({"role": "user", "parts": [{"text": user_text}]})

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
        params={"key": GEMINI_API_KEY},
        json={"contents": contents, "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]}},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def ask_llm(chat_id, user_text):
    info = AVAILABLE_MODELS[get_model_key(chat_id)]
    if info["provider"] == "gemini":
        return ask_gemini(chat_id, user_text, info["id"])
    return ask_groq(chat_id, user_text, info["id"])


def parse_reminder(text):
    t = text.strip()
    match = REMINDER_RE_EN.match(t)
    if match:
        amount, unit, reminder_text = match.groups()
        seconds = int(amount) * UNIT_SECONDS[unit.lower()]
        return seconds, reminder_text.strip()
    match = REMINDER_RE_AR.match(t)
    if match:
        amount, unit_word, reminder_text = match.groups()
        seconds = int(amount) * AR_UNIT_SECONDS[unit_word]
        return seconds, reminder_text.strip()
    return None


def strip_prefix(text, prefixes):
    t = text.strip()
    for prefix in prefixes:
        if t.startswith(prefix):
            return t[len(prefix):].strip()
    return ""


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
                set_model(chat_id, key)
                answer_callback_query(callback["id"], text=f"تم اختيار {key}")
                edit_message_reply_markup(chat_id, message_id, build_model_keyboard(key))
            else:
                answer_callback_query(callback["id"], text="خيار غير معروف")
        return jsonify(ok=True)

    message = update.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not chat_id or not text:
        return jsonify(ok=True)

    stripped = text.strip()
    parsed_reminder = parse_reminder(text)

    if stripped.startswith(START_WORDS):
        send_message(
            chat_id,
            "أهلاً! تكلم معي عادي، أو استخدم:\n"
            "تذكير 10 دقايق اشرب مويه\n"
            "تذكير 2 ساعة اتصل بأحمد\n"
            "الموديل — لتغيير الموديل",
        )
    elif stripped.startswith(HELP_WORDS):
        send_message(
            chat_id,
            "الصيغة: تذكير <رقم> <دقيقة/ساعة/يوم> <النص>\n"
            "مثال: تذكير 30 دقيقة راجع الإيميل\n\n"
            "الموديل — لعرض/تغيير الموديل",
        )
    elif stripped.startswith(MODEL_WORDS):
        rest = strip_prefix(stripped, MODEL_WORDS)
        if not rest:
            current = get_model_key(chat_id)
            send_message(chat_id, "اختر الموديل:", reply_markup=build_model_keyboard(current))
        else:
            choice = rest.lower()
            if choice not in AVAILABLE_MODELS:
                send_message(chat_id, "اسم غير معروف. اكتب الموديل لعرض القائمة.")
            else:
                set_model(chat_id, choice)
                send_message(chat_id, f"تم تغيير الموديل إلى: {choice}")
    elif parsed_reminder is not None:
        seconds, reminder_text = parsed_reminder
        due_at = create_reminder(chat_id, seconds, reminder_text)
        send_message(chat_id, f"تمام، بذكّرك الساعة {due_at.strftime('%H:%M UTC')} بـ: {reminder_text}")
    elif stripped.startswith(REMIND_TRIGGER_WORDS):
        send_message(chat_id, "الصيغة غلط. مثال: تذكير 30 دقيقة راجع الإيميل")
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
