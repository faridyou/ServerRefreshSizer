"""Launch the local UI: 127.0.0.1 only, port 8765 or the next free one, then open the default browser."""

from __future__ import annotations

import socket
import threading
import webbrowser
from collections.abc import Callable
from typing import Any

HOST = "127.0.0.1"  # never bind to another interface: the UI has no authentication


def port_free(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(start: int = 8765, tries: int = 50) -> int:
    for p in range(start, start + tries):
        if port_free(p):
            return p
    raise RuntimeError(f"no free port in {start}-{start + tries - 1}")


def build_server(port: int) -> Any:
    import uvicorn

    from refreshsizer.ui.app import create_app

    config = uvicorn.Config(create_app(), host=HOST, port=port, log_level="warning")
    return uvicorn.Server(config)


def setup_log() -> str:
    """Jobs and errors are logged to data/logs/ui.log (attach it when reporting a problem)."""
    import logging

    from refreshsizer.paths import data_dir

    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "ui.log"
    h = logging.FileHandler(f, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("refreshsizer")
    root.addHandler(h)
    root.setLevel(logging.INFO)
    return str(f)


def serve(port: int = 8765, open_browser: bool = True, echo: Callable[[str], Any] = print) -> None:
    logfile = setup_log()
    p = find_free_port(port)
    url = f"http://{HOST}:{p}/"
    server = build_server(p)
    echo(f"RefreshSizer UI: {url}  (local only - bound to {HOST}; press Ctrl+C to stop)")
    echo(f"log file: {logfile}")
    if p != port:
        echo(f"port {port} was busy; using {p}")
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    server.run()
