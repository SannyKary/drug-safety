"""Запуск локального MCP-сервера и Streamlit одной командой."""

import os
import socket
import subprocess
import sys
import time


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main():
    port = int(os.getenv("DOSSIER_MCP_PORT", "8000"))
    app_port = int(os.getenv("DOSSIER_UI_PORT", "8501"))
    for target in (port, app_port):
        if listening(target):
            raise SystemExit(f"Порт {target} занят. Остановите предыдущий запуск или выберите другой порт.")
    server = subprocess.Popen([sys.executable, "-m", "mcp_tools.server", "sse", "--port", str(port)])
    try:
        deadline = time.monotonic() + 15
        while not listening(port):
            if server.poll() is not None or time.monotonic() >= deadline:
                raise SystemExit(f"Сетевой MCP-сервер не запустился на порту {port}. См. его журнал выше.")
            time.sleep(0.15)
        return subprocess.call([
            sys.executable, "-m", "streamlit", "run", "app.py",
            "--server.address=127.0.0.1", f"--server.port={app_port}",
            "--server.headless=true", "--browser.gatherUsageStats=false",
        ])
    except KeyboardInterrupt:
        return 0
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=4)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    raise SystemExit(main())
