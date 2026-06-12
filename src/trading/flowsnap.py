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
from datetime import datetime
from pathlib import Path
from typing import Any

from trading.collectors.base import KST, now_kst
from trading.collectors.kis import KisClient
from trading.collectors.market import MarketStore

INJECT_DIR = Path(".runtime") / "flow"

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


def _prev_day_high(store: MarketStore, srtn_cd: str) -> float | None:
    """시세 DB 최신 거래일의 고가(hipr) — 9~10시엔 전 거래일 = '전일 고가'."""
    cutoff = store.nth_recent_date(3) or ""
    rows = store.series_for(srtn_cd, cutoff)
    if not rows:
        return None
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
    if cur is not None and prev_high is not None:
        obs["prev_day_high_reclaim"] = 1.0 if cur > prev_high else 0.0
    try:
        ask = client.quote_asking_price(srtn_cd)
    except Exception:  # noqa: BLE001
        ask = {}
    bid_q, ask_q = _f(ask.get(_F_BID_QTY)), _f(ask.get(_F_ASK_QTY))
    if bid_q is not None and ask_q is not None and (bid_q + ask_q) > 0:
        obs["orderbook_imbalance"] = (bid_q - ask_q) / (bid_q + ask_q)
    return obs


def build_snapshot(
    srtns: Sequence[str],
    *,
    kis_client: KisClient | None,
    market_store: MarketStore | None = None,
    now: datetime | None = None,
    inject_dir: Path | None = None,
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """흐름 스냅샷 + 결측 notes. KIS 없으면 주입 파일만(전부 없으면 빈 스냅샷=비거래)."""
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

    store = market_store
    own_store = False
    if kis_client is not None and store is None:
        store = MarketStore()
        own_store = True

    snapshot: dict[str, dict[str, float]] = {}
    for srtn in srtns:
        obs: dict[str, float] = dict(injected.get(srtn, {}))  # 주입을 베이스로
        if kis_client is not None and store is not None:
            prev_high = _prev_day_high(store, srtn)
            obs.update(_kis_observations(kis_client, srtn, prev_high))  # 실시간이 덮어씀
        snapshot[srtn] = obs

    if own_store and store is not None:
        store.close()
    return snapshot, notes


__all__ = ["INJECT_DIR", "build_snapshot"]
