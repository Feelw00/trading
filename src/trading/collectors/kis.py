"""KIS(한국투자증권) Open API 클라이언트 — 투자자별 매매동향(수급) 조회.

스펙 출처(추측 아님 — CLAUDE.md rule #1):
- 공식 저장소 ``koreainvestment/open-trading-api`` (examples_llm/kis_auth.py,
  domestic_stock/investor_trade_by_stock_daily, inquire_investor_daily_by_market).
- 응답 필드·파라미터 조합은 2026-06-11 실호출로 관측 확정(OPEN_QUESTIONS COLLECT-2 갱신).

엔드포인트(관측 검증):
- 토큰: POST ``/oauth2/tokenP`` {grant_type: client_credentials, appkey, appsecret}
  → access_token(만료 ``access_token_token_expired``, 24h). 6시간 내 재발급은 동일 토큰
  반환 + 알림톡 발송 → **파일 캐시 필수**(``.runtime/kis/token.json``).
- 종목별 투자자매매동향(일별): GET
  ``/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily``
  TR ``FHPTJ04160001`` — output2가 일별 행(최근 ~30거래일), 순매수 필드
  ``{frgn,prsn,orgn}_ntby_qty``(주) / ``..._ntby_tr_pbmn``(백만원, 관측).
- 시장별 투자자매매동향(일별): GET
  ``/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market``
  TR ``FHPTJ04040000`` — KOSPI=(업종 0001, KSP) / KOSDAQ=(업종 1001, KSQ) 관측 확정.

실시간 시세성 TR (P-6 arm-check용 — **2026-06-12 장중 실호출 관측 확정**, KIS-RT-1):
- 주식현재가 체결: GET ``/uapi/domestic-stock/v1/quotations/inquire-ccnl``
  TR ``FHKST01010300`` — output(체결 리스트), [0]행에 ``stck_prpr``(현재가)·
  ``tday_rltv``(당일 체결강도, 100 기준) 관측. (현재가 시세 ``inquire-price``엔 체결강도 없음 — 미사용.)
- 주식현재가 호가/예상체결: GET
  ``/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn``
  TR ``FHKST01010200`` — output1에 ``total_bidp_rsqn``·``total_askp_rsqn``(매수/매도 총호가잔량) 관측.
  필드 해석은 호출측(flowsnap)에서, 결측·비수치는 None=관측치 없음으로 처리(추측 금지).
- 주식현재가 시세: GET ``/uapi/domestic-stock/v1/quotations/inquire-price``
  TR ``FHKST01010100`` — output에 ``bstp_kor_isnm``(KRX 공식 업종명) 관측(2026-07-13,
  005930→'전기·전자'). 섹터 태깅(``kis-bstp-v1``, 운영자 결정)에 사용.

조회 전용(시세성) TR만 사용 — 주문 계열 TR은 이 모듈에 두지 않는다(CLAUDE.md rule #3).
"""

import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trading.collectors.base import KST, CollectError, now_kst

BASE_URLS = {
    "REAL": "https://openapi.koreainvestment.com:9443",
    "VTS": "https://openapivts.koreainvestment.com:29443",
}
TOKEN_CACHE = Path(".runtime") / "kis" / "token.json"
TR_INVESTOR_BY_STOCK = "FHPTJ04160001"
TR_INVESTOR_BY_MARKET = "FHPTJ04040000"
TR_INVESTOR_INTRADAY = "FHPTJ04030000"  # 시장별 투자자매매동향(시세성·당일 누계) — HTS [0403]
TR_QUOTE_CCNL = "FHKST01010300"         # 주식현재가 체결(현재가·체결강도, 관측 확정 KIS-RT-1)
TR_ASKING_PRICE = "FHKST01010200"       # 주식현재가 호가/예상체결(총호가잔량, 관측 확정)
TR_QUOTE_PRICE = "FHKST01010100"        # 주식현재가 시세(KRX 업종명 bstp_kor_isnm, 관측 확정 2026-07-13)
# 시장별 TR 파라미터 (업종코드, 시장구분) — 실호출 관측으로 확정
MARKET_PARAMS = {"KOSPI": ("0001", "KSP"), "KOSDAQ": ("1001", "KSQ")}
# 시세성 TR 파라미터 (시장구분, 업종구분) — 2026-06-11 장중 조합 프로브로 관측 확정:
# S001/S101만 비영(非0) 응답, 거래대금 규모비(~12:1)로 KOSPI/KOSDAQ 식별. 그 외 조합은 0 또는 오류.
INTRADAY_MARKET_PARAMS = {"KOSPI": ("999", "S001"), "KOSDAQ": ("999", "S101")}

