"""Общий клиент для трёх транспортов, с записью сообщений на границе транспорта."""

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage

ROOT = Path(__file__).resolve().parent.parent
ARCA_URL = "https://app.arca.inc/api/public/mcp"
SOURCES = {
    "stdio": "Локальный процесс · stdio",
    "sse": "Локальный сервер · SSE",
    "remote": "Внешний сервер · FDA / PubMed",
}


class RecordedStream:
    """Добавляет копии отправленных и полученных сообщений в журнал."""

    def __init__(self, stream, direction, packets):
        self.stream = stream
        self.direction = direction
        self.packets = packets

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *exc_info):
        return await self.stream.__aexit__(*exc_info)

    def __aiter__(self):
        return self

    @property
    def last_context(self):
        return getattr(self.stream, "last_context", None)

    async def __anext__(self):
        item = await self.stream.__anext__()
        self.record(item)
        return item

    async def send(self, item):
        self.record(item)
        await self.stream.send(item)

    def record(self, item):
        if isinstance(item, SessionMessage):
            data = item.message.model_dump(mode="json", by_alias=True, exclude_unset=True)
            self.packets.append({"direction": self.direction, "message": data})


@asynccontextmanager
async def transport(source):
    if source == "stdio":
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_tools.server"], cwd=ROOT
        )
        async with stdio_client(parameters) as streams:
            yield streams
    elif source == "sse":
        port = int(os.getenv("DOSSIER_MCP_PORT", "8000"))
        async with sse_client(f"http://127.0.0.1:{port}/sse", timeout=1.5) as streams:
            yield streams
    elif source == "remote":
        async with streamable_http_client(ARCA_URL) as streams:
            yield streams
    else:
        raise ValueError(f"Неизвестный тип подключения: {source}")


def sanitize(arguments, schema):
    """Приводит скалярные типы. Добавляет только явно объявленные значения по умолчанию."""
    clean = dict(arguments)
    for name, field in schema.get("properties", {}).items():
        if name not in clean and "default" in field:
            clean[name] = field["default"]
        value = clean.get(name)
        if not isinstance(value, str):
            continue
        value = value.strip()
        try:
            if field.get("type") == "integer":
                value = int(value)
            elif field.get("type") == "number":
                value = float(value.replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"Введите число в поле «{field.get('description', name)}».") from exc
        clean[name] = value
    return clean


async def request(source, packets, tool=None, arguments=None):
    # Один общий предел для подключения, инициализации и вызова.
    async with asyncio.timeout(12 if source == "remote" else 8):
        async with transport(source) as (rx, tx):
            async with ClientSession(
                RecordedStream(rx, "Ответ", packets), RecordedStream(tx, "Запрос", packets),
                read_timeout_seconds=6 if source == "remote" else 4,
            ) as session:
                await session.initialize()
                listing = await session.list_tools()
                if tool is None:
                    return [entry.model_dump(mode="json", by_alias=True, exclude_none=True)
                            for entry in listing.tools]
                if source == "sse":
                    arguments = sanitize(arguments, tool["inputSchema"])
                answer = await session.call_tool(tool["name"], arguments)
                return answer.model_dump(mode="json", by_alias=True, exclude_none=True)


def error_detail(error):
    if isinstance(error, BaseExceptionGroup):
        return "; ".join(error_detail(item) for item in error.exceptions)
    return str(error) or type(error).__name__


def retryable(error):
    if isinstance(error, BaseExceptionGroup):
        return any(retryable(item) for item in error.exceptions)
    return isinstance(error, (OSError, TimeoutError)) or type(error).__name__ in {
        "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
    }


def exchange(source, tool=None, arguments=None):
    """Возвращает данные, сообщения и ошибку. Повторяет только сетевой сбой локального SSE."""
    packets = []
    for attempt in range(3 if source == "sse" else 1):
        try:
            data = asyncio.run(request(source, packets, tool, arguments))
            return {"data": data, "packets": packets, "error": None}
        except Exception as exc:
            if source != "sse" or attempt == 2 or not retryable(exc):
                if isinstance(exc, TimeoutError):
                    message = "Сервер не ответил вовремя."
                else:
                    message = error_detail(exc)
                return {"data": None, "packets": packets, "error": message}
            time.sleep(0.25 * (attempt + 1))
