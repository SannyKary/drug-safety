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
import time

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


def rank_models(names):
    """Сортирует модели: сначала обычные flash (стабильные, самые новые),
    потом flash-lite, потом остальные. Первая в списке — основная,
    остальные — запасные на случай перегрузки."""
    skip = ("image", "tts", "audio", "live", "embedding", "vision", "thinking", "exp", "robotics", "computer")
    usable = [n for n in names if n.startswith("gemini") and not any(s in n for s in skip)]

    def key(n):
        tier = 0 if ("flash" in n and "lite" not in n) else 1 if "flash" in n else 2
        return (tier, "preview" in n, tuple(-x for x in _version(n)), "latest" in n)

    return sorted(usable, key=key)


def pick_model(names):
    ranked = rank_models(names)
    return ranked[0] if ranked else (names[0] if names else "gemini-flash-latest")


def models():
    """Список моделей по порядку предпочтения (основная + запасные)."""
    global _chosen_model, last_error
    preferred = os.environ.get("GEMINI_MODEL", "").strip()
    if _chosen_model is None:
        try:
            resp = requests.get(f"{BASE}/models", params={"key": _key(), "pageSize": 1000}, timeout=20)
            resp.raise_for_status()
            names = [
                m["name"].split("/", 1)[-1]
                for m in resp.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            _chosen_model = rank_models(names) or ["gemini-flash-latest"]
        except (requests.RequestException, ValueError) as exc:
            last_error = f"список моделей не получен: {exc}"
            return [preferred] if preferred else ["gemini-flash-latest"]
    ranked = list(_chosen_model)
    if preferred:
        ranked = [preferred] + [m for m in ranked if m != preferred]
    return ranked[:4]


def model_name():
    return models()[0]


used_model = None


def ask(prompt, temperature=0.2, json_mode=False):
    """Один вызов модели. Возвращает текст ответа или None."""
    global last_error, used_model
    if not available():
        return None

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

    errors = []
    for model in models():
        for attempt in range(2):
            try:
                resp = requests.post(
                    f"{BASE}/models/{model}:generateContent",
                    params={"key": _key()},
                    json=body,
                    timeout=90,
                )
            except requests.RequestException as exc:
                errors.append(f"{model}: {exc}")
                break
            if resp.status_code == 200:
                try:
                    parts = resp.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                except (KeyError, IndexError, ValueError):
                    errors.append(f"{model}: пустой ответ")
                    break
                used_model = model
                return text
            errors.append(f"{model}: HTTP {resp.status_code} {resp.text[:150]}")
            # 503 — модель перегружена, 429 — лимит запросов: ждём и пробуем ещё раз,
            # потом переходим к запасной модели. 404 — модели нет, сразу к следующей.
            if resp.status_code in (429, 500, 503) and attempt == 0:
                time.sleep(3)
                continue
            if resp.status_code in (400, 401, 403):
                last_error = errors[-1]  # проблема с ключом — запасные модели не помогут
                return None
            break
    last_error = " | ".join(errors[-3:])
    return None
