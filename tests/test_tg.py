import queue

from panel.tg import Telegram, escape


def test_escape():
    assert escape("a<b>&c") == "a&lt;b&gt;&amp;c"


def test_disabled_is_noop():
    tg = Telegram("", "")
    assert not tg.enabled
    tg.send("hi")
    tg.send_document(b"x", "a.bin")
    assert tg._queue.qsize() == 0


def test_enqueue_and_bounded_drop():
    tg = Telegram("t", "c")
    for i in range(500):
        tg.send(f"m{i}")
    assert tg._queue.qsize() <= 100


def test_worker_delivers(monkeypatch):
    sent = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None, files=None):
            sent.append((url, data))
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", lambda **kw: FakeClient())
    tg = Telegram("tok", "chat")
    import threading

    worker = threading.Thread(target=tg._run, daemon=True)
    worker.start()
    tg.send("hello")
    for _ in range(50):
        if sent:
            break
        import time

        time.sleep(0.05)
    tg.enabled = False
    assert sent
    url, data = sent[0]
    assert "/bottok/sendMessage" in url
    assert data["parse_mode"] == "HTML"


def test_send_document_enqueues():
    tg = Telegram("t", "c")
    tg.send_document(b"data", "file.bin", "cap")
    assert tg._queue.get_nowait().kind == "document"


def test_queue_type():
    tg = Telegram("t", "c")
    assert isinstance(tg._queue, queue.Queue)
