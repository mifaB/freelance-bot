from datetime import datetime, timedelta, timezone
import html
import json
import os
import re
import socket
import time
from pathlib import Path
from bs4 import BeautifulSoup
import requests

# Жёстко используем IPv4, чтобы запросы с GitHub к Telegram не зависали
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in res if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else res
socket.getaddrinfo = _ipv4_getaddrinfo

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
CHAT_ID = os.environ["CHAT_ID"].strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

PORTFOLIO_URL = "https://drive.google.com/drive/folders/1U95AXfkSEJVsAl4p5Rr3-AHVvGMjSIH5?usp=drive_link"
MAX_POST_AGE_HOURS = 48  # Игнорировать посты старше 48 часов

CHANNELS_FILE = Path("channels.txt")
SEEN_FILE = Path("seen_posts.json")

INCLUDE_KEYWORDS = [
    "монтаж", "монтажер", "монтажёр", "reels", "рилс", "shorts", "шортс",
    "davinci", "давинчи", "premiere", "after effects", "видеодизайн",
    "видеомонтаж", "роликов", "видеоролик", "вертикальн", "ютуб", "youtube",
    "тикток", "tiktok", "моушн", "трекинг", "субтитр"
]

EXCLUDE_KEYWORDS = [
    "ищу работу", "ищу заказ", "возьму заказы", "возьму в работу", "возьму проекты",
    "смонтирую для вас", "нарежу для вас", "сделаю для вас", "помогу вам с монтажом",
    "моё портфолио", "мое портфолио", "мои работы", "мои кейсы", "примеры моих работ",
    "предлагаю услуги", "оказываю услуги", "услуги монтажера", "услуги монтажёра",
    "услуги видеомонтажера", "услуги видеомонтажёра", "мой прайс", "прайс-лист",
    "#резюме", "моё резюме", "мое резюме", "меня зовут", "я монтажер", "я монтажёр",
    "я видеомонтажер", "я видеомонтажёр", "я рилсмейкер", "монтирую в ", "работаю в ",
    "владею программами", "свободен для заказов", "свободна для заказов",
    "открыт к предложениям", "открыта к предложениям", "ищу клиентов",
    "выездная съемка", "выездная съёмка", "работа в офисе", "в офис", "офис в москве",
    "гибрид", "съёмка в москве", "съемка в москве", "выезд по москве",
    "на постоянной основе в офис", "оператор-постановщик", "оплата звездами",
    "звёздами", "200 роликов", "вакансия закрыта", "исполнитель найден", "не актуально"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def load_channels() -> list[str]:
    if not CHANNELS_FILE.exists():
        return []
    return [
        line.strip().lstrip("@")
        for line in CHANNELS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def load_seen() -> list[str]:
    if not SEEN_FILE.exists():
        return []
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_seen(seen: list[str]) -> None:
    SEEN_FILE.write_text(
        json.dumps(seen[-3000:], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def fetch_channel_posts(channel: str) -> list[dict]:
    url = f"https://t.me/s/{channel}"
    resp = requests.get(url, headers=HEADERS, timeout=(5, 15))
    if resp.status_code != 200:
        print(f"[WARN] {channel}: HTTP {resp.status_code}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    posts = []

    for msg in soup.select(".tgme_widget_message"):
        post_id = msg.get("data-post")
        text_el = msg.select_one(".tgme_widget_message_text")
        if not post_id or not text_el:
            continue

        time_el = msg.select_one("time.time")
        dt_str = time_el.get("datetime") if time_el else None
        post_dt = None
        if dt_str:
            try:
                post_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except ValueError:
                post_dt = None

        text = text_el.get_text("\n", strip=True)
        links = [a.get("href", "") for a in text_el.select("a") if a.get("href")]

        posts.append({
            "id": post_id,
            "channel": channel,
            "url": f"https://t.me/{post_id}",
            "text": text,
            "links": links,
            "dt": post_dt
        })

    return posts


def is_relevant_basic(text: str) -> bool:
    low = text.lower()
    if any(bad in low for bad in EXCLUDE_KEYWORDS):
        return False
    return any(good in low for good in INCLUDE_KEYWORDS)


def analyze_with_gemini(text: str) -> dict | None:
    if not GEMINI_API_KEY:
        return None

    prompt = f"""Ты ассистент фриланс-видеомонтажёра и моушн-дизайнера Никиты (работает удалённо в DaVinci Resolve Studio, Premiere Pro, After Effects; умеет делать динамичные Reels/Shorts, длинные YouTube/обучающие видео, планарный трекинг экранов, интерактивную инфографику и AI-ролики).

Проанализируй пост из Telegram-канала и верни строгий JSON с полями:
- "is_job": true, если автор ИЩЕТ исполнителя на актуальный удалённый монтаж видео, Reels/Shorts, моушн-дизайн или AI-видео. Верни false, если это резюме/самореклама самого монтажёра, закрытая/неактуальная вакансия, оффлайн-съёмка или гибрид в офисе, вакансия не по видео или явный спам.
- "category": короткая категория (например: "Вертикальные видео (Reels/Shorts)", "YouTube / Длинные видео", "Моушн / Графика / VFX", "AI-видео").
- "budget": точный бюджет из текста или "не указан".
- "summary": суть задачи в 1-2 коротких предложениях без воды.
- "pitch": готовый короткий отклик в ЛС заказчику (3-4 предложения, живой деловой тон без канцелярита и пафоса). В отклике:
  1) если в посте есть кодовое слово или конкретный вопрос — ответь на него в первой же фразе;
  2) покажи понимание именно этой задачи и релевантный софт;
  3) вставь ссылку на портфолио: {PORTFOLIO_URL}
  4) предложи обсудить детали или сделать короткий тестовый фрагмент.

Текст поста:
{text[:2500]}"""

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.3
        }
    }

    models = ["gemini-3.8-flash", "gemini-3-flash-preview", "gemini-2.5-flash-lite"]
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=(5, 20))
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                print(f"[WARN] Gemini ({model}) HTTP {r.status_code}: {r.text[:200]}")
                return None
            data = r.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(raw_text)
        except Exception as e:
            print(f"[WARN] Gemini ({model}) ошибка: {e}")
            return None

    print("[WARN] Ни одна из моделей Gemini не ответила")
    return None


def detect_category(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ["reels", "рилс", "shorts", "шортс", "tiktok", "тикток", "вертикальн"]):
        return "Вертикальные видео"
    if any(w in low for w in ["youtube", "ютуб", "подкаст", "интервью", "урок", "лекци", "курс"]):
        return "YouTube и длинные видео"
    if any(w in low for w in ["after effects", "моушн", "трекинг", "инфографик", "vfx", "3d"]):
        return "Графика и VFX"
    return "Видеопродакшн"


def extract_budget(text: str) -> str:
    pattern = re.compile(
        r"((?:от\s+|до\s+)?\d[\d\s.,]{0,12}(?:\s*[-–—]\s*\d[\d\s.,]{0,12})?\s*(?:₽|руб(?:лей|ля|\.)?|тыс\.?|\$|usd|€|к\b|k\b))",
        re.IGNORECASE
    )
    match = pattern.search(text)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return "не указан"


def extract_contact(text: str, links: list[str], channel: str) -> str | None:
    ignore = {channel.lower(), "botfather", "share"}

    for user in re.findall(r"@([a-zA-Z0-9_]{4,32})", text):
        if user.lower() not in ignore and not user.lower().endswith("bot"):
            return f"@{user}"

    all_links = " ".join(links) + " " + text
    for user in re.findall(r"t\.me/([a-zA-Z0-9_]{4,32})", all_links):
        if user.lower() not in ignore and user.lower() != "s" and not user.lower().endswith("bot"):
            return f"@{user}"

    return None


def format_post_time(post_dt: datetime | None) -> str:
    if not post_dt:
        return "неизвестно"
    now_utc = datetime.now(timezone.utc)
    diff_min = max(0, int((now_utc - post_dt).total_seconds() // 60))
    if diff_min < 60:
        ago = f"{diff_min} мин. назад"
    else:
        ago = f"{diff_min // 60} ч. назад"
    msk_dt = post_dt + timedelta(hours=3)
    return f"{msk_dt.strftime('%d.%m в %H:%M')} МСК ({ago})"


def send_card(post: dict, ai: dict | None) -> bool:
    text = post["text"]
    contact = extract_contact(text, post["links"], post["channel"])
    contact_str = contact if contact else "см. в посте"
    time_str = format_post_time(post.get("dt"))

    if ai:
        category = html.escape(str(ai.get("category") or detect_category(text)))
        budget = html.escape(str(ai.get("budget") or extract_budget(text)))
        summary = html.escape(str(ai.get("summary") or ""))
        pitch = html.escape(str(ai.get("pitch") or ""))

        message = (
            f"🎬 <b>Монтаж | {category}</b>\n\n"
            f"🕒 <b>Опубликовано:</b> {time_str}\n"
            f"💰 <b>Бюджет:</b> {budget}\n"
            f"👤 <b>Контакт:</b> {contact_str}\n"
            f"📌 <b>Суть:</b> {summary}\n\n"
            f"<b>Готовый отклик (нажми, чтобы скопировать):</b>\n"
            f"<code>{pitch}</code>\n\n"
            f"<i>Источник: @{post['channel']}</i>"
        )
    else:
        category = detect_category(text)
        budget = extract_budget(text)
        snippet = text if len(text) <= 420 else text[:417].rstrip() + "..."
        message = (
            f"🎬 <b>Монтаж | {category}</b>\n\n"
            f"🕒 <b>Опубликовано:</b> {time_str}\n"
            f"💰 <b>Бюджет:</b> {html.escape(budget)}\n"
            f"👤 <b>Контакт:</b> {contact_str}\n\n"
            f"<b>Текст:</b>\n{html.escape(snippet)}\n\n"
            f"<i>Источник: @{post['channel']}</i>"
        )

    buttons = []
    if contact:
        buttons.append({"text": f"💬 Написать {contact}", "url": f"https://t.me/{contact.lstrip('@')}"})
    buttons.append({"text": "🔗 Открыть пост", "url": post["url"]})

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": [buttons]}
    }

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=(5, 10))
            if r.status_code == 200:
                return True
            print(f"[ERR] Telegram API (попытка {attempt}): {r.status_code} {r.text}")
        except requests.RequestException as e:
            print(f"[WARN] Сеть Telegram (попытка {attempt}/3): {e}")
        time.sleep(2 * attempt)

    return False


def main() -> None:
    channels = load_channels()
    seen_list = load_seen()
    seen_set = set(seen_list)
    now_utc = datetime.now(timezone.utc)

    candidates = []
    ids_to_save = []
    old_skipped = 0

    for channel in channels:
        try:
            posts = fetch_channel_posts(channel)
        except Exception as e:
            print(f"[ERR] {channel}: {e}")
            continue

        for post in posts:
            if post["id"] in seen_set:
                continue
            seen_set.add(post["id"])

            if post["dt"] is not None:
                age_hours = (now_utc - post["dt"]).total_seconds() / 3600
                if age_hours > MAX_POST_AGE_HOURS:
                    ids_to_save.append(post["id"])
                    old_skipped += 1
                    continue

            if is_relevant_basic(post["text"]):
                candidates.append(post)
            else:
                ids_to_save.append(post["id"])

        time.sleep(0.5)

    if len(candidates) > 8:
        for skipped in candidates[:-8]:
            ids_to_save.append(skipped["id"])
        candidates = candidates[-8:]

    sent_count = 0
    for post in candidates:
        ai_result = analyze_with_gemini(post["text"])
        if ai_result is not None and not ai_result.get("is_job", True):
            print(f"[AI SKIP] Отсеян мусор/резюме из @{post['channel']}")
            ids_to_save.append(post["id"])
            continue

        if send_card(post, ai_result):
            ids_to_save.append(post["id"])
            sent_count += 1

        time.sleep(2)

    if ids_to_save:
        seen_list.extend(ids_to_save)
        save_seen(seen_list)

    print(f"Каналов: {len(channels)} | Старых отсеяно: {old_skipped} | Кандидатов: {len(candidates)} | Отправлено: {sent_count}")


if __name__ == "__main__":
    main()
