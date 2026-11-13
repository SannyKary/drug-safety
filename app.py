"""Досье безопасности препарата — интерфейс Streamlit."""

import os

import altair as alt
import pandas as pd
import streamlit as st

# Ключи из Streamlit Secrets → в окружение, откуда их читают агенты
for key in ("GEMINI_API_KEY", "GEMINI_MODEL", "OPENFDA_API_KEY"):
    try:
        if key in st.secrets and not os.environ.get(key):
            os.environ[key] = str(st.secrets[key])
    except Exception:
        pass  # файла секретов нет — работаем без них

import orchestrator  # noqa: E402  (после установки ключей)
from agents import llm  # noqa: E402
from agents.label_agent import SECTIONS  # noqa: E402

st.set_page_config(page_title="Досье безопасности препарата", page_icon="💊", layout="wide")

VERDICT_COLORS = {
    "кандидат в сигнал": "#d1495b",
    "возможная серия дублей": "#8e6fb5",
    "выделяется (нет инструкции для сверки)": "#e07a5f",
    "ожидаемая, подтверждается": "#edae49",
    "в инструкции, не выделяется": "#66a182",
    "фон": "#a0a0a0",
    "служебная запись": "#d0d0d0",
}

EXAMPLES = {
    "Варфарин + амиодарон": ("warfarin, amiodarone", "Чем опасно сочетание?"),
    "Варфарин": ("warfarin", ""),
    "Метформин у пожилых": ("metformin", "Пациент 78 лет, сниженная функция почек"),
    "Симвастатин + кларитромицин": ("simvastatin, clarithromycin", ""),
}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cached_run(drugs, question, llm_on):
    # llm_on входит в ключ кэша, чтобы после добавления ключа не брать старый ответ
    return orchestrator.run(list(drugs), question)


def parse_drugs(text):
    for sep in ["+", ";", "\n"]:
        text = text.replace(sep, ",")
    return tuple(d.strip() for d in text.split(",") if d.strip())


# ---------- Боковая панель ----------
with st.sidebar:
    st.header("О приложении")
    st.write(
        "Мультиагентная система сравнивает **официальную инструкцию** (DailyMed) "
        "с **реальными сообщениями о нежелательных реакциях** (FAERS) и выделяет "
        "реакции, о которых часто сообщают, но которых нет в инструкции."
    )
    st.markdown(
        "**Источники**\n"
        "- [DailyMed](https://dailymed.nlm.nih.gov/) — инструкции FDA\n"
        "- [FAERS](https://open.fda.gov/data/faers/) — сообщения о нежелательных реакциях\n\n"
        "Оба получены через [openFDA API](https://open.fda.gov/apis/)."
    )
    st.markdown(f"**LLM:** {'Gemini подключена ✅' if llm.available() else 'не подключена — сводку собирает код'}")
    st.warning(
        "Учебный проект, не медицинская рекомендация. FAERS — спонтанные сообщения: "
        "они не доказывают, что реакцию вызвал препарат. Названия — международные, "
        "как в США (warfarin, acetaminophen)."
    )

# ---------- Ввод ----------
st.title("💊 Досье безопасности препарата")
st.caption("Что обещано в инструкции ↔ что сообщают на практике")

if "drugs" not in st.session_state:
    st.session_state.drugs, st.session_state.question = EXAMPLES["Варфарин + амиодарон"]

cols = st.columns(len(EXAMPLES))
for col, (title, (d, q)) in zip(cols, EXAMPLES.items()):
    if col.button(title, width="stretch"):
        st.session_state.drugs, st.session_state.question = d, q

drugs_text = st.text_input(
    "Препарат или сочетание (через запятую или +)", key="drugs",
    help="Международное название латиницей. Частые русские названия тоже понимаются: варфарин, метформин…",
)
question = st.text_input("Вопрос или особенности пациента (необязательно)", key="question")

if not st.button("Собрать досье", type="primary"):
    st.stop()

drugs = parse_drugs(drugs_text)
if not drugs:
    st.error("Введите хотя бы один препарат.")
    st.stop()

