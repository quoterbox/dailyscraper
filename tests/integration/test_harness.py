from __future__ import annotations

import os
import socket
import socketserver
import sys
import threading
from pathlib import Path

from tests.harness.connect_proxy import ProgrammableConnectProxy
from tests.harness.process import ManagedProcess


def test_managed_process_can_simulate_abrupt_parent_loss(tmp_path: Path) -> None:
    process = ManagedProcess.start(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        cwd=tmp_path,
        environment=os.environ.copy(),
        stdout_path=tmp_path / "crash.stdout.log",
        stderr_path=tmp_path / "crash.stderr.log",
    )
    try:
        killed_pids = process.crash_tree(timeout=5)
    finally:
        process.terminate_tree()

    assert process.pid in killed_pids
    assert process.is_running is False


def test_connect_proxy_rejects_with_407_in_proxy_auth_mode() -> None:
    with ProgrammableConnectProxy("proxy_auth") as proxy:
        response = _send_connect(proxy, "example.test:443")

    assert response.startswith(b"HTTP/1.1 407 Proxy Authentication Required")


def test_connect_proxy_tunnels_to_upstream_in_tunnel_mode() -> None:
    with _EchoServer() as echo, ProgrammableConnectProxy("tunnel") as proxy:
        with socket.create_connection(
            (proxy.endpoint.host, proxy.endpoint.port), timeout=5
        ) as client:
            client.sendall(f"CONNECT 127.0.0.1:{echo.port} HTTP/1.1\r\n\r\n".encode())
            established = _read_until(client, b"\r\n\r\n")
            client.sendall(b"ping")
            echoed = client.recv(4)

    assert established.startswith(b"HTTP/1.1 200 Connection Established")
    assert echoed == b"ping"


def _send_connect(proxy: ProgrammableConnectProxy, authority: str) -> bytes:
    with socket.create_connection((proxy.endpoint.host, proxy.endpoint.port), timeout=5) as client:
        client.sendall(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
        return _read_until(client, b"\r\n\r\n")


def _read_until(client: socket.socket, marker: bytes) -> bytes:
    data = bytearray()
    while marker not in data:
        chunk = client.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(4096):
            self.request.sendall(data)


class _EchoServer:
    def __init__(self) -> None:
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _EchoServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
