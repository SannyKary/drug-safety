"""Оркестратор: задаёт порядок шагов и ведёт журнал (трассу) одного запроса.

Путь записан в коде (workflow), модель решает только внутри двух шагов:
маршрутизатор выбирает разделы, синтезатор пишет и правит сводку.

    нормализация → [инструкции ‖ FAERS] → аналитик → маршрутизатор → синтезатор ⇄ критик
"""

import time
from concurrent.futures import ThreadPoolExecutor

from agents import analyst, faers_agent, label_agent, llm, normalizer, router, synthesizer
from agents.label_agent import SECTIONS


class Trace:
    """Журнал шагов: какой агент что получил и что вернул."""

    def __init__(self):
        self.steps = []

    def add(self, agent, pattern, started, result):
        self.steps.append({
            "agent": agent,
            "pattern": pattern,
            "seconds": round(time.time() - started, 2),
            "result": result,
        })


def run(drug_inputs, question=""):
    trace = Trace()
    result = {"errors": [], "trace": trace.steps}

    # 1. Нормализация названий
    t = time.time()
    names, notes = [], []
    for raw in drug_inputs:
        inn, how = normalizer.to_inn(raw)
        names.append(inn)
        notes.append(f"{raw} → {inn} ({how})")
    names = list(dict.fromkeys(n for n in names if n))
    trace.add("Нормализатор", "цепочка", t, "; ".join(notes))
    result["drugs"] = names
    if not names:
        result["errors"].append("Не введено ни одного препарата.")
        return result

    # 2. Источники параллельно: инструкции к каждому препарату и FAERS по сочетанию
    t = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        label_jobs = {n: pool.submit(label_agent.fetch, n) for n in names}
        faers_job = pool.submit(faers_agent.fetch, names)
        size_job = pool.submit(faers_agent.database_size)

        labels, missing = [], []
        for name, job in label_jobs.items():
            try:
                label = job.result()
            except Exception as exc:
                result["errors"].append(f"Инструкция {name}: {exc}")
                label = None
            if label:
                labels.append(label)
            else:
                missing.append(name)
        try:
            faers = faers_job.result()
            n_all = size_job.result()
        except Exception as exc:
            result["errors"].append(f"FAERS: {exc}")
            faers, n_all = None, 0

    trace.add(
        "Агенты DailyMed и FAERS", "параллельное выполнение", t,
        f"инструкций: {len(labels)}, не найдено: {missing or 'нет'}; "
        f"сообщений FAERS: {faers['n_reports'] if faers else 0}",
    )
    result.update(labels=labels, missing=missing, faers=faers)
    if not labels and not faers:
        result["errors"].append(
            "Ничего не найдено. Введите международное название латиницей, например warfarin."
        )
        return result

    # 3. Аналитик: PRR по каждой реакции и сверка с инструкцией
    t = time.time()
    rows = []
    if faers:
        terms = [term for term, _ in faers["reactions"]]
        try:
            totals = faers_agent.reaction_totals(terms)
            rows = analyst.compare(labels, faers, totals, n_all)
        except Exception as exc:
            result["errors"].append(f"Аналитик: {exc}")
    interactions = analyst.interaction_mentions(labels) if len(labels) > 1 else []
    n_cand = sum(r["verdict"] == "кандидат в сигнал" for r in rows)
    trace.add("Аналитик", "сведение источников", t,
              f"реакций: {len(rows)}, кандидатов в сигнал: {n_cand}, упоминаний взаимодействия: {len(interactions)}")
    result.update(rows=rows, interactions=interactions)

    # 4. Маршрутизатор: какие разделы инструкции нужны под вопрос
    t = time.time()
    routed, decided = {}, "правила"
    for label in labels:
        fields, decided = router.route(question, list(label["sections"]), len(names))
        routed[label["generic_name"]] = fields
    trace.add("Маршрутизатор", f"маршрутизация (решает: {decided})", t,
              {k: [SECTIONS[f] for f in v] for k, v in routed.items()})
    result["routed"] = routed

    # 5. Синтезатор ⇄ критик
    t = time.time()
    allowed = {SECTIONS[f] for fields in routed.values() for f in fields}
    numbers = synthesizer.allowed_numbers(faers, rows)
    facts = synthesizer.build_facts(labels, routed, faers, rows, interactions)
    summary, rounds = (None, [])
    if llm.available():
        summary, rounds = synthesizer.synthesize(names, question, facts, allowed, numbers)
    if summary is None:
        summary = synthesizer.fallback_summary(labels, routed, faers, rows, interactions)
        result["summary_by"] = "код (без LLM)"
    else:
        result["summary_by"] = "LLM + критик"
    trace.add("Синтезатор ⇄ Критик", "рефлексия", t,
              rounds or "LLM не подключена, сводка собрана кодом")
    result.update(summary=summary, critic_rounds=rounds, facts=facts)
    return result
