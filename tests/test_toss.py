"""토스증권 Open API 클라이언트 — 인증 캐시·envelope·지정가 전용(시장가 부재) 가드."""

import json
from pathlib import Path
from typing import Any

import pytest

import trading.collectors.toss as toss_mod
from trading.collectors.base import CollectError
from trading.collectors.toss import TossClient


class _Http:
    """호출 기록 + 준비된 응답 반환."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.calls: list[tuple[str, bytes | None, dict[str, str]]] = []
        self.methods: list[str] = []
        self._responses = responses

    def __call__(self, method: str, url: str, data: bytes | None, headers: dict[str, str], timeout: float) -> bytes:
        self.calls.append((url, data, headers))
        self.methods.append(method)
        return json.dumps(self._responses[len(self.calls) - 1]).encode()


def _client(tmp_path: Path, responses: list[dict[str, Any]], *, account: str | None = "acc-1") -> tuple[TossClient, _Http]:
    http = _Http(responses)
    c = TossClient(
        "cid", "sec", account_seq=account, token_cache=tmp_path / "token.json",
        http=http, sleeper=lambda s: None,
    )
    return c, http


def test_token_fetch_and_file_cache(tmp_path: Path) -> None:
    c, http = _client(tmp_path, [{"access_token": "tok1", "token_type": "Bearer", "expires_in": 3600}])
    assert c.token() == "tok1"
    # form-urlencoded 본문 + client_credentials
    url, data, headers = http.calls[0]
    assert url.endswith("/oauth2/token")
    assert b"grant_type=client_credentials" in (data or b"")
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    # 두 번째 클라이언트는 파일 캐시 재사용(HTTP 0회)
    c2, http2 = _client(tmp_path, [])
    assert c2.token() == "tok1"
    assert http2.calls == []


def test_token_failure_raises(tmp_path: Path) -> None:
    c, _ = _client(tmp_path, [{"error": "invalid_client"}])
    with pytest.raises(CollectError):
        c.token()


def test_place_limit_order_body_is_limit_only(tmp_path: Path) -> None:
    c, http = _client(tmp_path, [
        {"access_token": "t", "expires_in": 3600},
        {"result": {"orderId": "ord-1", "clientOrderId": "exec-x"}},
    ])
    res = c.place_limit_order("005930", "BUY", 3, 70000, client_order_id="exec-x")
    assert res["orderId"] == "ord-1"
    url, data, headers = http.calls[1]
    body = json.loads(data or b"{}")
    assert body["orderType"] == "LIMIT"           # 하드코딩 — 절대금지 #3
    assert body["quantity"] == "3" and body["price"] == "70000"
    assert headers["X-Tossinvest-Account"] == "acc-1"
    assert "confirmHighValueOrder" not in body    # 고액 확인 플래그 자체를 안 보냄


def test_place_limit_order_validates_inputs(tmp_path: Path) -> None:
    c, _ = _client(tmp_path, [{"access_token": "t", "expires_in": 3600}])
    with pytest.raises(ValueError):
        c.place_limit_order("005930", "MARKET", 1, 100, client_order_id="x")  # side 오용
    with pytest.raises(ValueError):
        c.place_limit_order("005930", "BUY", 0, 100, client_order_id="x")


def test_stop_conditional_is_single_sell_limit(tmp_path: Path) -> None:
    c, http = _client(tmp_path, [
        {"access_token": "t", "expires_in": 3600},
        {"result": {"conditionalOrderId": "cond-1"}},
    ])
    res = c.place_stop_sell_conditional(
        "005930", 3, trigger_price=65000, order_price=64900,
        expire_date="2026-07-22", client_order_id="stop-x",
    )
    assert res["conditionalOrderId"] == "cond-1"
    body = json.loads(http.calls[1][1] or b"{}")
    assert body["type"] == "SINGLE"
    assert body["orderType"] == "LIMIT"
    assert body["first"] == {"orderSide": "SELL", "triggerPrice": "65000", "orderPrice": "64900"}
    assert body["expireDate"] == "2026-07-22"


def test_oco_sell_body_stop_and_target(tmp_path: Path) -> None:
    c, http = _client(tmp_path, [
        {"access_token": "t", "expires_in": 3600},
        {"result": {"conditionalOrderId": "oco-1"}},
    ])
    res = c.place_oco_sell(
        "005930", 24, stop_trigger=65_000, stop_price=64_800,
        target_trigger=77_900, target_price=77_900,
        expire_date="2026-07-22", client_order_id="oco-x",
    )
    assert res["conditionalOrderId"] == "oco-1"
    body = json.loads(http.calls[1][1] or b"{}")
    assert body["type"] == "OCO" and body["orderType"] == "LIMIT"
    assert body["first"]["orderSide"] == body["second"]["orderSide"] == "SELL"
    assert body["first"]["triggerPrice"] == "65000" and body["second"]["triggerPrice"] == "77900"
    with pytest.raises(ValueError):  # 익절 트리거가 손절 이하 — 논리 오류 차단
        c.place_oco_sell("005930", 1, stop_trigger=65_000, stop_price=64_800,
                         target_trigger=64_000, target_price=64_000,
                         expire_date="2026-07-22", client_order_id="x")


def test_rankings_returns_items(tmp_path: Path) -> None:
    c, http = _client(tmp_path, [
        {"access_token": "t", "expires_in": 3600},
        {"result": {"rankedAt": "2026-07-13T10:00:00+09:00",
                    "rankings": [{"rank": 1, "symbol": "005930"}]}},
    ])
    rows = c.rankings_trading_amount()
    assert rows[0]["symbol"] == "005930"
    url = http.calls[1][0]
    assert "type=MARKET_TRADING_AMOUNT" in url and "duration=realtime" in url


def test_account_required_for_order_apis(tmp_path: Path) -> None:
    c, _ = _client(tmp_path, [{"access_token": "t", "expires_in": 3600}], account=None)
    with pytest.raises(CollectError):
        c.holdings()


def test_prices_batch_limit_and_envelope(tmp_path: Path) -> None:
    c, http = _client(tmp_path, [
        {"access_token": "t", "expires_in": 3600},
        {"result": [{"symbol": "005930", "lastPrice": "70100"}]},
    ])
    rows = c.prices(["005930"])
    assert rows[0]["lastPrice"] == "70100"
    with pytest.raises(ValueError):
        c.prices([f"{i:06d}" for i in range(201)])


def test_no_market_order_path_in_module_source() -> None:
    # 절대금지 #3 회귀 가드 — 모듈 어디에도 시장가("MARKET") 요청 경로가 없다.
    src = Path(toss_mod.__file__).read_text()
    assert '"MARKET"' not in src
    assert "'MARKET'" not in src


def test_cancel_conditional_uses_delete(tmp_path: Path) -> None:
    c, http = _client(tmp_path, [
        {"access_token": "t", "expires_in": 3600},
        {"result": {}},
    ])
    c.cancel_conditional("cond-1")
    assert http.methods[1] == "DELETE"
    assert http.calls[1][0].endswith("/api/v1/conditional-orders/cond-1")
