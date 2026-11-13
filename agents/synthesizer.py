"""Синтезатор (продюсер) и критик — паттерн «рефлексия» из лекции 3.

Синтезатор пишет сводку по фактам, которые ему положили в окно.
Критик проверяет сводку по правилам и, если нашёл нарушения,
возвращает их синтезатору на доработку. Кругов не больше MAX_ROUNDS,
чтобы цикл «улучши ещё» не шёл бесконечно.
"""

import re

from . import llm
from .label_agent import SECTIONS

MAX_ROUNDS = 2
EXCERPT_CHARS = 2500

FAERS_TAG = "[FAERS]"
ANALYSIS_TAG = "[Анализ]"
CAUSAL = re.compile(r"вызыва|вызвал|привод[ия]т к|является причин|провоцир", re.IGNORECASE)
TAG = re.compile(r"\[(Инструкция[^\]]*|FAERS|Анализ)\]")


def _fmt(n):
    return f"{n:,}".replace(",", " ")


def build_facts(labels, routed, faers, rows, interactions):
    """Факты, которые увидит модель. Только они, ничего «из памяти»."""
    parts = []
    for label in labels:
        parts.append(f"## Инструкция: {label['generic_name']} (версия {label['effective_time']})")
        for field in routed.get(label["generic_name"], []):
            text = label["sections"][field]
            cut = text[:EXCERPT_CHARS] + (" …[обрезано]" if len(text) > EXCERPT_CHARS else "")
            parts.append(f"### Раздел «{SECTIONS[field]}»\n{cut}")

    if faers:
        parts.append(
            f"## FAERS: {' + '.join(faers['drugs'])}\n"
            f"Уникальных случаев: {_fmt(faers['n_reports'])}; серьёзных: {_fmt(faers['n_serious'])}; "
            f"с исходом смерть: {_fmt(faers['n_deaths'])}."
            + (f" Исходно сообщений {_fmt(faers['n_raw'])}, дублей склеено {_fmt(faers['n_duplicates'])}."
               if faers.get("deduplicated") else " Дубли не склеивались.")
        )
        lines = []
        for r in rows:
            prr_txt = f"PRR {r['prr']:.1f}" if r["prr"] else "PRR —"
            label_txt = {True: "есть в инструкции", False: "НЕ найдено в инструкции", None: "—"}[r["in_label"]]
            lines.append(f"- {r['reaction']}: {_fmt(r['reports'])} сообщ.; {prr_txt}; {label_txt}; итог: {r['verdict']}")
        parts.append("## Анализ реакций\n" + "\n".join(lines))

    for it in interactions:
        parts.append(
            f"## Взаимодействие: инструкция {it['in_label_of']} упоминает {it['mentions']}\n«{it['quote']}»"
        )
    return "\n\n".join(parts)


def allowed_numbers(faers, rows):
    nums = set()
    if faers:
        nums |= {faers["n_reports"], faers["n_serious"], faers["n_deaths"], faers.get("n_raw", 0)}
        if faers.get("n_duplicates"):
            nums.add(faers["n_duplicates"])
    for r in rows:
        nums.add(r["reports"])
    return nums


def critique(summary, allowed_sections, numbers):
    """Правила критика. Возвращает список замечаний (пустой — всё хорошо)."""
    issues = []
    bullets = [l.strip() for l in summary.splitlines() if re.match(r"\s*([-•*]|\d+[.)])\s", l)]
    if not bullets:
        issues.append("Сводка должна быть списком пунктов, каждый со ссылкой на источник.")

    for line in bullets:
        tags = TAG.findall(line)
        if not tags:
            issues.append(f"Пункт без ссылки на источник: «{line[:90]}»")
        for t in tags:
            if t.startswith("Инструкция"):
                name = t.split(":", 1)[-1].strip() if ":" in t else ""
                if name and name not in allowed_sections:
                    issues.append(f"Ссылка на раздел, которого нет в данных: [{t}]")
        if "FAERS" in tags and CAUSAL.search(line):
            issues.append(
                f"FAERS не доказывает причину: пиши «сообщают о…», а не «вызывает». Пункт: «{line[:90]}»"
            )

    # Крупные числа должны браться из данных, а не придумываться
    for raw in re.findall(r"\d[\d   ]{2,}\d|\d{3,}", summary):
        value = int(re.sub(r"\D", "", raw))
        if value >= 100 and value not in numbers and not 1900 <= value <= 2100:
            issues.append(f"Число {raw.strip()} не найдено в данных.")
    return issues


