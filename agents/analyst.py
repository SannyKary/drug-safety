"""Агент-аналитик: сравнивает «обещанное» (инструкция) с «наблюдаемым» (FAERS).

Для каждой частой реакции из FAERS он отвечает на два вопроса:
1. Сообщают ли о ней с этим препаратом чаще, чем в среднем по базе?
   Мера — PRR (proportional reporting ratio) и критерии Эванса.
2. Упомянута ли она в разделах безопасности инструкции?

Реакция, которая проходит пункт 1 и не найдена в пункте 2, —
кандидат в сигнал: повод проверить, а не доказательство вреда.
"""

import math
import re

from . import label_agent

# Записи FAERS, которые описывают не реакцию организма, а обстоятельства
# применения препарата. В сигналы их не берём, но показываем отдельно.
PROCESS_TERMS = {
    "OFF LABEL USE", "DRUG INEFFECTIVE", "PRODUCT USE IN UNAPPROVED INDICATION",
    "INAPPROPRIATE SCHEDULE OF PRODUCT ADMINISTRATION", "PRODUCT USE ISSUE",
    "WRONG TECHNIQUE IN PRODUCT USAGE PROCESS", "PRODUCT DOSE OMISSION ISSUE",
    "PRODUCT DOSE OMISSION", "INTENTIONAL PRODUCT MISUSE", "INTENTIONAL PRODUCT USE ISSUE",
    "NO ADVERSE EVENT", "DRUG INTERACTION", "TOXICITY TO VARIOUS AGENTS",
    "PRODUCT QUALITY ISSUE", "THERAPY INTERRUPTED", "THERAPEUTIC PRODUCT EFFECT DECREASED",
    "DRUG DOSE OMISSION", "MEDICATION ERROR", "OVERDOSE", "ACCIDENTAL OVERDOSE",
    "CONDITION AGGRAVATED", "DEATH", "HOSPITALISATION", "TREATMENT FAILURE",
    "MALAISE", "ILLNESS", "INCORRECT DOSE ADMINISTERED", "PRODUCT PRESCRIBING ERROR",
    "DRUG HYPERSENSITIVITY", "THERAPY NON-RESPONDER", "DRUG LEVEL INCREASED",
}

# Британское написание MedDRA → американское в инструкциях FDA
SPELLING = [
    ("oedema", "edema"), ("dyspnoea", "dyspnea"), ("diarrhoea", "diarrhea"),
    ("oesophag", "esophag"), ("haem", "hem"), ("anaem", "anem"), ("leukaem", "leukem"),
    ("ischaem", "ischem"), ("paediatr", "pediatr"), ("foetal", "fetal"),
    ("normalised", "normalized"), ("tumour", "tumor"), ("oestr", "estr"),
]

# Термин MedDRA → как его обычно пишут в инструкциях
SYNONYMS = {
    "international normalised ratio increased": ["inr"],
    "international normalised ratio abnormal": ["inr"],
    "international normalised ratio decreased": ["inr"],
    "blood glucose increased": ["hyperglycemia"],
    "blood glucose decreased": ["hypoglycemia"],
    "blood pressure increased": ["hypertension"],
    "blood pressure decreased": ["hypotension"],
    "heart rate increased": ["tachycardia"],
    "heart rate decreased": ["bradycardia"],
    "blood creatinine increased": ["creatinine"],
    "hepatic enzyme increased": ["liver enzyme", "transaminase", "alt "],
    "electrocardiogram qt prolonged": ["qt prolong", "qt interval"],
    "fall": ["falls", "fall "],
}

# Слова, которые уточняют реакцию, но в инструкции могут быть сказаны иначе
WEAK_WORDS = {
    "increased", "decreased", "abnormal", "disorder", "syndrome", "nos", "of", "the",
    "and", "in", "site", "acute", "chronic", "aggravated", "worsening",
}

# Критерии Эванса (Evans et al., 2001) — классический порог сигнала в фармаконадзоре
MIN_CASES = 3
MIN_PRR = 2.0
MIN_CHI2 = 4.0


