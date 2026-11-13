import time

from panel.auth import Auth
from panel.db import Database


def make_auth(tmp_settings) -> Auth:
    db = Database(tmp_settings.db_path)
    db.migrate()
    return Auth(tmp_settings, db)


def test_password_roundtrip(tmp_settings, hashed_password):
    object.__setattr__(tmp_settings, "panel_pass_hash", hashed_password)
    auth = make_auth(tmp_settings)
    assert auth.verify_password("correct horse battery staple 00")
    assert not auth.verify_password("wrong")


def test_session_lifecycle(tmp_settings):
    auth = make_auth(tmp_settings)
    session_id, csrf = auth.create_session("operator", "127.0.0.1", "pytest")
    row = auth.get_session(session_id)
    assert row is not None
    assert row["csrf"] == csrf
    signed = auth.sign(session_id)
    assert auth.unsign(signed) == session_id
    auth.invalidate(session_id)
    assert auth.get_session(session_id) is None


def test_csrf_compare(tmp_settings):
    auth = make_auth(tmp_settings)
    assert auth.verify_csrf("abc", "abc")
    assert not auth.verify_csrf("abc", "abd")
    assert not auth.verify_csrf("", "")


def test_lockout_after_five_failures(tmp_settings):
    auth = make_auth(tmp_settings)
    key = "user:operator"
    for _ in range(4):
        auth.register_failure(key)
    assert auth.lockout_remaining(key) == 0.0
    auth.register_failure(key)
    remaining = auth.lockout_remaining(key)
    assert 0 < remaining <= tmp_settings.lockout_seconds
    auth.clear_failures(key)
    assert auth.lockout_remaining(key) == 0.0


def test_totp_disabled_by_default(tmp_settings):
    auth = make_auth(tmp_settings)
    assert auth.verify_totp("")


def test_totp_enforced_when_set(tmp_settings):
    import pyotp

    secret = pyotp.random_base32()
    object.__setattr__(tmp_settings, "panel_totp_secret", secret)
    auth = make_auth(tmp_settings)
    assert auth.verify_totp(pyotp.TOTP(secret).now())
    assert not auth.verify_totp("000000")


def test_session_expiry(tmp_settings):
    object.__setattr__(tmp_settings, "session_ttl", 1)
    auth = make_auth(tmp_settings)
    session_id, _ = auth.create_session("operator", "127.0.0.1", "pytest")
    with auth.db.connect() as conn:
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE id = ?", (time.time() - 10, session_id)
        )
    assert auth.get_session(session_id) is None
