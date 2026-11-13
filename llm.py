"""Обёртка над Gemini API.

Ключ берётся из переменной окружения GEMINI_API_KEY (в Streamlit Cloud её
задают в Secrets). Если ключа нет или модель не ответила, функции возвращают
None, и приложение продолжает работать без LLM-шагов.
"""

import os

import requests

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Сначала модель из настроек, потом запасные варианты на случай,
# если какую-то из них Google уже вывел из обращения.
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]

last_error = None


def _key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


def available():
    return bool(_key())


def _models():
    preferred = os.environ.get("GEMINI_MODEL", "").strip()
    models = [preferred] if preferred else []
    return models + [m for m in FALLBACK_MODELS if m != preferred]


def ask(prompt, temperature=0.2, json_mode=False):
    """Один вызов модели. Возвращает текст ответа или None."""
    global last_error
    if not available():
        return None

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

    for model in _models():
        try:
            resp = requests.post(
                API_URL.format(model=model),
                params={"key": _key()},
                json=body,
                timeout=60,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            continue
        if resp.status_code == 404:  # модели с таким именем нет — пробуем следующую
            last_error = f"{model}: 404"
            continue
        if resp.status_code != 200:
            last_error = f"{model}: HTTP {resp.status_code} {resp.text[:200]}"
            continue
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip()
        except (KeyError, IndexError, ValueError):
            last_error = f"{model}: пустой ответ"
            continue
    return None
