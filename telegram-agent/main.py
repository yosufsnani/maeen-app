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
TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"
HISTORY_LIMIT = 20
SYSTEM_PROMPT = (
    "أنت مساعد شخصي ذكي يتحدث العربية بشكل طبيعي ومختصر. "
    "استخدم سياق المحادثة السابقة للرد بما يناسب المستخدم. "
    "عندك أمر تذكير حقيقي شغّال بهذا البوت: لو المستخدم طلب منك تذكيره بشي، "
    "وجّهه يكتب رسالة بصيغة 'تذكير <رقم> <دقيقة/ساعة/يوم> <النص>'، "
    "أو للتكرار 'تذكير كل يوم <النص>'. "
    "وعنده أوامر لحفظ الملاحظات (ملاحظة <نص>) والمهام (مهمة <نص>). "
    "لا تقل إنك غير قادر على هذي الأشياء."
)

REMINDER_RE_EN = re.compile(r"^/remind\s+(\d+)\s*([mhd]?)\s+(.+)$", re.IGNORECASE | re.DOTALL)
UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "": 60}

REMINDER_RE_AR = re.compile(
    r"^(?:تذكير|ذكرني)\s+(?:بعد\s+)?(\d+)\s+(دقيقة|دقيقه|دقائق|دقايق|ساعة|ساعه|ساعات|يوم|ايام|أيام)\s+(.+)$",
    re.DOTALL,
)
REMINDER_REPEAT_RE = re.compile(
    r"^(?:تذكير|ذكرني)\s+كل\s+(\d+)?\s*(دقيقة|دقيقه|دقائق|دقايق|ساعة|ساعه|ساعات|يوم|ايام|أيام)\s+(.+)$",
    re.DOTALL,
)
AR_UNIT_SECONDS = {
    "دقيقة": 60, "دقيقه": 60, "دقائق": 60, "دقايق": 60,
    "ساعة": 3600, "ساعه": 3600, "ساعات": 3600,
    "يوم": 86400, "ايام": 86400, "أيام": 86400,
}
TASK_DONE_RE = re.compile(r"^تم\s+(\d+)$")


def normalize_arabic(text):
    return (
        text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        .replace("ى", "ي").replace("ة", "ه")
    )


START_WORDS = tuple(normalize_arabic(w) for w in ("/start", "ابدأ", "بدء", "البداية"))
HELP_WORDS = tuple(normalize_arabic(w) for w in ("/help", "مساعدة", "المساعدة", "الأوامر"))
MODEL_WORDS = tuple(normalize_arabic(w) for w in ("/model", "الموديل", "موديل"))
REMIND_TRIGGER_WORDS = tuple(normalize_arabic(w) for w in ("/remind", "تذكير", "ذكرني"))
NOTE_WORDS = tuple(normalize_arabic(w) for w in ("ملاحظة",))
NOTES_LIST_WORDS = tuple(normalize_arabic(w) for w in ("ملاحظاتي", "الملاحظات"))
TASK_WORDS = tuple(normalize_arabic(w) for w in ("مهمة",))
TASKS_LIST_WORDS = tuple(normalize_arabic(w) for w in ("مهامي", "المهام"))

AVAILABLE_MODELS = {
    "gptoss": {"provider": "groq", "id": "openai/gpt-oss-120b"},
    "gemini": {"provider": "gemini", "id": "gemini-3.5-flash-lite"},
}
DEFAULT_MODEL_KEY = "gptoss"

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


def save_note(chat_id, content):
    supabase.table("notes").insert({"chat_id": chat_id, "content": content}).execute()


def get_notes(chat_id):
    res = (
        supabase.table("notes")
        .select("content")
        .eq("chat_id", chat_id)
        .order("created_at")
        .execute()
    )
    return [n["content"] for n in res.data]


def add_task(chat_id, content):
    supabase.table("tasks").insert({"chat_id": chat_id, "content": content}).execute()


