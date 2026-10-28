from __future__ import annotations

import base64
import select
import socket
import socketserver
import threading
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

ConnectProxyMode = Literal["tunnel", "proxy_auth"]


@dataclass(frozen=True)
class ConnectProxyEndpoint:
    host: str
    port: int


@dataclass
class ProxyLog:
    tunnels: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    rejected: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class ProgrammableConnectProxy:
    """A local HTTP proxy for tests.

    ``tunnel`` relays CONNECT tunnels and plain absolute-URI requests;
    ``proxy_auth`` answers everything with 407. With ``credentials`` a tunnel
    proxy also demands Basic authentication, as real paid proxies do.
    """

    def __init__(
        self, mode: ConnectProxyMode, *, credentials: tuple[str, str] | None = None
    ) -> None:
        self.mode = mode
        self.log = ProxyLog()
        handler = _handler_for(mode, credentials, self.log)
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


REJECT = (
    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
    b'Proxy-Authenticate: Basic realm="dailyscraper test proxy"\r\n'
    b"Content-Length: 0\r\n"
    b"Connection: close\r\n\r\n"
)


def _handler_for(mode: ConnectProxyMode, credentials: tuple[str, str] | None, log: ProxyLog):
    expected = None
    if credentials is not None:
        token = base64.b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode()
        expected = f"Basic {token}"

    class ConnectProxyHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(10)
            request_head = _read_request_head(self.request)
            if not request_head:
                return
            head, _, rest = request_head.partition(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            parts = lines[0].split()
            if len(parts) < 2:
                return
            headers = _headers(lines[1:])
            if mode == "proxy_auth" or (
                expected is not None and headers.get("proxy-authorization") != expected
            ):
                with log.lock:
                    log.rejected += 1
                self.request.sendall(REJECT)
                return

            method = parts[0].upper()
            target = parts[1].decode("ascii")
            if method == b"CONNECT":
                host, port = _split_authority(target)
                with log.lock:
                    log.tunnels.append(target)
                with socket.create_connection((host, port), timeout=10) as upstream:
                    self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    _relay_bidirectionally(self.request, upstream)
                return

            url = urlsplit(target)
            with log.lock:
                log.requests.append(target)
            path = url.path or "/"
            if url.query:
                path += f"?{url.query}"
            forwarded = [b" ".join([method, path.encode("ascii"), parts[2]])]
            forwarded += [
                line
                for line in lines[1:]
                if not line.lower().startswith((b"proxy-", b"connection:"))
            ]
            forwarded.append(b"Connection: close")
            with socket.create_connection((url.hostname, url.port or 80), timeout=10) as upstream:
                upstream.sendall(b"\r\n".join(forwarded) + b"\r\n\r\n" + rest)
                _relay_bidirectionally(self.request, upstream)

    return ConnectProxyHandler


def _headers(lines: list[bytes]) -> dict[str, str]:
    headers = {}
    for line in lines:
        name, separator, value = line.decode("latin-1").partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    return headers


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