# (url, data|None, headers, timeout) -> bytes. 테스트 주입용.
HttpCall = Callable[[str, bytes | None, dict[str, str], float], bytes]


def _http(url: str, data: bytes | None, headers: dict[str, str], timeout: float) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw: bytes = resp.read()
    return raw


class KisClient:
    """KIS REST 조회 클라이언트. 토큰은 파일 캐시(만료 5분 마진)로 재사용."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        *,
        account_type: str = "REAL",
        token_cache: Path = TOKEN_CACHE,
        http: HttpCall = _http,
        sleeper: Callable[[float], None] = time.sleep,
        call_interval_s: float = 0.12,  # 실전 한도 20건/초 — 보수 페이싱
        timeout: float = 10.0,
    ) -> None:
        base = BASE_URLS.get(account_type.upper())
        if base is None:
            raise ValueError(f"KIS_ACCOUNT_TYPE은 REAL|VTS — got {account_type!r}")
        self._base = base
        self._key = app_key
        self._secret = app_secret
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
            expired = datetime.strptime(
                str(data["access_token_token_expired"]), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=KST)
        except (OSError, ValueError, KeyError):
            return None
        if now_kst() >= expired - timedelta(minutes=5):
            return None
        return str(data["access_token"])

    def token(self) -> str:
        if self._token:
            return self._token
        cached = self._cached_token()
        if cached:
            self._token = cached
            return cached
        body = json.dumps(
            {"grant_type": "client_credentials", "appkey": self._key, "appsecret": self._secret}
        ).encode()
        raw = self._http(
            f"{self._base}/oauth2/tokenP",
            body,
            {"Content-Type": "application/json"},
            self._timeout,
        )
        data = json.loads(raw)
        if "access_token" not in data:
            raise CollectError(f"KIS 토큰 발급 실패: {data}")
        self._cache.parent.mkdir(parents=True, exist_ok=True)
        self._cache.write_text(json.dumps(data))
        self._token = str(data["access_token"])
        return self._token

    # --- 조회 공통 ---

    def _get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self._base}{path}?{urllib.parse.urlencode(params)}"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.token()}",
            "appkey": self._key,
            "appsecret": self._secret,
            "tr_id": tr_id,
            "custtype": "P",
            "tr_cont": "",
        }
        last: Exception | None = None
        raw: bytes | None = None
        for attempt in range(3):
            self._sleep(self._interval if attempt == 0 else 0.5 * (2**attempt))
            try:
                raw = self._http(url, None, headers, self._timeout)
                break
            except OSError as exc:  # 일시 오류(HTTP 5xx·타임아웃) — 백오프 재시도
                last = exc
        if raw is None:
            raise CollectError(f"KIS 호출 실패: {path}") from last
        data: dict[str, Any] = json.loads(raw)
        if str(data.get("rt_cd")) != "0":
            raise CollectError(f"KIS 오류(rt_cd={data.get('rt_cd')}): {data.get('msg1')}")
        return data

    # --- 투자자별 매매동향 ---

    def investor_flows_by_stock(self, srtn_cd: str, bas_dt: str) -> list[dict[str, Any]]:
        """종목별 일별 수급 행(최근 ~30거래일, bas_dt 기준 과거 방향). 원시 dict 그대로."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
            TR_INVESTOR_BY_STOCK,
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": srtn_cd,
                "FID_INPUT_DATE_1": bas_dt,
                "FID_ORG_ADJ_PRC": "",
                "FID_ETC_CLS_CODE": "",
            },
        )
        rows = data.get("output2")
        return list(rows) if isinstance(rows, list) else []

    def investor_flows_by_market(self, market: str, bas_dt: str) -> list[dict[str, Any]]:
        """시장(KOSPI|KOSDAQ) 일별 수급 행. 원시 dict 그대로."""
        pair = MARKET_PARAMS.get(market.upper())
        if pair is None:
            raise ValueError(f"market은 KOSPI|KOSDAQ — got {market!r}")
        iscd, mkt_cls = pair
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
            TR_INVESTOR_BY_MARKET,
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": iscd,
                "FID_INPUT_DATE_1": bas_dt,
                "FID_INPUT_ISCD_1": mkt_cls,
                "FID_INPUT_DATE_2": bas_dt,
                "FID_INPUT_ISCD_2": iscd,
            },
        )
        rows = data.get("output")
        return list(rows) if isinstance(rows, list) else []


    def investor_flows_intraday(self, market: str) -> dict[str, Any]:
        """시장(KOSPI|KOSDAQ) 당일 장중 누계 수급 1행(잠정 — 확정치 아님). 원시 dict.

        응답에 날짜·시각 필드가 없음(관측) — 호출측이 조회 시각을 기준으로 라벨링한다.
        """
        pair = INTRADAY_MARKET_PARAMS.get(market.upper())
        if pair is None:
            raise ValueError(f"market은 KOSPI|KOSDAQ — got {market!r}")
        iscd, iscd2 = pair
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market",
            TR_INVESTOR_INTRADAY,
            {"FID_INPUT_ISCD": iscd, "FID_INPUT_ISCD_2": iscd2},
        )
        rows = data.get("output")
        return dict(rows[0]) if isinstance(rows, list) and rows else {}

    # --- 실시간 시세성 (P-6, 관측 확정 — KIS-RT-1) ---

    def quote_ccnl(self, srtn_cd: str) -> dict[str, Any]:
        """주식현재가 체결 output[0](최신 체결행, 원시). 현재가·체결강도. 해석은 호출측."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-ccnl",
            TR_QUOTE_CCNL,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": srtn_cd},
        )
        out = data.get("output")
        return dict(out[0]) if isinstance(out, list) and out else {}

    def quote_asking_price(self, srtn_cd: str) -> dict[str, Any]:
        """주식현재가 호가 output1(단일 dict, 원시). 필드 해석은 호출측(flowsnap)."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            TR_ASKING_PRICE,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": srtn_cd},
        )
        out = data.get("output1")
        return dict(out) if isinstance(out, dict) else {}

    def quote_price(self, srtn_cd: str) -> dict[str, Any]:
        """주식현재가 시세 output(단일 dict, 원시). KRX 공식 업종명 ``bstp_kor_isnm`` 포함
        (2026-07-13 실호출 관측: 005930 → '전기·전자'). 필드 해석은 호출측."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            TR_QUOTE_PRICE,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": srtn_cd},
        )
        out = data.get("output")
        return dict(out) if isinstance(out, dict) else {}


def client_from_env() -> KisClient | None:
    """환경변수로 클라이언트 생성. 키 미설정이면 None(호출측 blocked 처리)."""
    key = os.environ.get("KIS_APP_KEY", "")
    secret = os.environ.get("KIS_APP_SECRET", "")
    if not key or not secret:
        return None
    return KisClient(key, secret, account_type=os.environ.get("KIS_ACCOUNT_TYPE", "REAL"))


__all__ = [
    "BASE_URLS",
    "INTRADAY_MARKET_PARAMS",
    "MARKET_PARAMS",
    "TR_ASKING_PRICE",
    "TR_INVESTOR_BY_MARKET",
    "TR_INVESTOR_BY_STOCK",
    "TR_INVESTOR_INTRADAY",
    "TR_QUOTE_CCNL",
    "TR_QUOTE_PRICE",
    "HttpCall",
    "KisClient",
    "client_from_env",
]
