"""market_calendar 가드 — 거래일·장중 게이팅·미국 DST 단위 테스트 (순수 코드).

M2 AC(SCHED-1 개정 반영): "장중 시각에 LLM 라운드 시도 시 거부"를 스케줄러가 아니라
각 잡이 호출하는 가드 레벨에서 증명한다.
"""

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading.market_calendar.calendar import (
    MarketCalendar,
    MarketGuardError,
    in_krx_session,
    require_llm_rounds_allowed,
    require_market_closed,
    require_trading_day,
    us_session_kst,
)

KST = ZoneInfo("Asia/Seoul")


def _kst(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=KST)


# --- 거래일 판정 ---


def test_weekend_not_trading_day() -> None:
    cal = MarketCalendar()
    assert not cal.is_trading_day(date(2026, 6, 13))  # 토
    assert not cal.is_trading_day(date(2026, 6, 14))  # 일
    assert cal.is_trading_day(date(2026, 6, 10))      # 수


def test_fixed_statutory_holiday_closed() -> None:
    cal = MarketCalendar()
    assert not cal.is_trading_day(date(2026, 1, 1))    # 신정(목)
    assert not cal.is_trading_day(date(2026, 10, 9))   # 한글날(금)
    assert not cal.is_trading_day(date(2026, 12, 31))  # 연말 휴장(목)


def test_injected_holiday_file_honored(tmp_path: Path) -> None:
    p = tmp_path / "krx.json"
    p.write_text(json.dumps({"holidays": ["2026-06-10"]}), encoding="utf-8")
    cal = MarketCalendar.from_file(p)
    assert not cal.is_trading_day(date(2026, 6, 10))
    assert cal.is_trading_day(date(2026, 6, 11))


def test_latest_and_next_trading_day_skip_closures() -> None:
    cal = MarketCalendar()
    # 2026-10-09(금, 한글날) → 직전 거래일 10-08(목), 다음 거래일 10-12(월)
    assert cal.latest_trading_day(date(2026, 10, 9)) == date(2026, 10, 8)
    assert cal.next_trading_day(date(2026, 10, 9)) == date(2026, 10, 12)
    # 일요일 기준 직전 거래일 = 금요일
    assert cal.latest_trading_day(date(2026, 6, 14)) == date(2026, 6, 12)


# --- 장중 세션 ---


def test_session_boundaries_inclusive() -> None:
    cal = MarketCalendar()
    assert not in_krx_session(_kst(2026, 6, 10, 8, 59), cal)
    assert in_krx_session(_kst(2026, 6, 10, 9, 0), cal)
    assert in_krx_session(_kst(2026, 6, 10, 15, 30), cal)
    assert not in_krx_session(_kst(2026, 6, 10, 15, 31), cal)


def test_no_session_on_holiday_or_weekend() -> None:
    cal = MarketCalendar()
    assert not in_krx_session(_kst(2026, 6, 13, 10, 0), cal)  # 토
    assert not in_krx_session(_kst(2026, 1, 1, 10, 0), cal)   # 신정


def test_naive_datetime_rejected() -> None:
    with pytest.raises(MarketGuardError, match="naive"):
        in_krx_session(datetime(2026, 6, 10, 10, 0), MarketCalendar())


def test_non_kst_tz_converted() -> None:
    # 01:00 UTC = 10:00 KST → 장중
    utc = datetime(2026, 6, 10, 1, 0, tzinfo=ZoneInfo("UTC"))
    assert in_krx_session(utc, MarketCalendar())


# --- LLM 라운드·주문 설계 가드 (M2 AC) ---


def test_llm_round_rejected_during_session() -> None:
    cal = MarketCalendar()
    with pytest.raises(MarketGuardError, match="LLM round blocked"):
        require_llm_rounds_allowed(_kst(2026, 6, 10, 10, 0), cal)


def test_llm_round_allowed_after_close_and_on_holiday() -> None:
    cal = MarketCalendar()
    require_llm_rounds_allowed(_kst(2026, 6, 10, 16, 30), cal)  # 석간 R2 슬롯
    require_llm_rounds_allowed(_kst(2026, 6, 10, 6, 0), cal)    # 조간 슬롯
    require_llm_rounds_allowed(_kst(2026, 6, 13, 10, 0), cal)   # 토(R7 등)


def test_order_drafting_blocked_during_session() -> None:
    cal = MarketCalendar()
    with pytest.raises(MarketGuardError, match="order drafting blocked"):
        require_market_closed(_kst(2026, 6, 10, 14, 0), cal)
    require_market_closed(_kst(2026, 6, 10, 20, 30), cal)  # R5 슬롯은 통과


def test_require_trading_day_skips_holiday() -> None:
    cal = MarketCalendar()
    require_trading_day(_kst(2026, 6, 10, 15, 45), cal)
    with pytest.raises(MarketGuardError, match="not a trading day"):
        require_trading_day(_kst(2026, 6, 14, 15, 45), cal)


# --- 미국 DST (zoneinfo 변환) ---


def test_us_session_kst_summer_edt() -> None:
    # EDT(UTC-4): 16:00 마감 = 익일 05:00 KST
    _, close_kst = us_session_kst(date(2026, 6, 9))
    assert close_kst == _kst(2026, 6, 10, 5, 0)


def test_us_session_kst_winter_est() -> None:
    # EST(UTC-5): 16:00 마감 = 익일 06:00 KST
    open_kst, close_kst = us_session_kst(date(2026, 1, 15))
    assert close_kst == _kst(2026, 1, 16, 6, 0)
    assert open_kst == _kst(2026, 1, 15, 23, 30)


def test_default_calendar_loads_packaged_file() -> None:
    cal = MarketCalendar.default()
    assert isinstance(cal.extra_holidays, frozenset)
