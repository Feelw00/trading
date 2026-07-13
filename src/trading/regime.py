"""시장 레짐 감시 (EXEC-7, 2026-07-13 폭락 사후 설계) — 지수 급변의 전용 채널.

배경: 7/13 코스피 -8.9% 폭락이 촉매 파이프라인(R4가 "사후 보도 = 촉매 부적격"으로
정당 기각)에서 소멸해 R5가 폭락을 모른 채 계획을 세웠다. 레짐은 촉매가 아니라
**수치 사실** — LLM 판정 없이 순수 코드가 지수 등락을 직접 관측하는 별도 채널을 둔다.

- `snapshot(toss)`: KOSPI/KOSDAQ 현재가 vs 전 거래일 종가(토스 지표 캔들) → 등락률.
- 판정(결정론): 코스피 기준 ≤-5% RISK_OFF(신규 진입 중단) / ≤-3% CAUTION(배분 절반) /
  그 외 NORMAL. 임계는 env(REGIME_CAUTION_PCT/REGIME_RISKOFF_PCT)로 조정.
- 소비처: 집행기(진입 게이트), R5 프롬프트·R6 보고(당일 실시간 백드롭 줄).
- 관측 실패는 NORMAL이 아니라 **UNKNOWN**(보수 — 집행기는 CAUTION과 동일 취급).
"""

import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from trading.collectors.base import KST, now_kst


class Regime(str, Enum):
    NORMAL = "normal"
    CAUTION = "caution"      # 코스피 당일 ≤ -3%: 신규 진입 배분 절반
    RISK_OFF = "risk_off"    # 코스피 당일 ≤ -5%: 신규 진입 중단(청산 관리는 계속)
    UNKNOWN = "unknown"      # 관측 불가 — CAUTION과 동일 취급(보수)


def _caution_pct() -> float:
    try:
        return float(os.environ.get("REGIME_CAUTION_PCT", ""))
    except ValueError:
        return -3.0


def _riskoff_pct() -> float:
    try:
        return float(os.environ.get("REGIME_RISKOFF_PCT", ""))
    except ValueError:
        return -5.0


@dataclass(frozen=True)
class RegimeSnapshot:
    regime: Regime
    lines: list[str]              # 보고·R5 주입용 사람 읽는 줄("KOSPI 6,806.93 (당일 -8.9%)")
    kospi_change_pct: float | None


def _f(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _prev_close(candles: list[dict[str, Any]], today: str) -> float | None:
    """가장 최근의 '오늘 이전' 일봉 종가 — 장중엔 오늘 캔들이 진행형으로 섞여 온다(관측)."""
    for c in candles:
        ts = str(c.get("timestamp") or "")
        if ts[:10] and ts[:10] < today:
            return _f(c.get("closePrice"))
    return None


def snapshot(toss: Any | None, *, now: datetime | None = None) -> RegimeSnapshot:
    """지수 실시간 레짐 스냅샷 — 토스 미설정·조회 실패는 UNKNOWN(추측 금지)."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    today = resolved.date().isoformat()
    if toss is None:
        return RegimeSnapshot(Regime.UNKNOWN, ["레짐: 관측 불가(토스 미설정)"], None)
    lines: list[str] = []
    kospi_chg: float | None = None
    try:
        prices = {
            str(r.get("symbol")): _f(r.get("lastPrice"))
            for r in toss.market_indicator_prices(["KOSPI", "KOSDAQ"])
        }
        for sym in ("KOSPI", "KOSDAQ"):
            cur = prices.get(sym)
            prev = _prev_close(toss.market_indicator_candles(sym, interval="1d", count=5), today)
            if cur is None or prev is None or prev <= 0:
                lines.append(f"{sym}: 관측 불가")
                continue
            chg = (cur - prev) / prev * 100
            lines.append(f"{sym} {cur:,.2f} (당일 {chg:+.1f}%, 실시간)")
            if sym == "KOSPI":
                kospi_chg = chg
    except Exception:  # noqa: BLE001 — 조회 실패는 UNKNOWN(값을 지어내지 않는다)
        return RegimeSnapshot(Regime.UNKNOWN, ["레짐: 지수 조회 실패"], None)
    if kospi_chg is None:
        return RegimeSnapshot(Regime.UNKNOWN, lines, None)
    if kospi_chg <= _riskoff_pct():
        return RegimeSnapshot(Regime.RISK_OFF, lines, kospi_chg)
    if kospi_chg <= _caution_pct():
        return RegimeSnapshot(Regime.CAUTION, lines, kospi_chg)
    return RegimeSnapshot(Regime.NORMAL, lines, kospi_chg)


def live_backdrop_lines(*, now: datetime | None = None) -> list[str]:
    """R5 프롬프트·R6 보고 주입용 당일 실시간 백드롭(결정론) — EOD 지연의 보정 채널.

    7/13 사고 재발 방지: R5가 T-1 지수만 보고 계획하지 않도록 렌더/합성 직전에 호출."""
    from trading.collectors.toss import client_from_env

    snap = snapshot(client_from_env(), now=now)
    lines = list(snap.lines)
    if snap.regime in (Regime.CAUTION, Regime.RISK_OFF):
        lines.insert(0, f"⚠️ 시장 레짐 {snap.regime.value.upper()} — 당일 지수 급락, 신규 진입 보수화 중")
    return lines


__all__ = ["Regime", "RegimeSnapshot", "live_backdrop_lines", "snapshot"]