def _american(text):
    text = text.lower()
    for brit, amer in SPELLING:
        text = text.replace(brit, amer)
    return text


def in_label(term, label_text):
    """Упомянута ли реакция в тексте инструкции (по словам, без учёта формы)."""
    term_l = term.lower()
    for syn in SYNONYMS.get(term_l, []):
        if syn in label_text:
            return True
    words = [w for w in re.findall(r"[a-z]+", _american(term_l)) if w not in WEAK_WORDS]
    if not words:
        return False
    # Слово считается найденным, если в тексте есть слово с тем же началом:
    # «bleeding» найдёт «bleed», «haemorrhage» → «hemorrhag».
    return all(re.search(r"\b" + re.escape(w[:max(5, len(w) - 3)]), label_text) for w in words)


def prr(a, n_drug, n_reaction, n_all):
    """PRR, нижняя граница 95% ДИ и χ² с поправкой Йейтса.

    Таблица 2×2 по сообщениям FAERS:
                    реакция   нет реакции
      препарат         a          b
      остальные        c          d
    PRR = [a / (a+b)] / [c / (c+d)]
    """
    b = n_drug - a
    c = n_reaction - a
    d = n_all - n_drug - c
    if a <= 0 or b < 0 or c <= 0 or d <= 0:
        return None, None, None
    value = (a / (a + b)) / (c / (c + d))
    se = math.sqrt(max(1 / a - 1 / (a + b) + 1 / c - 1 / (c + d), 0))
    low = math.exp(math.log(value) - 1.96 * se)
    n = a + b + c + d
    chi2 = n * (abs(a * d - b * c) - n / 2) ** 2 / ((a + b) * (c + d) * (a + c) * (b + d))
    return value, low, chi2


def compare(labels, faers, reaction_totals, n_all):
    """Строки таблицы сравнения: одна реакция — одна строка.

    Для сочетания препаратов реакция считается упомянутой,
    если она есть в инструкции хотя бы одного из них.
    """
    text = " ".join(label_agent.safety_text(l) for l in labels)
    rows = []
    for term, a in faers["reactions"]:
        process = term in PROCESS_TERMS
        value, low, chi2 = prr(a, faers["n_reports"], reaction_totals.get(term, 0), n_all)
        stat_signal = (
            value is not None and a >= MIN_CASES and value >= MIN_PRR and chi2 >= MIN_CHI2
        )
        mentioned = in_label(term, text) if text else None

        if process:
            verdict = "служебная запись"
        elif stat_signal and mentioned is False:
            verdict = "кандидат в сигнал"
        elif stat_signal:
            verdict = "ожидаемая, подтверждается"
        elif mentioned:
            verdict = "в инструкции, не выделяется"
        else:
            verdict = "фон"

        rows.append({
            "reaction": term,
            "reports": a,
            "share": a / faers["n_reports"],
            "prr": value,
            "prr_low": low,
            "chi2": chi2,
            "in_label": mentioned,
            "verdict": verdict,
        })
    return rows


def interaction_mentions(labels):
    """Сочетание препаратов: упоминает ли инструкция одного другой.

    Возвращает список {кто, о ком, цитата} — с цитатой из раздела
    «Взаимодействия», чтобы вывод можно было проверить.
    """
    found = []
    for a in labels:
        for b in labels:
            if a is b:
                continue
            text = a["sections"].get("drug_interactions", "")
            name = b["generic_name"].split()[0]  # «warfarin sodium» → «warfarin»
            m = re.search(r"\b" + re.escape(name), text, re.IGNORECASE)
            if m:
                start, end = max(0, m.start() - 220), min(len(text), m.end() + 280)
                found.append({
                    "in_label_of": a["generic_name"],
                    "mentions": b["generic_name"],
                    "quote": ("…" if start else "") + text[start:end] + ("…" if end < len(text) else ""),
                })
    return found
