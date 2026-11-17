"""A loopback-only HTTPS fixture origin, not a forwarding proxy.

START_MODULE_CONTRACT
PURPOSE: Serve unchanged committed connector fixtures by host/path with scripted statuses and stalls.
SCOPE: Committed search/refusal fixtures, response sequences, Refresh/redirect headers and cancellable signal gates/hangs.
DEPENDS: cryptography (a throwaway certificate); stdlib http.server,ssl,tempfile,threading,collections,dataclasses,datetime,pathlib,urllib.parse.
LINKS: M-CDP-SIM, R-CDP-SIM-INFRA.fixtures, R-CDP-SIM-S1, R-CDP-SIM-S2, R-CDP-SIM-S3, R-CDP-SIM-S4, R-CDP-SIM-S5, R-CDP-SIM-S6, R-CDP-SIM-S7, R-CDP-SIM-S9.
END_MODULE_CONTRACT
START_MODULE_MAP
  Reply: Describe a fixture response, including a status, headers or an intentional stall.
  Request: Record what the real browser requested and which scripted response it received.
  FixtureServer: Bind HTTPS to 127.0.0.1, select replies under a lock and stop held requests on reset/exit.
  fixture_bytes: Read only a fixture in the fixed committed-fixture inventory.
END_MODULE_MAP
"""

from __future__ import annotations

import ssl
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# START_BLOCK_FIXTURE_INVENTORY
ROOT = Path(__file__).resolve().parents[4]
# No capture, generated page or arbitrary filesystem path can be registered.
FIXTURES = {
    "ozon_search": ("packages/ozon-connector/tests/fixtures/search_composer_live.json", "application/json"),
    "dns_search": ("packages/dns-connector/tests/fixtures/search_grid.html", "text/html"),
    "citilink_search": ("packages/citilink-connector/tests/fixtures/search_grid.html", "text/html"),
    "avito_search": ("packages/avito-connector/tests/fixtures/js_items_live.json", "application/json"),
    "refusal": ("packages/lamoda-connector/tests/fixtures/refusal_page_live.html", "text/html"),
}


def fixture_bytes(name: str) -> bytes:
    return (ROOT / FIXTURES[name][0]).read_bytes()


@dataclass(frozen=True)
class Reply:
    fixture: str
    status: int = 200
    headers: tuple[tuple[str, str], ...] = ()
    hang: bool = False
    stall_body: bool = False
    wait_for: threading.Event | None = None
    arrived: threading.Event | None = None


@dataclass(frozen=True)
class Request:
    host: str
    target: str
    status: int | None
    fixture: str | None
    peer: str
    stalled: bool = False

    @property
    def path(self) -> str:
        return urlsplit(self.target).path


# END_BLOCK_FIXTURE_INVENTORY
# START_BLOCK_HTTPS_SERVER


def _load_throwaway_cert(context: ssl.SSLContext) -> None:
    """A self-signed certificate made for this run only, so no private key is committed.

    Chrome runs with --ignore-certificate-errors, so the certificate only has to exist.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cdp-sim.localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    with tempfile.TemporaryDirectory(prefix="cdp-sim-tls-") as folder:
        cert_path, key_path = Path(folder, "cert.pem"), Path(folder, "key.pem")
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        context.load_cert_chain(cert_path, key_path)


class FixtureServer:
    """Replies repeat their last step; unregistered host/path pairs get an empty 404.

    The PEM key is public test material for this local server, not a credential.
    No handler resolves a host, fetches a URL, or serves a caller-supplied path.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: dict[tuple[str, str], deque[Reply]] = {}
        self._requests: list[Request] = []
        self._release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                self.request.settimeout(3.0)
                super().setup()

            def do_GET(self) -> None:
                owner._respond(self)

            def log_message(self, format: str, *args: object) -> None:
                # No raw page, URL query or machine detail goes into pytest logs.
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        _load_throwaway_cert(context)
        self._httpd.socket = context.wrap_socket(self._httpd.socket, server_side=True, do_handshake_on_connect=False)
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def address(self) -> tuple[str, int]:
        return self._httpd.server_address

    @property
    def requests(self) -> tuple[Request, ...]:
        with self._lock:
            return tuple(self._requests)

    def route(self, host: str, path: str, *replies: Reply) -> None:
        if not replies or not path.startswith("/") or urlsplit(path).query:
            raise ValueError("a route needs a host, query-free path and at least one reply")
        for reply in replies:
            fixture_bytes(reply.fixture)  # Validate the fixed inventory before a request arrives.
            if not 200 <= reply.status <= 599:
                raise ValueError("unsupported scripted status")
        with self._lock:
            self._routes[(host.lower(), path)] = deque(replies)

    def reset(self) -> None:
        """Release the previous scenario's hangs and forget its routes and ledger."""
        with self._lock:
            self._release.set()
            self._release = threading.Event()
            self._routes.clear()
            self._requests.clear()

    # START_CONTRACT: _respond
    # PURPOSE: Select one scripted reply without any upstream I/O, then write fixture bytes or hold the connection.
    # INPUTS: A GET on the loopback listener; Host and path select an explicitly registered route.
    # OUTPUTS: Scripted status/body/headers, empty 404, or no response until reset/shutdown.
    # SIDE_EFFECTS: Append a request record; waiting never holds the route lock.
    # END_CONTRACT: _respond
    def _respond(self, handler: BaseHTTPRequestHandler) -> None:
        host = (urlsplit("https://" + handler.headers.get("Host", "")).hostname or "").lower()
        path = urlsplit(handler.path).path
        with self._lock:
            steps = self._routes.get((host, path))
            reply = steps[0] if steps else None
            if steps and len(steps) > 1:
                steps.popleft()
            release = self._release
            self._requests.append(
                Request(
                    host,
                    handler.path,
                    None if reply and reply.hang else reply.status if reply else 404,
                    reply.fixture if reply else None,
                    handler.client_address[0],
                    bool(reply and (reply.hang or reply.stall_body)),
                )
            )
        if reply and reply.arrived:
            reply.arrived.set()
        if reply and reply.wait_for:
            # A real Refresh navigation waits here until the test signals that
            # the fixture origin has cleared the challenge. Reset also cancels
            # this gate, so a failed/cancelled test cannot strand a server thread.
            while not reply.wait_for.wait(0.05):
                if release.is_set():
                    handler.close_connection = True
                    return
        if reply and reply.hang:
            release.wait()
            return
        body = fixture_bytes(reply.fixture) if reply else b""
        try:
            handler.send_response(reply.status if reply else 404)
            handler.send_header(
                "Content-Type", (FIXTURES[reply.fixture][1] if reply else "text/plain") + "; charset=utf-8"
            )
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Connection", "close")
            handler.send_header("X-Content-Type-Options", "nosniff")
            if reply:
                for name, value in reply.headers:
                    handler.send_header(name, value)
            handler.end_headers()
            if reply and reply.stall_body:
                # A prefix of the committed bytes commits the document, but never
                # completes parsing or fires DOMContentLoaded/load. No invented HTML.
                handler.wfile.write(body[: min(128, len(body) - 1)])
                handler.wfile.flush()
                release.wait()
            else:
                handler.wfile.write(body)
        except (ConnectionError, ssl.SSLError, TimeoutError):
            # Chrome closes a refused/timed-out owned tab before the held reply ends.
            pass
        finally:
            handler.close_connection = True

    def __enter__(self) -> FixtureServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.reset()
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        assert not self._thread.is_alive(), "simulator HTTPS thread did not stop"


# END_BLOCK_HTTPS_SERVER
