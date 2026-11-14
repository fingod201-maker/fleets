import time

from panel.rd import mint_token, verify_token


def test_token_roundtrip():
    token = mint_token("secret", "session-1", ttl=60)
    assert verify_token("secret", "session-1", token)


def test_token_rejects_wrong_session():
    token = mint_token("secret", "session-1", ttl=60)
    assert not verify_token("secret", "session-2", token)


def test_token_rejects_wrong_secret():
    token = mint_token("secret", "session-1", ttl=60)
    assert not verify_token("other", "session-1", token)


def test_token_expiry():
    token = mint_token("secret", "session-1", ttl=-1)
    assert not verify_token("secret", "session-1", token)


def test_token_malformed():
    assert not verify_token("secret", "s", "")
    assert not verify_token("secret", "s", "notatoken")
    assert not verify_token("secret", "s", "abc.def")
    assert verify_token("secret", "s", f"{int(time.time()) + 100}.deadbeef") is False
