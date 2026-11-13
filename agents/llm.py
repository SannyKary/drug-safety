"""Обёртка над Gemini API.

Ключ берётся из переменной окружения GEMINI_API_KEY (в Streamlit Cloud её
задают в Secrets). Если ключа нет или модель не ответила, функции возвращают
None, и приложение продолжает работать без LLM-шагов.

Названия моделей Google регулярно меняет, поэтому модель не зашита в код:
при первом вызове мы спрашиваем у API список доступных моделей и берём
самую новую «flash» (быстрая и бесплатная).
"""

import os
import re

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"

last_error = None
_chosen_model = None


def _key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


def available():
    return bool(_key())


def _version(name):
    """'gemini-3.5-flash' → (3, 5); у кого номер больше, тот новее."""
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def pick_model(names):
    """Из списка моделей выбирает самую новую обычную flash-модель."""
    skip = ("lite", "image", "tts", "audio", "live", "embedding", "vision", "thinking", "exp")
    flash = [n for n in names if "flash" in n and not any(s in n for s in skip)]
    if not flash:
        flash = [n for n in names if "flash" in n] or names
    # Стабильные версии (без preview) предпочтительнее, затем — самая новая
    return max(flash, key=lambda n: ("preview" not in n, _version(n), "latest" not in n))


def model_name():
    """Название модели: из настроек или найденное через список моделей."""
    global _chosen_model, last_error
    if os.environ.get("GEMINI_MODEL", "").strip():
        return os.environ["GEMINI_MODEL"].strip()
    if _chosen_model:
        return _chosen_model
    try:
        resp = requests.get(f"{BASE}/models", params={"key": _key(), "pageSize": 1000}, timeout=20)
        resp.raise_for_status()
        names = [
            m["name"].split("/", 1)[-1]
            for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
    except (requests.RequestException, ValueError) as exc:
        last_error = f"список моделей не получен: {exc}"
        return "gemini-flash-latest"
    if not names:
        last_error = "API не вернул ни одной модели (проверьте ключ)"
        return "gemini-flash-latest"
    _chosen_model = pick_model(names)
    return _chosen_model


def ask(prompt, temperature=0.2, json_mode=False):
    """Один вызов модели. Возвращает текст ответа или None."""
    global last_error, _chosen_model
    if not available():
        return None

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

    for attempt in range(2):
        model = model_name()
        try:
            resp = requests.post(
                f"{BASE}/models/{model}:generateContent",
                params={"key": _key()},
                json=body,
                timeout=90,
            )
        except requests.RequestException as exc:
            last_error = f"{model}: {exc}"
            continue
        if resp.status_code == 200:
            try:
                parts = resp.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            except (KeyError, IndexError, ValueError):
                last_error = f"{model}: пустой ответ"
                return None
        last_error = f"{model}: HTTP {resp.status_code} {resp.text[:200]}"
        if resp.status_code == 404:
            _chosen_model = None  # модель пропала — со второй попытки выберем заново
            continue
        if resp.status_code != 429 and resp.status_code < 500:
            return None
    return None
