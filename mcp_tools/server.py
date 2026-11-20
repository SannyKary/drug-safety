"""Запуск: python -m mcp_tools.server [stdio|sse]."""

import argparse
from functools import wraps
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .calculators import NETWORK_CALCULATORS, STDIO_CALCULATORS

def validate_tool(calculator):
    """Показывает ожидаемую ошибку формулы в результате MCP."""

    @wraps(calculator)
    def run(**arguments):
        try:
            return calculator(**arguments)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return run


def make_server(network: bool) -> MCPServer:
    server = MCPServer("Досье — сетевые калькуляторы" if network else "Досье — локальные калькуляторы")
    groups = {"bmi": "Антропометрия", "bsa": "Антропометрия", "gcs": "Неврология / реанимация",
              "egfr": "Нефрология", "qtc": "Кардиология"}
    for calculator in NETWORK_CALCULATORS if network else STDIO_CALCULATORS:
        server.add_tool(
            validate_tool(calculator),
            meta={"category": groups[calculator.__name__]},
            structured_output=True,
        )

    @server.tool(structured_output=True)
    async def list_calculators() -> dict[str, list[dict[str, Any]]]:
        """Каталог калькуляторов с описанием, единицами и границами аргументов."""
        tools = await server.list_tools()
        return {"calculators": [
            {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
            for tool in tools if tool.name != "list_calculators"
        ]}

    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("transport", choices=("stdio", "sse"), nargs="?", default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.transport == "sse":
        make_server(network=True).run("sse", host="127.0.0.1", port=args.port)
    else:
        make_server(network=False).run("stdio")


if __name__ == "__main__":
    main()
