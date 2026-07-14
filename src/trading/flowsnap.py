"""흐름 관측치 스냅샷 생성 (P-6 arm-check 입력, SEL-1 부분 해소).

R5.5 선택기(``selector/engine``)는 ``FlowSnapshot`` ({srtn_cd: {흐름변수: 값}})을 입력으로
받아 순수 코드로 발동 판단한다. 이 모듈은 그 스냅샷을 **결정론으로 조립**한다 — 판단 미개입.

소스(우선순위 = 뒤가 덮어씀):
1. 주입 파일 ``.runtime/flow/<YYYYMMDD>.json`` — 운영자/외부 수동 관측(NXT 프리마켓 등 KIS
   미가용분 보충). SEL-1 잠정 처리 규약.
2. KIS 실시간(장중, **미검증 TR — KIS-RT-1**): 체결강도·전일고가 회복·호가 불균형.

**추측 금지(절대금지 #1):** KIS 응답 필드가 없거나 비수치면 그 변수는 **채우지 않는다**
(= 관측치 없음 = selector가 보수적으로 미충족 처리). 값을 지어내지 않는다.
"""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trading.collectors.base import KST, now_kst
from trading.collectors.kis import KisClient
from trading.collectors.market import MarketStore
from trading.collectors.toss import client_from_env as _toss_from_env

INJECT_DIR = Path(".runtime") / "flow"

# 현재 자동(매일) 관측 가능한 흐름변수 — KIS 실시간으로 채워지는 것만(아래 _kis_observations와 1:1).
# NXT 의존(premkt_volume_ratio·premkt_volume_rank·gap_pct·auction_projection)·미배선
# (volume_climax·new_low_renewal_fail·new_low_after)은 제외 → R5 arm/abort 조건은 이 집합으로만
# 짜야 "영영 미충족"을 피한다. NXT 어댑터가 생기면 여기에 추가(R5 프롬프트가 자동 반영).
# 값은 범위·단위 설명 — R5가 임계값을 범위 밖(예: imbalance>1.15)으로 지어내지 않도록 프롬프트에 주입.
OBSERVABLE_FLOW_DESC: dict[str, str] = {
    "prev_day_high_reclaim": "전일 고가 완전 회복 여부(boolean) — 조건식 ==true/==false만",
    "prev_day_high_recovery": (
        "전일 고가 대비 현재가 비율(연속, 1.0=전일 고가 도달), 통상 0.7~1.3 — "
        "완전 회복은 >=1.0, 일부 회복은 분석 근거가 있는 임계로(예: >=0.97)"
    ),
    "orderbook_imbalance": "호가 (매수-매도)/(매수+매도) 잔량비, 범위 -1.0~+1.0, >0 매수우위(통상 ±0.3)",
    "execution_strength": "당일 체결강도, 100 기준(>100 매수체결 우세), 통상 80~150",
    "sector_ignition": (
        "종목 소속 섹터(KRX 업종)가 실시간 거래대금 상위 100에 5종목 이상 집중된 점화 상태"
        "(boolean) — 조건식 ==true/==false만 (P-11 Stage B, 토스 랭킹 기반)"
    ),
}

# 섹터 점화 판정: 실시간 거래대금 상위 100 중 같은 KRX 업종 소속이 이 수 이상이면 '점화'.
SECTOR_IGNITION_MIN_MEMBERS = 5
OBSERVABLE_FLOW_VARS: frozenset[str] = frozenset(OBSERVABLE_FLOW_DESC)

# KIS 원시 응답 필드(2026-06-12 장중 실호출 관측 확정 — KIS-RT-1). 부재/비수치는 관측치 없음으로.
_F_EXEC_STRENGTH = "tday_rltv"     # 체결 output: 당일 체결강도(100 기준)
_F_CUR_PRICE = "stck_prpr"         # 체결 output: 주식 현재가
_F_BID_QTY = "total_bidp_rsqn"     # 호가 output1: 총 매수호가 잔량
_F_ASK_QTY = "total_askp_rsqn"     # 호가 output1: 총 매도호가 잔량