def get_open_tasks(chat_id):
    res = (
        supabase.table("tasks")
        .select("id, content")
        .eq("chat_id", chat_id)
        .eq("done", False)
        .order("created_at")
        .execute()
    )
    return res.data


def complete_task(chat_id, task_number):
    tasks = get_open_tasks(chat_id)
    if 1 <= task_number <= len(tasks):
        supabase.table("tasks").update({"done": True}).eq("id", tasks[task_number - 1]["id"]).execute()
        return True
    return False


def build_system_prompt(chat_id):
    notes = get_notes(chat_id)
    if not notes:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + "\n\nمعلومات محفوظة عن المستخدم:\n" + "\n".join(f"- {n}" for n in notes)


def ask_groq(chat_id, user_text, model_id, system_prompt):
    history = get_history(chat_id)
    messages = [{"role": "system", "content": system_prompt}]
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


def ask_gemini(chat_id, user_text, model_id, system_prompt):
    history = get_history(chat_id)
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in history
    ]
    contents.append({"role": "user", "parts": [{"text": user_text}]})

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
        params={"key": GEMINI_API_KEY},
        json={"contents": contents, "systemInstruction": {"parts": [{"text": system_prompt}]}},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def ask_llm(chat_id, user_text):
    system_prompt = build_system_prompt(chat_id)
    info = AVAILABLE_MODELS[get_model_key(chat_id)]
    if info["provider"] == "gemini":
        return ask_gemini(chat_id, user_text, info["id"], system_prompt)
    return ask_groq(chat_id, user_text, info["id"], system_prompt)


