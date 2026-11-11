"""A tiny localhost server that gives one run of one agent its Council tools.

Why it exists: an agent's tool process used to open the room database itself, which is impossible for a server room and would
hand the agent a real credential. Instead Council starts a broker on 127.0.0.1 for the run, mints the agent a short-lived token
and gives the agent's tool only the broker's address and that token. The broker holds the real store (or, in an encrypted room,
the encrypting store), so the agent never holds a database, a server token or a room key.

A token is one agent in one room: it names the agent (posts are stamped with it, whatever the request says), the kinds of
message it may post (not decisions, tasks or system events), when it expires, and how many posts and reads it has. The broker
listens only on the loopback address, refuses any peer that is not local, and refuses a Host header that is not its own address
(so a web page cannot reach it through DNS rebinding). It logs nothing about requests.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from aletheore.council.agent_posts import MAX_READ_LIMIT, WORKER_POST_TYPES, build_post_draft, read_text
from aletheore.council.protocol import ProtocolError

DEFAULT_KINDS = ("claim", "challenge", "evidence", "question")
MAX_REQUEST_BYTES = 64 * 1024
_DISCARD_LIMIT = 1024 * 1024  # an oversized body up to this is read and thrown away so the client sees a clean 413
_AGENT_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
MAX_TTL_S = 24 * 3600


class _Token:
    __slots__ = ("agent", "kinds", "expires", "posts_left", "reads_left")

    def __init__(self, agent: str, kinds: frozenset[str], expires: float, posts: int, reads: int) -> None:
        self.agent, self.kinds, self.expires, self.posts_left, self.reads_left = agent, kinds, expires, posts, reads


class Broker:
    host = "127.0.0.1"

    def __init__(self, store, room_id: str, participant: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.store, self.room_id, self.participant, self.clock = store, room_id, participant, clock
        self._tokens: dict[str, _Token] = {}  # sha256(token) -> record; the token itself is never kept
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.port = 0

    # -- lifecycle ---------------------------------------------------------------------------------------------------
    def start(self) -> str:
        if self._server is not None:
            return self.url
        broker = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "council-broker"
            sys_version = ""

            def log_message(self, *args) -> None:  # never log requests: they carry tokens and message text
                pass

            def reply(self, status: int, payload: dict) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = True

            def do_GET(self) -> None:
                broker._handle(self, "GET")

            def do_POST(self) -> None:
                broker._handle(self, "POST")

            def not_allowed(self) -> None:
                self.reply(405, {"detail": "method not allowed"})

            do_PUT = do_DELETE = do_PATCH = not_allowed

        self._server = ThreadingHTTPServer((self.host, 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, name=f"council-broker-{self.room_id}", daemon=True).start()
        return self.url

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        server, self._server = self._server, None
        with self._lock:
            self._tokens.clear()
        if server is not None:
            server.shutdown()
            server.server_close()

    # -- tokens ------------------------------------------------------------------------------------------------------
    def mint(self, agent_id: str, kinds: tuple[str, ...] = DEFAULT_KINDS, ttl_s: float = 3600, max_posts: int = 50, max_reads: int = 1000) -> str:
        if not isinstance(agent_id, str) or not _AGENT_RE.fullmatch(agent_id):
            raise ValueError("agent id must be 1 to 64 letters, digits, '.', '_' or '-'")
        if not kinds or not set(kinds) <= WORKER_POST_TYPES:
            raise ValueError(f"kinds must be some of: {', '.join(sorted(WORKER_POST_TYPES))}")
        if not 0 < ttl_s <= MAX_TTL_S:
            raise ValueError(f"ttl must be between 0 and {MAX_TTL_S} seconds")
        if max_posts < 0 or max_reads < 0:
            raise ValueError("limits cannot be negative")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[hashlib.sha256(token.encode()).hexdigest()] = _Token(agent_id, frozenset(kinds), self.clock() + ttl_s, max_posts, max_reads)
        return token

    @staticmethod
    def is_local(addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return ip.is_loopback

    # -- requests ----------------------------------------------------------------------------------------------------
    def _handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        try:
            if not self.is_local(h.client_address[0]) or h.headers.get("Host", "") != f"{self.host}:{self.port}":
                return h.reply(403, {"detail": "local requests only"})
            auth = h.headers.get("Authorization", "")
            token = auth[7:] if auth.lower().startswith("bearer ") else ""
            with self._lock:
                rec = self._tokens.get(hashlib.sha256(token.encode()).hexdigest()) if token else None
                if rec is not None and rec.expires <= self.clock():
                    rec = None
            if rec is None:
                return h.reply(401, {"detail": "missing, wrong or expired token"})
            parts = urlsplit(h.path)
            if method == "GET" and parts.path == "/status":
                return h.reply(200, {"room": self.room_id, "you": {"participant": self.participant, "agent": rec.agent},
                                     "host": self.store.host_of(self.room_id), "last_seq": self.store.last_seq(self.room_id)})
            if method == "GET" and parts.path == "/read":
                return self._read(h, rec, parse_qs(parts.query))
            if method == "POST" and parts.path == "/post":
                return self._post(h, rec)
            h.reply(404, {"detail": "not found"})
        except ConnectionError:
            h.reply(503, {"detail": "the room is temporarily unreachable"})
        except PermissionError as exc:
            h.reply(403, {"detail": str(exc) or "not permitted"})
        except KeyError:
            h.reply(404, {"detail": "not found"})
        except ProtocolError as exc:
            h.reply(422, {"detail": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read(self, h, rec: _Token, query: dict) -> None:
        try:
            after = int(query.get("after", ["0"])[0])
            limit = int(query.get("limit", ["50"])[0])
        except ValueError:
            return h.reply(422, {"detail": "after and limit must be whole numbers"})
        with self._lock:
            if rec.reads_left <= 0:
                return h.reply(429, {"detail": "this run has used all its reads"})
            rec.reads_left -= 1
        events = self.store.read_after(self.room_id, max(0, after), min(max(1, limit), MAX_READ_LIMIT))
        h.reply(200, {"text": read_text(events), "last_seq": events[-1].seq if events else max(0, after)})

    def _post(self, h, rec: _Token) -> None:
        try:
            length = int(h.headers.get("Content-Length", ""))
        except ValueError:
            return h.reply(411, {"detail": "Content-Length is required"})
        if length < 0 or length > MAX_REQUEST_BYTES:
            if 0 <= length <= _DISCARD_LIMIT:
                h.rfile.read(length)
            return h.reply(413, {"detail": "request too large"})
        try:
            raw = json.loads(h.rfile.read(length))
        except (ValueError, RecursionError):
            return h.reply(422, {"detail": "body must be JSON"})
        if not isinstance(raw, dict):
            return h.reply(422, {"detail": "body must be a JSON object"})
        kind = raw.get("type")
        if kind not in rec.kinds:
            return h.reply(403, {"detail": f"this token may post only: {', '.join(sorted(rec.kinds))}"})
        try:  # identity fields in the request are ignored: the stamp comes from the token
            draft = build_post_draft(self.room_id, self.participant, rec.agent, kind, raw.get("thread_id"), raw.get("text"),
                                     raw.get("reply_to"), raw.get("evidence"), allowed=rec.kinds)
        except ValueError as exc:
            return h.reply(422, {"detail": str(exc)})
        with self._lock:
            if rec.posts_left <= 0:
                return h.reply(429, {"detail": "this run has used all its posts"})
            rec.posts_left -= 1
        event = self.store.append(draft)
        h.reply(200, {"seq": event.seq})
