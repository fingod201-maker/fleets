import dataclasses

import pytest
from panel.sliver import BridgeError, SliverBridge


def bridge_at(url: str) -> SliverBridge:
    settings = dataclasses.replace(_base_settings(), bridge_url=url)
    return SliverBridge(settings)


def _base_settings():
    from panel.config import Settings

    return Settings(
        env_file=None,
        db_path="x.db",
        base_dir=".",
        artifacts_dir=".",
        panel_user="operator",
        panel_pass_hash="",
        panel_secret="s",
        panel_totp_secret="",
        session_cookie="c",
        session_ttl=60,
        cookie_secure=False,
        lockout_max=5,
        lockout_seconds=900,
        bridge_url="http://127.0.0.1:8001",
        rd_port=8443,
        rd_bind="127.0.0.1",
        public_host="localhost",
        tls_cert="",
        tg_bot_token="",
        tg_chat_id="",
        cf_api_token="",
        cf_zone_name="",
        operator_email="",
        document_name="doc",
        lure_hosts=["get"],
        lure_meet_hosts=["meet"],
        panel_bin_dir="/opt/panel/bin",
        rd_endpoint="127.0.0.1:8443",
    )


def test_unreachable_raises_bridge_error():
    br = bridge_at("http://127.0.0.1:59999")
    with pytest.raises(BridgeError):
        br.sessions()
    assert not br.available or br._backoff.attempts >= 1


def test_backoff_grows_and_caps():
    br = bridge_at("http://127.0.0.1:59999")
    delays = [br._backoff.failure() for _ in range(10)]
    assert delays[0] < delays[1]
    assert max(delays) <= br._backoff.max_delay


def test_backoff_resets_on_success():
    br = bridge_at("http://127.0.0.1:59999")
    br._backoff.failure()
    br._backoff.failure()
    br._backoff.success()
    assert br._backoff.attempts == 0
