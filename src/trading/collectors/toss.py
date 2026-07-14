"""토스증권 Open API 클라이언트 — 계좌·시세·주문(지정가 전용)·조건부 청산.

스펙 출처(추측 아님 — CLAUDE.md rule #1): **공식 OpenAPI JSON**
``https://openapi.tossinvest.com/openapi-docs/latest/openapi.json``
(2026-07-13 전수 검증 — OPEN_QUESTIONS 외부 의존 조사 노트·EXEC-1).

- 인증: OAuth 2.0 Client Credentials — ``POST /oauth2/token`` (form-urlencoded,
  grant_type/client_id/client_secret) → ``{access_token, token_type, expires_in}``.
  허용 IP 사전 등록 필수(미등록 403). 토큰은 파일 캐시(만료 5분 마진).
- 응답 envelope: 성공 페이로드는 ``{"result": ...}`` (토큰 엔드포인트 제외).
- 계좌 계열(잔고·주문·조건주문)은 ``X-Tossinvest-Account: <accountSeq>`` 헤더 필수.
- rate limit(스펙): 계좌 초당 1회 · 주문 초당 6회(09:00~09:10 3회) — 보수 페이싱 +
  429는 지수 백오프 재시도.

**절대금지 #3 집행:** 이 모듈에 시장가 경로는 존재하지 않는다 — 주문 생성은
``place_limit_order``/``place_stop_sell_conditional`` 뿐이고 두 함수 모두 요청 본문의
``orderType`` 을 ``"LIMIT"`` 으로 하드코딩한다(호출자가 바꿀 파라미터 자체가 없음).
``confirmHighValueOrder`` 도 두지 않는다 — 집행 하드캡(EXEC-1, 종목당 100만원)이
1억 원 문턱을 구조적으로 넘지 못하게 하는 이중벽이다.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from trading.collectors.base import CollectError, now_kst

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_CACHE = Path(".runtime") / "toss" / "token.json"

# (method, url, data|None, headers, timeout) -> bytes. 테스트 주입용.
HttpCall = Callable[[str, str, bytes | None, dict[str, str], float], bytes]

_SIDES = frozenset({"BUY", "SELL"})


def _http(method: str, url: str, data: bytes | None, headers: dict[str, str], timeout: float) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw: bytes = resp.read()
    return raw


class TossClient:
    """토스증권 Open API REST 클라이언트. 조회 + 지정가 주문 + 조건부 청산만."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        account_seq: str | None = None,
        base_url: str = BASE_URL,
        token_cache: Path = TOKEN_CACHE,
        http: HttpCall = _http,
        sleeper: Callable[[float], None] = time.sleep,
        call_interval_s: float = 1.1,  # 계좌 계열 초당 1회 스펙 — 보수 페이싱
        timeout: float = 10.0,
    ) -> None:
        self._id = client_id
        self._secret = client_secret
        self._account = account_seq
        self._base = base_url
        self._cache = token_cache
        self._http = http
        self._sleep = sleeper
        self._interval = call_interval_s
        self._timeout = timeout
        self._token: str | None = None

    # --- 인증 ---

    def _cached_token(self) -> str | None:
        try:
            data = json.loads(self._cache.read_text())
            expires_at = float(data["expires_at"])
        except (OSError, ValueError, KeyError):
            return None
        if now_kst().timestamp() >= expires_at - 300:  # 만료 5분 마진
            return None
        return str(data["access_token"])

    def token(self) -> str:
        if self._token:
            return self._token
        cached = self._cached_token()
        if cached:
            self._token = cached
            return cached
        body = urllib.parse.urlencode(
            {"grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret}
        ).encode()
        raw = self._http(
            "POST",
            f"{self._base}/oauth2/token",
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
            self._timeout,
        )
        data = json.loads(raw)
        if "access_token" not in data:
            raise CollectError(f"토스 토큰 발급 실패: {data}")
        expires_at = now_kst().timestamp() + float(data.get("expires_in") or 0)
        self._cache.parent.mkdir(parents=True, exist_ok=True)
        self._cache.write_text(json.dumps({"access_token": data["access_token"], "expires_at": expires_at}))
        self._token = str(data["access_token"])
        return self._token

    # --- 공통 호출 (envelope 해제 + 429 백오프) ---

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        need_account: bool = False,
    ) -> Any:
        url = f"{self._base}{path}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"}
        if need_account:
            if not self._account:
                raise CollectError("토스 계좌(accountSeq) 미설정 — TOSS_ACCOUNT_SEQ 필요")
            headers["X-Tossinvest-Account"] = self._account
        data = json.dumps(body).encode() if body is not None else None
        if data is not None and method in ("GET", "DELETE"):
            raise ValueError(f"{method}에 body 불가")
        last: Exception | None = None
        raw: bytes | None = None
        for attempt in range(3):
            self._sleep(self._interval if attempt == 0 else 1.0 * (2**attempt))
            try:
                req_headers = dict(headers)
                raw = self._http(method, url, data, req_headers, self._timeout)
                break
            except OSError as exc:  # 5xx·429·타임아웃 — 백오프 재시도
                last = exc
        if raw is None:
            raise CollectError(f"토스 호출 실패: {method} {path}") from last
        parsed: Any = json.loads(raw)
        if isinstance(parsed, dict) and "result" in parsed:
            return parsed["result"]
        return parsed

    # --- 계좌 / 자산 (조회) ---

    def accounts(self) -> list[dict[str, Any]]:
        """계좌 목록(원시). accountSeq 필드가 주문 헤더에 쓰인다."""
        out = self._call("GET", "/api/v1/accounts")
        return list(out) if isinstance(out, list) else []

    def holdings(self) -> dict[str, Any]:
        """보유 종목 개요(원시 HoldingsOverview — items가 종목 리스트)."""
        out = self._call("GET", "/api/v1/holdings", need_account=True)
        return dict(out) if isinstance(out, dict) else {}

    def buying_power_krw(self) -> int | None:
        """현금 기반 매수 가능 금액(원, 미수 미발생 기준). 결측·비수치는 None(추측 금지)."""
        out = self._call("GET", "/api/v1/buying-power", params={"currency": "KRW"}, need_account=True)
        if not isinstance(out, dict):
            return None
        try:
            return int(float(str(out.get("cashBuyingPower"))))
        except (TypeError, ValueError):
            return None

    # --- 시세 (조회 — 토큰만) ---

    def prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        """현재가 배치(최대 200종목/1콜, 원시 PriceResponse 리스트)."""
        if not symbols:
            return []
        if len(symbols) > 200:
            raise ValueError("prices는 1콜 200종목까지")
        out = self._call("GET", "/api/v1/prices", params={"symbols": ",".join(symbols)})
        return list(out) if isinstance(out, list) else []

    def market_indicator_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        """시장 지표 현재가(KOSPI/KOSDAQ 등, 원시). 토큰만 필요 — 레짐 감시(EXEC-7)용."""
        if not symbols:
            return []
        out = self._call(
            "GET", "/api/v1/market-indicators/prices", params={"symbols": ",".join(symbols)}
        )
        return list(out) if isinstance(out, list) else []

    def market_indicator_candles(
        self, symbol: str, *, interval: str = "1d", count: int = 2
    ) -> list[dict[str, Any]]:
        """시장 지표 캔들(원시) — 전일 종가 산출용(interval 1m|1d, 스펙)."""
        out = self._call(
            "GET",
            f"/api/v1/market-indicators/{symbol}/candles",
            params={"interval": interval, "count": str(count)},
        )
        if isinstance(out, dict):
            rows = out.get("candles") or out.get("items")
            return list(rows) if isinstance(rows, list) else []
        return list(out) if isinstance(out, list) else []

    def rankings_trading_amount(
        self, *, market: str = "KR", duration: str = "realtime", count: int = 100
    ) -> list[dict[str, Any]]:
        """시장 거래대금 상위 랭킹(원시 RankingItem 리스트 — symbol 포함). 토큰만 필요.

        P-11 Stage B 섹터 점화 판정용. duration=realtime은 거래대금/거래량 타입만 지원(스펙)."""
        out = self._call(
            "GET",
            "/api/v1/rankings",
            params={
                "type": "MARKET_TRADING_AMOUNT",
                "marketCountry": market,
                "duration": duration,
                "count": str(count),
            },
        )
        if isinstance(out, dict):
            rows = out.get("rankings")
            return list(rows) if isinstance(rows, list) else []
        return []

    # --- 주문 (지정가 전용 — 시장가 경로 없음) ---

    def place_limit_order(
        self, symbol: str, side: str, quantity: int, price: int, *, client_order_id: str
    ) -> dict[str, Any]:
        """지정가 주문 생성. orderType=LIMIT 하드코딩(절대금지 #3). 멱등키 필수.

        KR 가격은 정수(원)·호가단위 준수 — 어긋나면 400에 올바른 단위가 옴(호출측 처리).
        """
        if side not in _SIDES:
            raise ValueError(f"side는 BUY|SELL — got {side!r}")
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity/price는 양수")
        body = {
            "symbol": symbol,
            "side": side,
            "orderType": "LIMIT",  # 시장가 금지 — 파라미터화하지 않는다
            "quantity": str(quantity),
            "price": str(price),
            "clientOrderId": client_order_id,
        }
        out = self._call("POST", "/api/v1/orders", body=body, need_account=True)
        return dict(out) if isinstance(out, dict) else {}

    def order(self, order_id: str) -> dict[str, Any]:
        """주문 상세(원시 Order — status/execution.filledQuantity 포함)."""
        out = self._call("GET", f"/api/v1/orders/{order_id}", need_account=True)
        return dict(out) if isinstance(out, dict) else {}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        out = self._call("POST", f"/api/v1/orders/{order_id}/cancel", body={}, need_account=True)
        return dict(out) if isinstance(out, dict) else {}

    # --- 조건부 청산 (SINGLE 매도 — 손절 전용) ---

    def place_stop_sell_conditional(
        self,
        symbol: str,
        quantity: int,
        *,
        trigger_price: int,
        order_price: int,
        expire_date: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        """손절 조건주문(SINGLE·SELL) — 감시가 도달 시 지정가 매도. orderType=LIMIT 하드코딩.

        ``expire_date`` = YYYY-MM-DD (초안 TTL 만료일과 정렬).
        """
        if quantity <= 0 or trigger_price <= 0 or order_price <= 0:
            raise ValueError("quantity/trigger_price/order_price는 양수")
        body = {
            "symbol": symbol,
            "type": "SINGLE",
            "quantity": str(quantity),
            "orderType": "LIMIT",  # 시장가 금지
            "expireDate": expire_date,
            "clientOrderId": client_order_id,
            "first": {
                "orderSide": "SELL",
                "triggerPrice": str(trigger_price),
                "orderPrice": str(order_price),
            },
        }
        out = self._call("POST", "/api/v1/conditional-orders", body=body, need_account=True)
        return dict(out) if isinstance(out, dict) else {}

    def place_oco_sell(
        self,
        symbol: str,
        quantity: int,
        *,
        stop_trigger: int,
        stop_price: int,
        target_trigger: int,
        target_price: int,
        expire_date: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        """손절+익절 OCO(SELL·SELL) — 한쪽 발동 시 다른쪽 자동 취소. orderType=LIMIT 하드코딩.

        **first=익절(상방 감시, 높은 감시가), second=손절(하방 감시)** — 2026-07-14 실호출
        관측 확정: 반대로 보내면 400 invalid-request("첫번째(익절) 감시가가 두번째(손절)
        감시가보다 높아야 합니다"). 종목당 OCO 1개 제한(스펙).
        """
        if quantity <= 0 or min(stop_trigger, stop_price, target_trigger, target_price) <= 0:
            raise ValueError("quantity/가격 인자는 양수")
        if target_trigger <= stop_trigger:
            raise ValueError("익절 트리거는 손절 트리거보다 높아야 한다")
        body = {
            "symbol": symbol,
            "type": "OCO",
            "quantity": str(quantity),
            "orderType": "LIMIT",  # 시장가 금지
            "expireDate": expire_date,
            "clientOrderId": client_order_id,
            "first": {
                "orderSide": "SELL",
                "triggerPrice": str(target_trigger),
                "orderPrice": str(target_price),
            },
            "second": {
                "orderSide": "SELL",
                "triggerPrice": str(stop_trigger),
                "orderPrice": str(stop_price),
            },
        }
        out = self._call("POST", "/api/v1/conditional-orders", body=body, need_account=True)
        return dict(out) if isinstance(out, dict) else {}

    def cancel_conditional(self, conditional_order_id: str) -> None:
        """조건주문 취소(DELETE) — 브래킷 재구성(잔량·본전 상향) 시 사용."""
        self._call(
            "DELETE", f"/api/v1/conditional-orders/{conditional_order_id}", need_account=True
        )

    def conditional_orders(self) -> Any:
        out = self._call("GET", "/api/v1/conditional-orders", need_account=True)
        return out


def client_from_env() -> TossClient | None:
    """환경변수로 클라이언트 생성. 키 미설정이면 None(호출측 blocked/dry-run 처리)."""
    cid = os.environ.get("TOSS_CLIENT_ID", "")
    secret = os.environ.get("TOSS_CLIENT_SECRET", "")
    if not cid or not secret:
        return None
    return TossClient(cid, secret, account_seq=os.environ.get("TOSS_ACCOUNT_SEQ") or None)


__all__ = ["BASE_URL", "HttpCall", "TossClient", "client_from_env"]
