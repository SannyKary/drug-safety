"""Агент инструкций (DailyMed, через openFDA /drug/label).

Достаёт официальную инструкцию FDA к препарату и раскладывает её по
разделам. Это «что обещано»: противопоказания, предупреждения,
взаимодействия, особые группы.
"""

import re

from . import openfda

SOURCE_NAME = "DailyMed (инструкция FDA)"

# Поле openFDA → русское название раздела. Порядок — порядок показа.
SECTIONS = {
    "boxed_warning": "Предупреждение в рамке",
    "indications_and_usage": "Показания",
    "contraindications": "Противопоказания",
    "warnings_and_cautions": "Предупреждения и меры предосторожности",
    "warnings": "Предупреждения",
    "adverse_reactions": "Нежелательные реакции",
    "drug_interactions": "Взаимодействия",
    "pregnancy": "Беременность",
    "lactation": "Лактация",
    "nursing_mothers": "Кормление грудью",
    "pediatric_use": "Применение у детей",
    "geriatric_use": "Применение у пожилых",
    "overdosage": "Передозировка",
    "dosage_and_administration": "Способ применения и дозы",
}

# Разделы, в которых ищем упоминание побочных реакций из FAERS
SAFETY_SECTIONS = [
    "boxed_warning", "contraindications", "warnings_and_cautions", "warnings",
    "adverse_reactions", "drug_interactions", "overdosage",
]


def _clean(text):
    return re.sub(r"\s+", " ", text).strip()


def _score(label):
    """Чем больше разделов безопасности в инструкции, тем она полезнее."""
    return sum(1 for f in SAFETY_SECTIONS if label.get(f))


def fetch(drug):
    """Инструкция к препарату или None, если в базе её нет.

    Возвращает словарь: название, торговые названия, производитель,
    дата версии, ссылка на DailyMed и разделы {поле: текст}.
    """
    q = openfda.quote(drug)
    # Поля проверяем по очереди: запрос «A или B» openFDA понимает ненадёжно
    data = None
    for field in ("openfda.generic_name", "openfda.brand_name", "openfda.substance_name"):
        data = openfda.get("drug/label", search=f"{field}:{q}", limit=5)
        if data and data.get("results"):
            break
    if not data or not data.get("results"):
        return None

    # У одного вещества десятки инструкций от разных производителей.
    # Берём самую полную по разделам безопасности.
    label = max(data["results"], key=_score)
    meta = label.get("openfda", {})
    set_id = label.get("set_id", "")

    sections = {}
    for field in SECTIONS:
        if label.get(field):
            sections[field] = _clean(" ".join(label[field]))

    return {
        "source": SOURCE_NAME,
        "query": drug,
        "generic_name": (meta.get("generic_name") or [drug])[0].lower(),
        "brand_names": sorted(set(meta.get("brand_name", [])))[:8],
        "manufacturer": (meta.get("manufacturer_name") or ["—"])[0],
        "effective_time": label.get("effective_time", ""),
        "url": f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={set_id}" if set_id else "",
        "sections": sections,
    }


def safety_text(label):
    """Весь текст разделов безопасности одной строкой, в нижнем регистре."""
    return " ".join(label["sections"].get(f, "") for f in SAFETY_SECTIONS).lower()
