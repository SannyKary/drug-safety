"""Агент FAERS (через openFDA /drug/event).

FAERS — база спонтанных сообщений о нежелательных реакциях. Это «что
происходит на практике». Важно: одно сообщение — это подозрение врача или
пациента, а не доказанная причинно-следственная связь.
"""

from concurrent.futures import ThreadPoolExecutor

from . import openfda

SOURCE_NAME = "FAERS (сообщения о нежелательных реакциях)"
ENDPOINT = "drug/event"
REACTION_FIELD = "patient.reaction.reactionmeddrapt.exact"


def drug_search(drugs):
    """Сообщения, где упомянуты все препараты из списка."""
    return " AND ".join(f"patient.drug.openfda.generic_name:{openfda.quote(d)}" for d in drugs)


def fetch(drugs, top=20):
    """Сводка FAERS по препарату или сочетанию. None, если сообщений нет."""
    search = drug_search(drugs)
    n_reports = openfda.total(ENDPOINT, search)
    if n_reports == 0:
        return None

    counts = openfda.get(ENDPOINT, search=search, count=REACTION_FIELD, limit=top)
    reactions = [(r["term"], r["count"]) for r in (counts or {}).get("results", [])]

    return {
        "source": SOURCE_NAME,
        "drugs": drugs,
        "n_reports": n_reports,
        "n_serious": openfda.total(ENDPOINT, f"{search} AND serious:1"),
        "n_deaths": openfda.total(ENDPOINT, f"{search} AND seriousnessdeath:1"),
        "reactions": reactions,
        "url": "https://open.fda.gov/data/faers/",
    }


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