with st.spinner("Агенты собирают данные из DailyMed и FAERS…"):
    res = cached_run(drugs, question.strip(), llm.available())

for err in res["errors"]:
    st.error(err)
if not res.get("labels") and not res.get("faers"):
    st.stop()

if res.get("missing"):
    st.warning(f"Инструкция не найдена: {', '.join(res['missing'])}. Проверьте название (латиницей, международное).")

faers, rows = res.get("faers"), res.get("rows", [])

# ---------- Метрики ----------
m = st.columns(4)
if faers:
    if faers.get("deduplicated"):
        m[0].metric("Уникальных случаев FAERS", f"{faers['n_reports']:,}".replace(",", " "),
                    f"из {faers['n_raw']:,} сообщений, дублей {faers['n_duplicates']:,}".replace(",", " "),
                    delta_color="off")
    else:
        m[0].metric("Сообщений в FAERS", f"{faers['n_reports']:,}".replace(",", " "))
    m[1].metric("Серьёзных", f"{faers['n_serious']:,}".replace(",", " "),
                f"{faers['n_serious'] / faers['n_reports']:.0%}", delta_color="off")
    m[2].metric("С исходом смерть", f"{faers['n_deaths']:,}".replace(",", " "), delta_color="off")
m[3].metric("Кандидатов в сигнал", sum(r["verdict"] == "кандидат в сигнал" for r in rows))

tab_sum, tab_sig, tab_label, tab_trace, tab_arch = st.tabs(
    ["Сводка", "Сигналы", "Инструкция", "Трасса агентов", "Как это работает"]
)

# ---------- Сводка ----------
with tab_sum:
    st.caption(f"Сводку написал: {res['summary_by']}. Каждый пункт ссылается на источник.")
    st.markdown(res["summary"])
    for it in res.get("interactions", []):
        with st.expander(f"Цитата: инструкция {it['in_label_of']} о {it['mentions']}"):
            st.write(it["quote"])
    rounds = res.get("critic_rounds") or []
    if rounds:
        with st.expander("Как работал критик"):
            for r in rounds:
                if r["issues"]:
                    st.write(f"Круг {r['round']}: замечаний {len(r['issues'])}")
                    for i in r["issues"]:
                        st.write("•", i)
                else:
                    st.write(f"Круг {r['round']}: замечаний нет ✅")

# ---------- Сигналы ----------
with tab_sig:
    if not rows:
        st.info("В FAERS нет сообщений по этому запросу.")
    else:
        df = pd.DataFrame(rows)
        if faers.get("deduplicated"):
            st.success(
                f"Дубли удалены: из {faers['n_raw']} сообщений {faers['n_duplicates']} совпали "
                "по ключевым полям (пол, возраст, страна, препараты, реакции) с другими и склеены. "
                "Счёт ниже — по уникальным случаям."
            )
        elif faers.get("n_duplicates") is None:
            st.info("Сообщений больше 1000 — счёт по данным openFDA без склейки дублей.")
        if any(r.get("series") for r in rows):
            st.warning(
                "Несколько реакций встречаются почти одинаковое число раз — похоже на один случай, "
                "присланный многократно. Такие реакции помечены «возможная серия дублей» и в сигналы не входят."
            )
        chart = (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X("reports:Q", title="Сообщений"),
                y=alt.Y("reaction:N", sort="-x", title=None),
                color=alt.Color("verdict:N", title="Итог",
                                scale=alt.Scale(domain=list(VERDICT_COLORS), range=list(VERDICT_COLORS.values()))),
                tooltip=["reaction", "reports", alt.Tooltip("prr:Q", format=".2f"), "verdict"],
            )
            .properties(height=28 * len(df))
        )
        st.altair_chart(chart, width="stretch")

        table = pd.DataFrame({
            "Реакция (MedDRA)": df["reaction"],
            "Сообщений": df["reports"],
            "Доля": (df["share"] * 100).round(1).astype(str) + "%",
            "PRR": df["prr"].round(2),
            "PRR, нижн. 95%": df["prr_low"].round(2),
            "χ²": df["chi2"].round(1),
            "В инструкции": df["in_label"].map({True: "да", False: "не найдено", None: "—"}),
            "Итог": df["verdict"],
        })
        st.dataframe(table, hide_index=True, width="stretch")
        st.markdown(
            r"""
**Как считается сигнал.** По всей базе FAERS строится таблица 2×2: сообщения с препаратом / без,
с реакцией / без. **PRR** (proportional reporting ratio) показывает, во сколько раз реакцию
упоминают с этим препаратом чаще, чем со всеми остальными:

$$PRR = \frac{a/(a+b)}{c/(c+d)}$$

где $a$ — сообщения с препаратом и реакцией, $a+b$ — все сообщения с препаратом,
$c$ — сообщения с реакцией без препарата, $c+d$ — все сообщения без препарата.
Реакция выделяется по критериям Эванса: $a \ge 3$, $PRR \ge 2$, $\chi^2 \ge 4$.
Если она выделяется и при этом не найдена в инструкции — это **кандидат в сигнал**:
повод для проверки, а не доказательство вреда.

Сверка с инструкцией идёт по словам, поэтому синонимы иногда не распознаются —
«не найдено» означает «стоит посмотреть», а не «точно отсутствует».
"""
        )

