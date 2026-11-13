"""Агент нормализации: превращает то, что ввёл пользователь, в название,
которое понимает openFDA (международное непатентованное, латиницей).

Порядок попыток: словарь частых русских названий → LLM (если есть ключ)
→ ввод как есть. Найденное имя затем сверяется с базой инструкций.
"""

import re

from . import llm

RU_TO_INN = {
    "варфарин": "warfarin",
    "амиодарон": "amiodarone",
    "аспирин": "aspirin",
    "ацетилсалициловая кислота": "aspirin",
    "кардиомагнил": "aspirin",
    "ибупрофен": "ibuprofen",
    "нурофен": "ibuprofen",
    "парацетамол": "acetaminophen",
    "метформин": "metformin",
    "глюкофаж": "metformin",
    "симвастатин": "simvastatin",
    "аторвастатин": "atorvastatin",
    "розувастатин": "rosuvastatin",
    "кларитромицин": "clarithromycin",
    "азитромицин": "azithromycin",
    "ципрофлоксацин": "ciprofloxacin",
    "флуконазол": "fluconazole",
    "дигоксин": "digoxin",
    "лизиноприл": "lisinopril",
    "эналаприл": "enalapril",
    "лозартан": "losartan",
    "амлодипин": "amlodipine",
    "бисопролол": "bisoprolol",
    "метопролол": "metoprolol",
    "омепразол": "omeprazole",
    "клопидогрел": "clopidogrel",
    "апиксабан": "apixaban",
    "ривароксабан": "rivaroxaban",
    "дабигатран": "dabigatran",
    "сертралин": "sertraline",
    "флуоксетин": "fluoxetine",
    "трамадол": "tramadol",
    "диклофенак": "diclofenac",
    "преднизолон": "prednisone",
    "левотироксин": "levothyroxine",
    "аллопуринол": "allopurinol",
    "фуросемид": "furosemide",
    "спиронолактон": "spironolactone",
    "карбамазепин": "carbamazepine",
    "вальпроевая кислота": "valproic acid",
    "литий": "lithium",
    "метотрексат": "methotrexate",
    "инсулин": "insulin",
}

CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


def to_inn(user_input):
    """Возвращает (название латиницей, как получено)."""
    name = user_input.strip().lower()
    if not CYRILLIC.search(name):
        return name, "введено латиницей"
    if name in RU_TO_INN:
        return RU_TO_INN[name], "словарь"
    if llm.available():
        answer = llm.ask(
            "Переведи название лекарства в международное непатентованное "
            "название на английском (как в базе FDA). Ответь только названием, "
            "строчными буквами, без пояснений.\n\nНазвание: " + user_input
        )
        if answer:
            return answer.strip().strip(".").lower(), "LLM"
    return name, "не распознано"
