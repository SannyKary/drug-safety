"""Экран практического задания в существующем Streamlit-приложении."""

import json

import streamlit as st

from .client import SOURCES, exchange

REMOTE_TOOLS = {"search_medical", "search_papers"}
GROUPS = {
    "bmi": "Антропометрия",
    "bsa": "Антропометрия",
    "gcs": "Неврология / реанимация",
    "egfr": "Нефрология",
    "qtc": "Кардиология",
}
NAMES = {
    "bmi": "Индекс массы тела · ИМТ / BMI",
    "bsa": "Площадь поверхности тела · ППТ / BSA",
    "gcs": "Шкала комы Глазго · ШКГ / GCS",
    "egfr": "Расчётная СКФ · eGFR",
    "qtc": "Интервал QT с поправкой на частоту · QTc",
    "search_medical": "Поиск медицинских и регуляторных записей · FDA",
    "search_papers": "Поиск публикаций · PubMed",
    "list_calculators": "Методики, единицы и границы · каталог",
}


@st.cache_data(ttl=600, show_spinner=False)
def remote_catalog():
    result = exchange("remote")
    if result["error"]:
        raise RuntimeError(result["error"])
    return result


@st.cache_data(ttl=900, show_spinner=False)
def remote_answer(tool, arguments):
    result = exchange("remote", tool, arguments)
    if result["error"] or (result["data"] and result["data"].get("isError")):
        raise RemoteCallFailed(result)
    return result


class RemoteCallFailed(Exception):
    def __init__(self, result):
        self.result = result


FIELD_LABELS = {
    "query": "Поисковый запрос",
    "numResults": "Число результатов",
    "minYear": "Год публикации от",
    "maxYear": "Год публикации до",
    "publicationTypes": "Тип публикации",
    "openAccessOnly": "Только открытый доступ",
    "pmcOnly": "Только полный текст в PubMed Central",
    "minCitedByCount": "Минимальное число цитирований",
    "sourceTypes": "Источники",
    "publicSourceFilters": "Фильтры источников (JSON)",
}


def field_value(name, schema, key, required):
    label = FIELD_LABELS.get(name) or schema.get("description") or schema.get("title") or name
    # Длинное описание внешней схемы остаётся подсказкой.
    if len(label) > 95:
        st.caption(label)
        label = name
    default = schema.get("default")
    choices = schema.get("enum")
    kind = schema.get("type")
    if choices:
        index = choices.index(default) if default in choices else None
        return st.selectbox(label, choices, index=index, key=key)
    if kind == "boolean":
        if required or default is not None:
            return st.checkbox(label, value=bool(default), key=key)
        return st.selectbox(label, [True, False], index=None, format_func=lambda x: "Да" if x else "Нет", key=key)
    if kind in ("integer", "number"):
        cast = int if kind == "integer" else float
        return st.number_input(
            label, value=cast(default) if default is not None else None,
            min_value=cast(schema["minimum"]) if "minimum" in schema else None,
            max_value=cast(schema["maximum"]) if "maximum" in schema else None,
            step=1 if kind == "integer" else 0.1, key=key,
        )
    if kind == "array" and schema.get("items", {}).get("enum"):
        return st.multiselect(label, schema["items"]["enum"], default=default or [], key=key) or None
    if kind in ("object", "array") or not kind:
        raw = st.text_area(label, key=key, placeholder="JSON")
        if raw.strip():
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise ValueError(f"Проверьте JSON в поле «{label}».") from None
        return None
    return st.text_input(label, value=default or "", key=key).strip() or None


def exchange_steps(packets, *, call_only=False):
    replies = {
        packet["message"]["id"]: packet["message"]
        for packet in packets
        if packet["direction"] == "Ответ" and "id" in packet["message"]
        and ("result" in packet["message"] or "error" in packet["message"])
    }
    for packet in packets:
        request = packet["message"]
        if packet["direction"] != "Запрос" or "method" not in request:
            continue
        method = request["method"]
        if call_only and method != "tools/call":
            continue
        request_id = request.get("id")
        reply = replies.get(request_id, {})
        data = reply.get("result") or {}
        if request_id is None:
            detail = "Уведомление без ожидания ответа"
        elif "error" in reply or data.get("isError"):
            detail = "Сервер вернул ошибку"
        elif not reply:
            detail = "Ответ не получен"
        elif method == "initialize":
            name = data.get("serverInfo", {}).get("name", "Сервер")
            detail = f"{name} · MCP {data.get('protocolVersion', '—')}"
        elif method == "tools/list":
            names = ", ".join(tool["name"] for tool in data.get("tools", []))
            detail = names or "Каталог пуст"
        else:
            output = data.get("structuredContent") or {}
            detail = f"{output['value']} {output.get('unit', '')}".strip() if "value" in output else "Результат получен"
        action = {
            "initialize": "Предложили версию MCP",
            "notifications/initialized": "Клиент сообщил о готовности",
            "tools/list": "Запросили каталог",
        }.get(method, method)
        if method == "tools/call":
            params = request["params"]
            action = f"Вызвали {params['name']}"
            arguments = ", ".join(f"{key} = {value}" for key, value in params.get("arguments", {}).items())
            if arguments:
                action = f"{action} ({arguments})"
        suffix = f" (ID {request_id})" if request_id is not None else ""
        yield f"• {action} — {detail}{suffix}"


