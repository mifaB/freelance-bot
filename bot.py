def analyze_with_gemini(text: str) -> dict | None:
    if not GEMINI_API_KEY:
        return None

    prompt = f"""Ты ассистент фриланс-видеомонтажёра и моушн-дизайнера Никиты (работает удалённо в DaVinci Resolve Studio, Premiere Pro, After Effects; умеет делать динамичные Reels/Shorts, длинные YouTube/обучающие видео, планарный трекинг экранов, интерактивную инфографику и AI-ролики).

Проанализируй пост из Telegram-канала и верни строгий JSON с полями:
- "is_job": true, если автор ИЩЕТ исполнителя на актуальный удалённый монтаж видео, Reels/Shorts, моушн-дизайн или AI-видео. Верни false, если это резюме/самореклама самого монтажёра, публикация готовых видео без монтажа, закрытая/неактуальная вакансия, оффлайн-съёмка или гибрид в офисе, вакансия не по видео или явный спам.
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

    # Собираем все доступные модели для ключа + резервный список
    candidate_models = []
    try:
        m_resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers=headers,
            timeout=(5, 10)
        )
        if m_resp.status_code == 200:
            for m in m_resp.json().get("models", []):
                name = m.get("name", "").replace("models/", "")
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods and "flash" in name.lower():
                    candidate_models.append(name)
    except Exception:
        pass

    for fallback in ["gemini-3.8-flash", "gemini-3-flash-preview", "gemini-2.5-flash-lite"]:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    for model in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=(5, 20))
            if r.status_code in (404, 429, 500, 503):
                print(f"[WARN] {model} недоступна (HTTP {r.status_code}), пробую следующую...")
                time.sleep(2)
                continue
            if r.status_code != 200:
                print(f"[WARN] Gemini ({model}) HTTP {r.status_code}: {r.text[:200]}")
                continue
            data = r.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(raw_text)
        except Exception as e:
            print(f"[WARN] Gemini ({model}) ошибка: {e}")
            time.sleep(1)

    print("[WARN] Все модели Gemini временно перегружены, отправляю базовую карточку")
    return None
