"""Клиент openFDA: один источник правды для HTTP-запросов.

Оба агента-источника ходят сюда, а не в requests напрямую. Здесь же
тайм-ауты, повторы и понятные ошибки, чтобы сбой сети не ронял приложение.
"""

import os
import time

import requests

BASE_URL = "https://api.fda.gov"
TIMEOUT = 20
RETRIES = 3

# Ключ openFDA необязателен: без него лимит 1000 запросов в день с одного IP.
API_KEY = os.environ.get("OPENFDA_API_KEY", "")


class OpenFDAError(Exception):
    """Сервис недоступен или ответил ошибкой."""


def get(endpoint, search=None, count=None, limit=1):
    """GET к openFDA. Возвращает JSON или None, если ничего не найдено (404)."""
    params = {"limit": limit}
    if search:
        params["search"] = search
    if count:
        params["count"] = count
    if API_KEY:
        params["api_key"] = API_KEY

    url = f"{BASE_URL}/{endpoint}.json"
    last_error = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = exc
        else:
            # 404 у openFDA означает «по запросу ничего нет», это не сбой.
            if resp.status_code == 404:
                return None
            if resp.status_code == 200:
                return resp.json()
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            # 429 — превышен лимит запросов, ждём подольше
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(1.5 * (attempt + 1))

    raise OpenFDAError(f"openFDA не ответил ({endpoint}): {last_error}")


def total(endpoint, search=None):
    """Сколько записей подходит под запрос (поле meta.results.total)."""
    data = get(endpoint, search=search, limit=1)
    if not data:
        return 0
    return data.get("meta", {}).get("results", {}).get("total", 0)


def quote(term):
    """Фраза для поиска openFDA: в кавычках, без внутренних кавычек."""
    return '"' + term.replace('"', "") + '"'