def inspector(source, catalog, result):
    st.subheader("Инспектор JSON-RPC")
    st.caption(f"Подключение: {SOURCES[source]}. Одинаковый ID связывает запрос с ответом в пределах одного соединения.")
    sessions = [("Загрузка каталога", catalog)]
    if result is not None:
        sessions.append(("Вызов инструмента", result))
    for title, exchange_result in sessions:
        packets = exchange_result["packets"]
        steps = list(exchange_steps(packets, call_only=exchange_result is result))
        if not steps and exchange_result is result:
            steps = list(exchange_steps(packets))
        if steps:
            st.markdown(f"**{title}**")
            st.text("\n".join(steps))
    if result and result["packets"]:
        st.download_button(
            "Скачать сообщения вызова",
            data=json.dumps(result["packets"], ensure_ascii=False, indent=2),
            file_name="mcp-trace.json",
            mime="application/json",
        )
    if any(exchange_result["packets"] for _, exchange_result in sessions):
        with st.expander("Исходные сообщения JSON-RPC"):
            for title, exchange_result in sessions:
                if not exchange_result["packets"]:
                    continue
                st.markdown(f"**{title}**")
                for packet in exchange_result["packets"]:
                    message = packet["message"]
                    description = message.get("method", f"id {message.get('id', '—')}")
                    st.caption(f"{packet['direction']} · {description}")
                    raw = json.dumps(message, ensure_ascii=False, indent=2)
                    st.code(raw, language="json", height=220 if raw.count("\n") > 14 else "content")


def show_result(packet):
    if packet["error"]:
        st.error(f"Вызов завершился ошибкой: {packet['error']}")
        return
    answer = packet["data"]
    if answer.get("isError"):
        st.error("Сервер отклонил вызов. Проверьте ввод.")
        for content in answer.get("content", []):
            st.text(content.get("text", ""))
        return
    data = answer.get("structuredContent")
    if isinstance(data, dict) and "value" in data and "unit" in data:
        st.metric("Результат", f"{data['value']} {data['unit']}")
        st.badge(data["category"], color=data.get("color", "gray"))
        st.write(data["interpretation"])
        st.table([{"Компонент": key, "Значение": str(value)} for key, value in data["components"].items()])
        st.link_button("Методика и первоисточник", data["source"])
        return
    if isinstance(data, dict) and isinstance(data.get("papers"), list):
        papers = data["papers"]
        st.write(f"Публикаций в ответе: {len(papers)}")
        for paper in papers:
            st.text(paper.get("title", "Публикация"))
            st.caption(f"{paper.get('journal', '')} · {paper.get('year', '')}")
            if str(paper.get("pmid", "")).isdigit():
                st.link_button("Открыть в PubMed", f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/")
        return
    if data is not None:
        st.json(data, expanded=1)
        return
    for item in answer.get("content", []):
        if item.get("type") == "text":
            raw = item.get("text", "")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                st.text(raw)
            else:
                st.json(parsed, expanded=1)
        else:
            st.json(item, expanded=False)


def render():
    st.title("MCP-инструменты")
    source = st.radio("Подключение", list(SOURCES), format_func=SOURCES.get, horizontal=True)
    if source == "remote":
        st.caption("Учебный поиск по публичным источникам. Вводите тему или название препарата.")
    if st.button("Обновить каталог"):
        remote_catalog.clear()
        st.session_state.pop("mcp_catalog", None)
        st.session_state.pop("mcp_answer", None)
    saved = st.session_state.get("mcp_catalog")
    if not saved or saved[0] != source:
        st.session_state.pop("mcp_answer", None)
        with st.spinner("Получаем список инструментов через MCP…"):
            try:
                catalog = remote_catalog() if source == "remote" else exchange(source)
            except RuntimeError as exc:
                catalog = {"data": None, "error": str(exc), "packets": []}
        st.session_state.mcp_catalog = (source, catalog)
    else:
        catalog = saved[1]
    if catalog["error"]:
        st.error(f"Каталог недоступен: {catalog['error']}")
        inspector(source, catalog, None)
        return

    tools = {tool["name"]: tool for tool in catalog["data"]
             if source != "remote" or tool["name"] in REMOTE_TOOLS}
    if source == "sse":
        groups = ["Все", *dict.fromkeys(GROUPS.values())]
        group = st.selectbox("Раздел", groups)
        if group != "Все":
            tools = {name: tool for name, tool in tools.items() if GROUPS.get(name) == group}
    names = list(tools)
    if not names:
        st.info("Подключение не вернуло подходящих инструментов.")
        inspector(source, catalog, None)
        return
    # Поиск работает прямо в списке Streamlit по русскому названию и английскому идентификатору.
    name = st.selectbox("Инструмент", names, format_func=lambda item: NAMES.get(item, item),
                        key=f"mcp-tool-{source}")
    tool = tools[name]
    if tool.get("description"):
        with st.expander("Описание инструмента"):
            st.text(tool["description"])
    required = set(tool["inputSchema"].get("required", []))
    values = {}
    errors = []
    with st.form(f"mcp-form-{source}-{name}"):
        for field, schema in tool["inputSchema"].get("properties", {}).items():
            try:
                value = field_value(field, schema, f"mcp-{source}-{name}-{field}", field in required)
                if value is not None:
                    values[field] = value
            except ValueError as exc:
                errors.append(str(exc))
        submitted = st.form_submit_button("Вызвать инструмент", type="primary")
    if submitted:
        st.session_state.pop("mcp_answer", None)
        missing = sorted(required - values.keys())
        if missing:
            errors.append("Заполните обязательные поля: " + ", ".join(missing))
        for error in errors:
            st.error(error)
        if not errors:
            with st.spinner("Выполняется вызов MCP…"):
                try:
                    packet = remote_answer(tool, values) if source == "remote" else exchange(source, tool, values)
                except RemoteCallFailed as exc:
                    packet = exc.result
            st.session_state.mcp_answer = (source, name, packet)
    last = st.session_state.get("mcp_answer")
    packet = last[2] if last and last[:2] == (source, name) else None
    if packet:
        show_result(packet)
    inspector(source, catalog, packet)
