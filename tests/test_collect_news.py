"""뉴스 수집 독립 커맨드 — env→백엔드 구성(네트워크 없이)."""

import pytest

from trading.collect_news import build_sources_from_env
from trading.collectors.news_naver import NaverNewsSource
from trading.collectors.news_searxng import SearxngNewsSource

_VARS = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "SEARXNG_URL")


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


def test_no_keys_yields_no_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    assert build_sources_from_env() == {}


def test_naver_keys_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    sources = build_sources_from_env()
    assert set(sources) == {"naver"} and isinstance(sources["naver"], NaverNewsSource)


def test_searxng_url_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8888")
    sources = build_sources_from_env()
    assert set(sources) == {"searxng"} and isinstance(sources["searxng"], SearxngNewsSource)


def test_partial_naver_key_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")  # SECRET 누락 → 네이버 미구성
    assert build_sources_from_env() == {}


def test_both_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8888")
    assert set(build_sources_from_env()) == {"naver", "searxng"}