PROMPT = """Ты помощник клинического фармаколога. Напиши на русском краткую сводку
по безопасности: {drugs}.{question}

Правила:
1. Пиши только по фактам ниже. Ничего не добавляй из своих знаний.
2. Формат: 5–8 пунктов списка, каждый начинается с «- ».
3. Каждый пункт заканчивается ссылкой: [Инструкция: <название раздела как в данных>],
   [FAERS] или [Анализ]. Разделы: {sections}.
4. FAERS — спонтанные сообщения, они не доказывают причину. Пиши «сообщают о…»,
   а не «вызывает».
5. Если реакция — «кандидат в сигнал», так и назови: повод для проверки.
   «Возможная серия дублей» — не сигнал: скорее всего, один случай прислан много раз.
6. Числа бери только из данных. Не давай рекомендаций по лечению.

ФАКТЫ:
{facts}
"""


def synthesize(drugs, question, facts, allowed_sections, numbers):
    """Цикл продюсер → критик. Возвращает (текст, журнал кругов)."""
    log = []
    prompt = PROMPT.format(
        drugs=" + ".join(drugs),
        question=f"\nВопрос пользователя: {question}" if question else "",
        sections=", ".join(sorted(allowed_sections)),
        facts=facts,
    )
    summary = llm.ask(prompt)
    if summary is None:
        return None, [{"round": 0, "issues": [f"LLM недоступна: {llm.last_error}"]}]

    for round_no in range(1, MAX_ROUNDS + 1):
        issues = critique(summary, allowed_sections, numbers)
        log.append({"round": round_no, "issues": issues})
        if not issues:
            break
        if round_no == MAX_ROUNDS:
            break
        fixed = llm.ask(
            prompt + "\n\nТВОЙ ЧЕРНОВИК:\n" + summary
            + "\n\nКРИТИК НАШЁЛ НАРУШЕНИЯ, ИСПРАВЬ ИХ И ВЕРНИ ТОЛЬКО НОВУЮ СВОДКУ:\n- "
            + "\n- ".join(issues)
        )
        if not fixed:
            break
        summary = fixed
    return summary, log


def fallback_summary(labels, routed, faers, rows, interactions):
    """Сводка без LLM: собирается кодом из тех же фактов."""
    out = []
    for label in labels:
        for field in ("boxed_warning", "contraindications"):
            if field in routed.get(label["generic_name"], []):
                text = label["sections"][field]
                out.append(f"- {label['generic_name']}: {text[:260]}… [Инструкция: {SECTIONS[field]}]")
    for it in interactions:
        out.append(f"- Инструкция {it['in_label_of']} упоминает {it['mentions']} в разделе взаимодействий. [Инструкция: Взаимодействия]")
    if faers:
        out.append(
            f"- В FAERS {_fmt(faers['n_reports'])} уникальных случаев, из них серьёзных {_fmt(faers['n_serious'])}. {FAERS_TAG}"
        )
        series = [r["reaction"] for r in rows if r["verdict"] == "возможная серия дублей"]
        if series:
            out.append(f"- Похоже на серию дублей, в сигналы не включены: {', '.join(series)}. {ANALYSIS_TAG}")
        cands = [r["reaction"] for r in rows if r["verdict"] == "кандидат в сигнал"]
        if cands:
            out.append(f"- Кандидаты в сигнал (часто сообщают, в инструкции не найдено): {', '.join(cands)}. {ANALYSIS_TAG}")
        else:
            out.append(f"- Среди частых реакций кандидатов в сигнал нет. {ANALYSIS_TAG}")
    return "\n".join(out)
