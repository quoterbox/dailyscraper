from __future__ import annotations

import select
import socket
import socketserver
import threading
from dataclasses import dataclass
from typing import Literal

ConnectProxyMode = Literal["tunnel", "proxy_auth"]


@dataclass(frozen=True)
class ConnectProxyEndpoint:
    host: str
    port: int


class ProgrammableConnectProxy:
    def __init__(self, mode: ConnectProxyMode) -> None:
        self.mode = mode
        handler = _handler_for(mode)
        self._server = _ThreadingTcpServer(("127.0.0.1", 0), handler)
        host, port = self._server.server_address
        self.endpoint = ConnectProxyEndpoint(str(host), int(port))
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"test-connect-proxy-{mode}-{port}",
            daemon=True,
        )

    def __enter__(self) -> ProgrammableConnectProxy:
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _ThreadingTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _handler_for(mode: ConnectProxyMode):
    class ConnectProxyHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(10)
            request_head = _read_request_head(self.request)
            if not request_head:
                return
            request_line = request_head.split(b"\r\n", 1)[0]
            parts = request_line.split()
            if len(parts) < 2 or parts[0].upper() != b"CONNECT":
                self.request.sendall(
                    b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n"
                )
                return

            if mode == "proxy_auth":
                self.request.sendall(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: Basic realm="dailyscraper test proxy"\r\n'
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
                return

            host, port = _split_authority(parts[1].decode("ascii"))
            with socket.create_connection(
                (host, port),
                timeout=10,
            ) as upstream:
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                _relay_bidirectionally(self.request, upstream)

    return ConnectProxyHandler


def _read_request_head(client: socket.socket) -> bytes:
    chunks = bytearray()
    while b"\r\n\r\n" not in chunks:
        chunk = client.recv(4096)
        if not chunk:
            return b""
        chunks.extend(chunk)
        if len(chunks) > 64 * 1024:
            raise ValueError("CONNECT request head exceeds 64 KiB")
    return bytes(chunks)


def _split_authority(authority: str) -> tuple[str, int]:
    host, separator, raw_port = authority.rpartition(":")
    if not separator or not host:
        raise ValueError(f"Invalid CONNECT authority: {authority!r}")
    return host.strip("[]"), int(raw_port)


def _relay_bidirectionally(
    client: socket.socket,
    upstream: socket.socket,
) -> None:
    sockets = (client, upstream)
    while True:
        readable, _, exceptional = select.select(
            sockets,
            (),
            sockets,
            1.0,
        )
        if exceptional:
            return
        if not readable:
            continue
        for source in readable:
            data = source.recv(64 * 1024)
            if not data:
                return
            target = upstream if source is client else client
            target.sendall(data)