def transcribe_voice(file_id):
    file_resp = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=15)
    file_resp.raise_for_status()
    file_path = file_resp.json()["result"]["file_path"]

    audio_resp = requests.get(f"{TELEGRAM_FILE_API}/{file_path}", timeout=30)
    audio_resp.raise_for_status()

    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": ("voice.ogg", audio_resp.content, "audio/ogg")},
        data={"model": "whisper-large-v3", "language": "ar"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


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


def parse_recurring_reminder(text):
    match = REMINDER_REPEAT_RE.match(text.strip())
    if not match:
        return None
    amount, unit_word, reminder_text = match.groups()
    n = int(amount) if amount else 1
    seconds = n * AR_UNIT_SECONDS[unit_word]
    return seconds, reminder_text.strip()


def strip_prefix(text, prefixes):
    t = text.strip()
    for prefix in prefixes:
        if t.startswith(prefix):
            return t[len(prefix):].strip()
    return ""


def strip_prefix_original(original, normalized, prefixes):
    for prefix in prefixes:
        if normalized.startswith(prefix):
            return original[len(prefix):].strip()
    return ""


def format_repeat_interval(seconds):
    if seconds % 86400 == 0:
        n = seconds // 86400
        return f"كل {n} يوم" if n == 1 else f"كل {n} أيام"
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"كل {n} ساعة" if n == 1 else f"كل {n} ساعات"
    n = max(seconds // 60, 1)
    return f"كل {n} دقيقة" if n == 1 else f"كل {n} دقائق"


def create_reminder(chat_id, seconds_from_now, text, repeat_seconds=None):
    due_at = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    supabase.table("reminders").insert(
        {"chat_id": chat_id, "due_at": due_at.isoformat(), "text": text, "repeat_seconds": repeat_seconds}
    ).execute()
    return due_at


def handle_text(chat_id, text):
    original_stripped = text.strip()
    stripped = normalize_arabic(original_stripped)
    parsed_reminder = parse_reminder(text)
    parsed_recurring = parse_recurring_reminder(text)
    task_done_match = TASK_DONE_RE.match(stripped)

    if stripped.startswith(START_WORDS):
        send_message(
            chat_id,
            "أهلاً! تكلم معي عادي، أو استخدم:\n"
            "تذكير 10 دقايق اشرب مويه\n"
            "تذكير كل يوم اتصل بأحمد\n"
            "ملاحظة <نص> — لحفظ معلومة\n"
            "مهمة <نص> — لإضافة مهمة\n"
            "الموديل — لتغيير الموديل",
        )
    elif stripped.startswith(HELP_WORDS):
        send_message(
            chat_id,
            "تذكير <رقم> <دقيقة/ساعة/يوم> <النص> — تذكير لمرة وحدة\n"
            "تذكير كل <دقيقة/ساعة/يوم> <النص> — تذكير متكرر\n"
            "ملاحظة <نص> / ملاحظاتي\n"
            "مهمة <نص> / مهامي / تم <رقم>\n"
            "الموديل — لعرض/تغيير الموديل\n"
            "تقدر كمان ترسل رسالة صوتية",
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
    elif stripped.startswith(NOTES_LIST_WORDS):
        notes = get_notes(chat_id)
        if not notes:
            send_message(chat_id, "ما فيه ملاحظات محفوظة")
        else:
            send_message(chat_id, "\n".join(f"{i + 1}. {n}" for i, n in enumerate(notes)))
    elif stripped.startswith(NOTE_WORDS):
        content = strip_prefix_original(original_stripped, stripped, NOTE_WORDS)
        if not content:
            send_message(chat_id, "اكتب: ملاحظة <النص>")
        else:
            save_note(chat_id, content)
            send_message(chat_id, "تم الحفظ ✅")
    elif stripped.startswith(TASKS_LIST_WORDS):
        tasks = get_open_tasks(chat_id)
        if not tasks:
            send_message(chat_id, "ما فيه مهام مفتوحة")
        else:
            lines = [f"{i + 1}. {t['content']}" for i, t in enumerate(tasks)]
            send_message(chat_id, "\n".join(lines) + "\n\nاكتب: تم <رقم> لتأشير مهمة كمكتملة")
    elif task_done_match:
        if complete_task(chat_id, int(task_done_match.group(1))):
            send_message(chat_id, "تمام، تم تحديدها كمكتملة ✅")
        else:
            send_message(chat_id, "رقم غير صحيح")
    elif stripped.startswith(TASK_WORDS):
        content = strip_prefix_original(original_stripped, stripped, TASK_WORDS)
        if not content:
            send_message(chat_id, "اكتب: مهمة <النص>")
        else:
            add_task(chat_id, content)
            send_message(chat_id, "تمت الإضافة ✅")
    elif parsed_recurring is not None:
        seconds, reminder_text = parsed_recurring
        due_at = create_reminder(chat_id, seconds, reminder_text, repeat_seconds=seconds)
        send_message(chat_id, f"تمام، بذكّرك {format_repeat_interval(seconds)} بـ: {reminder_text}")
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

    if not chat_id:
        return jsonify(ok=True)

    text = message.get("text")
    voice = message.get("voice")

    if not text and voice:
        text = transcribe_voice(voice["file_id"])
        if not text:
            send_message(chat_id, "تعذر فهم الرسالة الصوتية")
            return jsonify(ok=True)

    if not text:
        return jsonify(ok=True)

    handle_text(chat_id, text)
    return jsonify(ok=True)


@app.get("/reminders/check")
def check_reminders():
    if request.args.get("token") != CRON_SECRET:
        return jsonify(error="forbidden"), 403

    now = datetime.now(timezone.utc).isoformat()
    due = (
        supabase.table("reminders")
        .select("id, chat_id, text, repeat_seconds")
        .eq("sent", False)
        .lte("due_at", now)
        .execute()
    )

    for reminder in due.data:
        send_message(reminder["chat_id"], f"تذكير: {reminder['text']}")
        if reminder.get("repeat_seconds"):
            new_due = datetime.now(timezone.utc) + timedelta(seconds=reminder["repeat_seconds"])
            supabase.table("reminders").update({"due_at": new_due.isoformat()}).eq("id", reminder["id"]).execute()
        else:
            supabase.table("reminders").update({"sent": True}).eq("id", reminder["id"]).execute()

    return jsonify(sent=len(due.data))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
