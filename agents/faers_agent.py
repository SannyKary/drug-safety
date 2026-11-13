"""Агент FAERS (через openFDA /drug/event).

FAERS — база спонтанных сообщений о нежелательных реакциях. Это «что
происходит на практике». Важно: одно сообщение — это подозрение врача или
пациента, а не доказанная причинно-следственная связь.

Известная проблема FAERS — дубли: один и тот же случай (часто из научной
статьи) присылают несколько раз разные производители. Если сообщений
немного (≤ DEDUP_LIMIT), агент скачивает их целиком и склеивает те, что
совпадают по ключевым полям, а реакции считает по уникальным случаям.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import openfda

SOURCE_NAME = "FAERS (сообщения о нежелательных реакциях)"
ENDPOINT = "drug/event"
REACTION_FIELD = "patient.reaction.reactionmeddrapt.exact"
DEDUP_LIMIT = 2000
PAGE = 100


def drug_search(drugs):
    """Сообщения, где упомянуты все препараты из списка."""
    return " AND ".join(f"patient.drug.openfda.generic_name:{openfda.quote(d)}" for d in drugs)


def _fingerprint(report):
    """Ключевые поля случая. Совпали все — считаем, что это один случай."""
    patient = report.get("patient", {})
    drugs = set()
    for d in patient.get("drug", []):
        names = d.get("openfda", {}).get("generic_name")
        drugs.add((names[0] if names else d.get("medicinalproduct", "")).upper())
    reactions = frozenset(r.get("reactionmeddrapt", "").upper() for r in patient.get("reaction", []))
    return (
        patient.get("patientsex"),
        patient.get("patientonsetage"),
        report.get("occurcountry"),
        frozenset(drugs),
        reactions,
    )


def _deduplicate(search, n_reports):
    """Скачивает сообщения, склеивает дубли, считает реакции по случаям.

    Качаем небольшими страницами по очереди и от каждого сообщения
    сразу оставляем только нужное: полный JSON сообщения FAERS весит
    десятки килобайт, и тысяча таких целиком не влезает в память
    бесплатного сервера.
    """
    cases = {}
    n_loaded = 0
    for skip in range(0, n_reports, PAGE):
        data = openfda.get(ENDPOINT, search=search, limit=PAGE, skip=skip)
        for r in (data or {}).get("results", []):
            n_loaded += 1
            fp = _fingerprint(r)
            if fp not in cases:
                cases[fp] = (str(r.get("serious")) == "1", str(r.get("seriousnessdeath")) == "1")
        del data

    counts = Counter()
    for fp in cases:
        for term in fp[4]:  # набор реакций — последнее поле отпечатка
            counts[term] += 1
    serious = sum(s for s, _ in cases.values())
    deaths = sum(d for _, d in cases.values())
    return n_loaded, len(cases), counts, serious, deaths


def fetch(drugs, top=20):
    """Сводка FAERS по препарату или сочетанию. None, если сообщений нет."""
    search = drug_search(drugs)
    n_reports = openfda.total(ENDPOINT, search)
    if n_reports == 0:
        return None

    result = {
        "source": SOURCE_NAME,
        "drugs": drugs,
        "n_raw": n_reports,
        "deduplicated": False,
        "url": "https://open.fda.gov/data/faers/",
    }

    if n_reports <= DEDUP_LIMIT:
        try:
            n_loaded, n_cases, counts, serious, deaths = _deduplicate(search, n_reports)
        except openfda.OpenFDAError:
            n_loaded = 0
        if n_loaded:
            result.update(
                deduplicated=True,
                n_reports=n_cases,
                n_duplicates=n_loaded - n_cases,
                n_serious=serious,
                n_deaths=deaths,
                reactions=[(t, c) for t, c in counts.most_common(top) if t],
            )
            return result

    # Сообщений много — считает сам openFDA, без склейки дублей
    counts = openfda.get(ENDPOINT, search=search, count=REACTION_FIELD, limit=top)
    result.update(
        n_reports=n_reports,
        n_duplicates=None,
        n_serious=openfda.total(ENDPOINT, f"{search} AND serious:1"),
        n_deaths=openfda.total(ENDPOINT, f"{search} AND seriousnessdeath:1"),
        reactions=[(r["term"], r["count"]) for r in (counts or {}).get("results", [])],
    )
    return result


def database_size():
    """Всего сообщений в FAERS (нужно для расчёта PRR)."""
    return openfda.total(ENDPOINT)


def reaction_totals(terms):
    """Сколько всего сообщений с каждой реакцией, по всей базе.

    Запросы независимы, поэтому идут параллельно (4 потока, чтобы
    не упереться в лимит openFDA).
    """
    def one(term):
        return term, openfda.total(ENDPOINT, f"{REACTION_FIELD}:{openfda.quote(term)}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(one, terms))
