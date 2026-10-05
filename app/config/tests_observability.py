from __future__ import annotations

from unittest import mock

from config.observability import init_sentry, scrub_event


def test_sentry_is_disabled_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    with mock.patch("sentry_sdk.init") as init:
        assert init_sentry() is False
    init.assert_not_called()


def test_sentry_initialises_without_pii_when_a_dsn_is_set(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://key@example.invalid/1")
    with mock.patch("sentry_sdk.init") as init:
        assert init_sentry() is True
    kwargs = init.call_args.kwargs
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["before_send"] is scrub_event


def test_events_lose_bodies_cookies_credentials_and_user_details():
    event = {
        "request": {
            "data": {"password": "hunter2", "code": "123456"},
            "cookies": {"sessionid": "x"},
            "query_string": "token=abc",
            "headers": {"Authorization": "Bearer t", "Cookie": "a=b", "Accept": "application/json"},
        },
        "user": {"id": 7, "email": "a@b.test", "ip_address": "1.2.3.4"},
    }
    clean = scrub_event(event)
    assert clean is not None
    assert "data" not in clean["request"] and "cookies" not in clean["request"]
    assert "query_string" not in clean["request"]
    assert clean["request"]["headers"] == {"Accept": "application/json"}
    assert clean["user"] == {"id": 7}
