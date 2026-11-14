"""Telegram notifier: bounded queue, 1.5s spacing, 429 backoff, 3 retries.

A dead Telegram must never block the request path, so every call enqueues and a
single worker thread drains.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("fleet.tg")

API = "https://api.telegram.org"
SPACING = 1.5
MAX_RETRIES = 3


@dataclass
class _Message:
    kind: str  # "text" | "document"
    body: str
    caption: str = ""
    document: bytes | None = None
    filename: str = ""
    chat_id: str = ""
    thread_id: int = 0


class Telegram:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self._queue: queue.Queue[_Message] = queue.Queue(maxsize=100)
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="tg", daemon=True)
        self._worker.start()

    # -- public API ----------------------------------------------------------
    def send(self, html: str, caption: str = "", chat_id: str = "", thread_id: int = 0) -> None:
        self._enqueue(_Message("text", html, caption, chat_id=chat_id, thread_id=thread_id))

    def send_document(self, data: bytes, filename: str, caption: str = "") -> None:
        self._enqueue(_Message("document", "", caption, data, filename))

    def _enqueue(self, message: _Message) -> None:
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(message)
            except queue.Empty:
                log.debug("telegram queue race")

    # -- worker --------------------------------------------------------------
    def _run(self) -> None:
        with httpx.Client(timeout=20.0) as client:
            while True:
                message = self._queue.get()
                delay = SPACING
                for attempt in range(MAX_RETRIES):
                    ok, retry_after = self._deliver(client, message)
                    if ok:
                        break
                    if retry_after:
                        time.sleep(retry_after)
                    else:
                        time.sleep(SPACING * (2**attempt))
                else:
                    log.warning("telegram dropped a message after %d tries", MAX_RETRIES)
                time.sleep(delay)

    def _deliver(self, client: httpx.Client, message: _Message) -> tuple[bool, float]:
        try:
            chat = message.chat_id or self.chat_id
            extra = {"message_thread_id": str(message.thread_id)} if message.thread_id else {}
            if message.kind == "document":
                data = {"chat_id": chat, "caption": message.caption[:1000], **extra}
                resp = client.post(
                    f"{API}/bot{self.token}/sendDocument",
                    data=data,
                    files={"document": (message.filename, message.document or b"")},
                )
            else:
                text = message.body
                if message.caption:
                    text = f"<b>{message.caption}</b>\n{message.body}"
                resp = client.post(
                    f"{API}/bot{self.token}/sendMessage",
                    data={
                        "chat_id": chat,
                        "text": text[:4000],
                        "parse_mode": "HTML",
                        "disable_web_page_preview": "true",
                        **extra,
                    },
                )
            if resp.status_code == 429:
                retry = 5.0
                with contextlib.suppress(ValueError):
                    retry = float(resp.json().get("parameters", {}).get("retry_after", 5))
                return False, retry
            return resp.status_code < 400, 0.0
        except httpx.HTTPError as exc:
            log.warning("telegram transport error: %s", exc)
            return False, 0.0


    # -- forum topics (supergroups with Topics enabled) ---------------------
    def chat_info(self, chat_id: str = "") -> dict:
        if not self.enabled:
            return {}
        try:
            resp = httpx.get(
                f"{API}/bot{self.token}/getChat",
                params={"chat_id": chat_id or self.chat_id},
                timeout=10.0,
            )
            data = resp.json()
            return data.get("result") or {} if data.get("ok") else {}
        except (httpx.HTTPError, ValueError):
            return {}

    def create_topic(self, name: str, chat_id: str = "") -> int:
        if not self.enabled:
            return 0
        try:
            resp = httpx.post(
                f"{API}/bot{self.token}/createForumTopic",
                data={"chat_id": chat_id or self.chat_id, "name": name[:120]},
                timeout=15.0,
            )
            data = resp.json()
            if data.get("ok"):
                return int(data["result"]["message_thread_id"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
        return 0


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