# ---------- Инструкция ----------
with tab_label:
    for label in res.get("labels", []):
        st.subheader(label["generic_name"])
        st.caption(
            f"Торговые названия: {', '.join(label['brand_names']) or '—'} · "
            f"производитель: {label['manufacturer']} · версия {label['effective_time']}"
        )
        if label["url"]:
            st.markdown(f"[Открыть на DailyMed]({label['url']})")
        chosen = res["routed"].get(label["generic_name"], [])
        st.caption("⭐ — разделы, которые маршрутизатор передал синтезатору")
        for field, text in label["sections"].items():
            mark = "⭐ " if field in chosen else ""
            with st.expander(f"{mark}{SECTIONS[field]} ({len(text):,} симв.)".replace(",", " ")):
                st.write(text)

# ---------- Трасса ----------
with tab_trace:
    st.caption("Журнал одного запроса: какой агент что получил и вернул.")
    for i, s in enumerate(res["trace"], 1):
        st.markdown(f"**{i}. {s['agent']}** · _{s['pattern']}_ · {s['seconds']} с")
        st.write(s["result"])

# ---------- Архитектура ----------
with tab_arch:
    st.graphviz_chart(
        """
digraph {
  rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#f4f1ea", fontname="Helvetica"];
  ui [label="Интерфейс\\n(Streamlit)", fillcolor="#dbeafe"];
  orch [label="Оркестратор\\n(порядок шагов в коде)"];
  norm [label="Нормализатор\\nназваний"];
  lab [label="Агент DailyMed\\n(инструкции)"];
  fa [label="Агент FAERS\\n(сообщения)"];
  an [label="Аналитик\\nPRR + сверка"];
  rt [label="Маршрутизатор\\n(LLM выбирает разделы)", fillcolor="#fde68a"];
  syn [label="Синтезатор\\n(LLM пишет сводку)", fillcolor="#fde68a"];
  cr [label="Критик\\n(проверка ссылок и чисел)"];
  ui -> orch -> norm;
  norm -> lab [label="параллельно"]; norm -> fa [label="параллельно"];
  lab -> an; fa -> an; an -> rt -> syn;
  syn -> cr [label="черновик"]; cr -> syn [label="замечания\\n(≤2 кругов)", style=dashed];
  cr -> ui [label="сводка"];
}
"""
    )
    st.markdown(
        """
| Паттерн (лекция 3) | Где в приложении |
|---|---|
| Цепочка (конвейер) | нормализация → источники → аналитик → синтез |
| Параллельное выполнение | агенты DailyMed и FAERS работают одновременно |
| Маршрутизация | LLM выбирает разделы инструкции под вопрос |
| Рефлексия | критик проверяет сводку и возвращает её на доработку |

Порядок шагов задан кодом (workflow): так поведение предсказуемо и проверяемо.
Модель принимает решения в двух местах — в маршрутизаторе и в синтезаторе.
"""
    )