def _f(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _prev_day_high(
    store: MarketStore, srtn_cd: str, *,
    kis_client: KisClient | None = None, now: datetime | None = None,
) -> float | None:
    """**진짜 전일(직전 거래일) 고가.**

    1차: KIS 일자별 시세(FHKST01010400, 관측 확정 2026-07-14) — 당일 행을 건너뛰고
    직전 거래일 행의 고가. 국내 EOD가 +1영업일 공개라 장중 DB 최신은 T-2다.
    2차(폴백): EOD DB — **최신 적재일이 직전 거래일과 일치할 때만** 신뢰.
    불일치(낡은 기준)면 None(미관측 = 보수 미충족) — 2026-07-14 뉴파워 오발동
    (T-2 고가 9,870을 '전일 고가'로 써 익절 라인 밑에서 발동) 재발 방지."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    today = f"{resolved:%Y%m%d}"
    daily_fn = getattr(kis_client, "daily_prices", None)
    if callable(daily_fn):
        try:
            rows_kis = daily_fn(srtn_cd)
        except Exception:  # noqa: BLE001 — 어댑터 오류는 폴백으로(값을 지어내지 않는다)
            rows_kis = []
        for r in rows_kis:
            d = str(r.get("stck_bsop_date") or "")
            if d and d < today:
                return _f(r.get("stck_hgpr"))
        if rows_kis:
            return None  # KIS 응답은 있는데 전일 행 부재 — 폴백보다 미관측이 보수적
    cutoff = store.nth_recent_date(3) or ""
    rows = store.series_for(srtn_cd, cutoff)
    if not rows:
        return None
    from trading.market_calendar.calendar import MarketCalendar

    prev_td = MarketCalendar.default().latest_trading_day(
        resolved.date() - timedelta(days=1)
    )
    if str(rows[-1][3]) != f"{prev_td:%Y%m%d}":
        return None  # DB 최신이 직전 거래일이 아님(공개 대기) — 낡은 기준으로 판정 금지
    return _f(rows[-1][5])  # series_for 컬럼: (...,bas_dt[3],clpr[4],hipr[5],...)


def _load_injected(now: datetime, inject_dir: Path) -> dict[str, dict[str, float]]:
    path = inject_dir / f"{now.astimezone(KST):%Y%m%d}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, float]] = {}
    if isinstance(raw, dict):
        for srtn, obs in raw.items():
            if isinstance(obs, dict):
                vals = {str(k): f for k, v in obs.items() if (f := _f(v)) is not None}
                if vals:
                    out[str(srtn)] = vals
    return out


def _kis_observations(client: KisClient, srtn_cd: str, prev_high: float | None) -> dict[str, float]:
    """KIS 실시간 1종목 → 흐름변수(가용분만). 호출 실패·결측은 빈 dict로 흡수(추측 금지)."""
    obs: dict[str, float] = {}
    try:
        ccnl = client.quote_ccnl(srtn_cd)
    except Exception:  # noqa: BLE001 — 어댑터 오류는 결측으로 흡수(부분 스냅샷 정상)
        ccnl = {}
    strength = _f(ccnl.get(_F_EXEC_STRENGTH))
    if strength is not None:
        obs["execution_strength"] = strength
    cur = _f(ccnl.get(_F_CUR_PRICE))
    if cur is not None and prev_high is not None and prev_high > 0:
        obs["prev_day_high_reclaim"] = 1.0 if cur > prev_high else 0.0
        # 등급형(운영자 2026-07-14): 계획이 완전/일부 회복을 임계로 명시할 수 있게 연속값 병행
        obs["prev_day_high_recovery"] = round(cur / prev_high, 4)
    try:
        ask = client.quote_asking_price(srtn_cd)
    except Exception:  # noqa: BLE001
        ask = {}
    bid_q, ask_q = _f(ask.get(_F_BID_QTY)), _f(ask.get(_F_ASK_QTY))
    if bid_q is not None and ask_q is not None and (bid_q + ask_q) > 0:
        obs["orderbook_imbalance"] = (bid_q - ask_q) / (bid_q + ask_q)
    return obs


def _hot_sectors(toss: Any, secmap: dict[str, list[str]]) -> set[str] | None:
    """실시간 거래대금 상위 100 → KRX 업종 조인 → 점화 섹터 집합.

    토스 미설정·호출 실패는 None(관측치 없음 — 값을 지어내지 않는다)."""
    if toss is None:
        return None
    try:
        rows = toss.rankings_trading_amount()
    except Exception:  # noqa: BLE001 — 랭킹 실패는 변수 결측으로 흡수
        return None
    if not rows:
        return None
    counts: dict[str, int] = {}
    for r in rows:
        for sec in secmap.get(str(r.get("symbol") or ""), []):
            counts[sec] = counts.get(sec, 0) + 1
    return {s for s, n in counts.items() if n >= SECTOR_IGNITION_MIN_MEMBERS}


def build_snapshot(
    srtns: Sequence[str],
    *,
    kis_client: KisClient | None,
    market_store: MarketStore | None = None,
    now: datetime | None = None,
    inject_dir: Path | None = None,
    toss_client: Any | None = None,
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """흐름 스냅샷 + 결측 notes. KIS 없으면 주입 파일만(전부 없으면 빈 스냅샷=비거래).

    ``toss_client`` 미지정이면 env에서 생성 시도 — 섹터 점화(sector_ignition) 전용.
    """
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    base_dir = inject_dir if inject_dir is not None else INJECT_DIR
    injected = _load_injected(resolved, base_dir)
    notes: list[str] = []
    if not injected and kis_client is None:
        notes.append(
            f"흐름 관측치 없음 — KIS 미설정 + 주입 파일({base_dir}/"
            f"{resolved:%Y%m%d}.json) 부재 → 전 플레이북 비활성(보수)"
        )
    if kis_client is None:
        notes.append("KIS 실시간 미설정 — 체결강도·호가·전고회복 미수집")
    notes.append("프리마켓 거래량(premkt_volume_ratio): NXT 소스 부재(SEL-1) — 미수집")

    toss = toss_client if toss_client is not None else _toss_from_env()

    store = market_store
    own_store = False
    if store is None and (kis_client is not None or toss is not None):
        store = MarketStore()
        own_store = True

    # 섹터 점화(P-11 Stage B) — 랭킹 1콜 + 업종 조인, 스냅샷당 1회.
    # 토스 미설정이거나 store가 업종 맵을 못 주면(테스트 페이크 등) 변수 결측(보수).
    hot: set[str] | None = None
    secmap: dict[str, list[str]] = {}
    if toss is not None and store is not None and hasattr(store, "sector_map_multi"):
        from trading.screener import SECTOR_SOURCES

        secmap = store.sector_map_multi(SECTOR_SOURCES)
        hot = _hot_sectors(toss, secmap)
    if hot is None:
        notes.append("섹터 점화(sector_ignition): 토스 랭킹 미가용 — 미수집")

    snapshot: dict[str, dict[str, float]] = {}
    for srtn in srtns:
        obs: dict[str, float] = dict(injected.get(srtn, {}))  # 주입을 베이스로
        if kis_client is not None and store is not None:
            prev_high = _prev_day_high(store, srtn, kis_client=kis_client, now=resolved)
            obs.update(_kis_observations(kis_client, srtn, prev_high))  # 실시간이 덮어씀
        if hot is not None:
            secs = secmap.get(srtn)
            if secs:  # 업종 미태깅 종목은 관측치 없음(보수 — 지어내지 않음)
                obs["sector_ignition"] = 1.0 if any(s in hot for s in secs) else 0.0
        snapshot[srtn] = obs

    if own_store and store is not None:
        store.close()
    return snapshot, notes


__all__ = [
    "INJECT_DIR",
    "OBSERVABLE_FLOW_DESC",
    "OBSERVABLE_FLOW_VARS",
    "SECTOR_IGNITION_MIN_MEMBERS",
    "build_snapshot",
]
