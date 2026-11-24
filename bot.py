import html
import json
import os
import re
import socket
import time
from pathlib import Path
from bs4 import BeautifulSoup
import requests
import urllib3.util.connection as urllib3_cn

# Принудительно используем IPv4, чтобы запросы с GitHub к Telegram не зависали
try:
    urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
CHAT_ID = os.environ["CHAT_ID"].strip()

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
    "выездная съемка", "выездная съёмка", "работа в офисе", "в офис",
    "съёмка в москве", "съемка в москве", "выезд по москве",
    "на постоянной основе в офис", "оператор-постановщик", "оплата звездами",
    "звёздами", "200 роликов"
]
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


def load_seen() -> tuple[list[str], bool]:
    if not SEEN_FILE.exists():
        return [], True
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8")), False
    except Exception:
        return [], True


def save_seen(seen: list[str]) -> None:
    SEEN_FILE.write_text(
        json.dumps(seen[-3000:], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def fetch_channel_posts(channel: str) -> list[dict]:
    url = f"https://t.me/s/{channel}"
    resp = requests.get(url, headers=HEADERS, timeout=25)
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

        text = text_el.get_text("\n", strip=True)
        links = [a.get("href", "") for a in text_el.select("a") if a.get("href")]

        posts.append({
            "id": post_id,
            "channel": channel,
            "url": f"https://t.me/{post_id}",
            "text": text,
            "links": links
        })

    return posts


def is_relevant(text: str) -> bool:
    low = text.lower()
    if any(bad in low for bad in EXCLUDE_KEYWORDS):
        return False
    return any(good in low for good in INCLUDE_KEYWORDS)


def detect_category(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ["reels", "рилс", "shorts", "шортс", "tiktok", "тикток", "вертикальн"]):
        return "Монтаж | Вертикальные видео"
    if any(w in low for w in ["youtube", "ютуб", "подкаст", "интервью", "урок", "лекци", "курс"]):
        return "Монтаж | YouTube и длинные видео"
    if any(w in low for w in ["after effects", "моушн", "трекинг", "инфографик", "vfx", "3d"]):
        return "Монтаж | Графика и VFX"
    return "Монтаж | Видеопродакшн"


def extract_budget(text: str) -> str:
    pattern = re.compile(
        r"((?:от\s+|до\s+)?\d[\d\s.,]{0,12}(?:\s*[-–—]\s*\d[\d\s.,]{0,12})?\s*(?:₽|руб(?:лей|ля|\.)?|тыс\.?|\$|usd|€|к\b|k\b))",
        re.IGNORECASE
    )
    match = pattern.search(text)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return "не указан"


def extract_test_task(text: str) -> str:
    low = text.lower()
    if "тест" not in low:
        return "не указано"
    if any(w in low for w in ["бесплатное тест", "тестовое не оплач", "неоплачиваемое тест"]):
        return "не оплачивается"
    if any(w in low for w in ["оплачиваемое тест", "тест оплач", "тестовое оплач"]):
        return "оплачивается"
    return "есть (уточнить условия)"


def extract_portfolio(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ["портфолио", "примеры работ", "пример работ", "кейсы", "шоурил"]):
        return "нужно"
    return "не указано"


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


def send_card(post: dict) -> bool:
    text = post["text"]
    category = detect_category(text)
    budget = extract_budget(text)
    test_task = extract_test_task(text)
    portfolio = extract_portfolio(text)
    contact = extract_contact(text, post["links"], post["channel"])

    snippet = text if len(text) <= 420 else text[:417].rstrip() + "..."
    safe_snippet = html.escape(snippet)
    contact_str = contact if contact else "см. в посте"

    message = (
        f"🎬 <b>{category}</b>\n\n"
        f"<b>Бюджет:</b> {html.escape(budget)}\n"
        f"<b>Тестовое:</b> {test_task}\n"
        f"<b>Портфолио:</b> {portfolio}\n"
        f"<b>Контакт:</b> {contact_str}\n\n"
        f"<b>Текст:</b>\n{safe_snippet}\n\n"
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
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                return True
            print(f"[ERR] Telegram API (попытка {attempt}): {r.status_code} {r.text}")
        except requests.RequestException as e:
            print(f"[WARN] Сеть Telegram (попытка {attempt}/3): {e}")
        time.sleep(3 * attempt)

    return False


def main() -> None:
    channels = load_channels()
    seen_list, is_first_run = load_seen()
    seen_set = set(seen_list)

    new_relevant = []
    ids_to_save = []

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
            if is_relevant(post["text"]):
                new_relevant.append(post)
            else:
                ids_to_save.append(post["id"])

        time.sleep(1)

    if is_first_run and len(new_relevant) > 5:
        for skipped in new_relevant[:-5]:
            ids_to_save.append(skipped["id"])
        to_send = new_relevant[-5:]
    else:
        to_send = new_relevant

    sent_count = 0
    for post in to_send:
        if send_card(post):
            ids_to_save.append(post["id"])
            sent_count += 1
        time.sleep(1)

    if ids_to_save:
        seen_list.extend(ids_to_save)
        save_seen(seen_list)

    print(f"Проверено каналов: {len(channels)} | Подходящих: {len(new_relevant)} | Отправлено: {sent_count}")


if __name__ == "__main__":
    main()
