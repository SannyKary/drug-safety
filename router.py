"""Агент-маршрутизатор: решает, какие разделы инструкции нужны под вопрос.

Инструкция к варфарину — больше 100 тысяч символов. Класть её в окно
модели целиком дорого и вредно: модель теряется в лишнем. Маршрутизатор
выбирает разделы под конкретный вопрос («пациентка беременна» → раздел
беременности). Это точка, где решение принимает модель; без ключа LLM
работают простые правила.
"""

import json

from . import llm
from .label_agent import SECTIONS

ALWAYS = ["boxed_warning", "contraindications"]

RULES = {
    "drug_interactions": ["взаимодейств", "вместе", "сочетан", "совмест", "+", "interact"],
    "pregnancy": ["беремен", "pregnan"],
    "lactation": ["корм", "лактац", "грудн"],
    "nursing_mothers": ["корм", "лактац", "грудн"],
    "pediatric_use": ["ребён", "ребен", "дет", "подрост", "child"],
    "geriatric_use": ["пожил", "старш", "лет", "elderly"],
    "overdosage": ["передоз", "доз"],
    "dosage_and_administration": ["доз", "как принимать", "схем"],
    "adverse_reactions": ["побоч", "нежелат", "реакц"],
    "warnings_and_cautions": ["предупрежд", "осторож", "риск", "почк", "печен"],
    "warnings": ["предупрежд", "осторож", "риск", "почк", "печен"],
}


def _by_rules(question, n_drugs):
    q = question.lower()
    chosen = list(ALWAYS)
    if n_drugs > 1:
        chosen.append("drug_interactions")
    for field, keys in RULES.items():
        if any(k in q for k in keys) and field not in chosen:
            chosen.append(field)
    # Если вопрос общий, берём основные разделы безопасности
    if len(chosen) <= len(ALWAYS) + (n_drugs > 1):
        chosen += ["warnings_and_cautions", "warnings", "adverse_reactions"]
    return chosen


def route(question, available_fields, n_drugs):
    """Список полей инструкции, которые пойдут синтезатору, и кто решал."""
    question = (question or "").strip()
    decided_by = "правила"
    chosen = None

    if question and llm.available():
        menu = "\n".join(f"- {f}: {SECTIONS[f]}" for f in available_fields)
        answer = llm.ask(
            "Ты маршрутизатор медицинской системы. По вопросу пользователя выбери "
            "разделы инструкции к препарату, которые нужны для ответа. Выбирай только "
            "из списка, 2–6 разделов. Ответь JSON-массивом названий полей.\n\n"
            f"Препаратов в запросе: {n_drugs}\nВопрос: {question}\n\nРазделы:\n{menu}",
            json_mode=True,
        )
        try:
            fields = json.loads(answer) if answer else None
            if isinstance(fields, list):
                chosen = [f for f in fields if f in available_fields]
                decided_by = "LLM"
        except ValueError:
            chosen = None

    if not chosen:
        chosen = _by_rules(question, n_drugs)
        decided_by = "правила"

    # Предупреждение в рамке и противопоказания показываем всегда
    for f in ALWAYS + (["drug_interactions"] if n_drugs > 1 else []):
        if f not in chosen:
            chosen.insert(0, f)
    return [f for f in dict.fromkeys(chosen) if f in available_fields], decided_by
