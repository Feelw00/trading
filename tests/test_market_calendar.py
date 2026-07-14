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
    in_after_market,
    in_extended_session,
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


def test_add_trading_days_skips_weekends_and_holidays() -> None:
    cal = MarketCalendar()
    # 6/10(수)부터 3거래일 후 = 6/15(월) (6/13~14 주말 건너뜀)
    assert cal.add_trading_days(date(2026, 6, 10), 3) == date(2026, 6, 15)
    assert cal.add_trading_days(date(2026, 6, 10), 0) == date(2026, 6, 10)  # n=0 그대로
    # 한글날(10/9 금) 포함 구간 건너뜀: 10/8(목)+1거래일=10/12(월)
    assert cal.add_trading_days(date(2026, 10, 8), 1) == date(2026, 10, 12)


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


# --- CAL-3: 애프터마켓(16:00~20:00, 2026-09-14~)도 휴면 창 ---


def test_after_market_window_only_from_effective_date() -> None:
    cal = MarketCalendar()
    # 시행 전(2026-07-13 월 17:00) — 애프터마켓 없음 → 라운드 허용
    assert not in_after_market(_kst(2026, 7, 13, 17, 0), cal)
    require_llm_rounds_allowed(_kst(2026, 7, 13, 17, 0), cal)
    # 시행일(2026-09-14 월) 17:00 — 애프터마켓 → 휴면
    assert in_after_market(_kst(2026, 9, 14, 17, 0), cal)
    with pytest.raises(MarketGuardError, match="애프터마켓"):
        require_llm_rounds_allowed(_kst(2026, 9, 14, 17, 0), cal)


def test_after_market_boundaries_and_gap_after_close() -> None:
    cal = MarketCalendar()
    assert not in_extended_session(_kst(2026, 9, 14, 15, 45), cal)  # 정규장~애프터 사이 공백
    assert in_extended_session(_kst(2026, 9, 14, 16, 0), cal)       # 애프터 개시(경계 포함)
    assert in_extended_session(_kst(2026, 9, 14, 20, 0), cal)       # 애프터 마감(경계 포함)
    assert not in_extended_session(_kst(2026, 9, 14, 20, 1), cal)


def test_pm_llm_slots_land_outside_dormant_window() -> None:
    """재배치된 pm 슬롯(20:02/20:15/20:32 · R5 21:05 · 보고 21:30)이 휴면 창 밖인가."""
    cal = MarketCalendar()
    for hh, mm in [(20, 2), (20, 15), (20, 32), (21, 5), (21, 30)]:
        require_llm_rounds_allowed(_kst(2026, 9, 14, hh, mm), cal)
    require_market_closed(_kst(2026, 9, 14, 21, 5), cal)  # R5 주문 설계


def test_order_drafting_blocked_during_after_market() -> None:
    cal = MarketCalendar()
    with pytest.raises(MarketGuardError, match="order drafting blocked"):
        require_market_closed(_kst(2026, 9, 14, 19, 0), cal)


def test_after_market_closed_on_holiday() -> None:
    # 2026-10-05(개천절 대체공휴일) 17:00 — 휴장일이므로 애프터마켓도 없다
    assert not in_after_market(_kst(2026, 10, 5, 17, 0), MarketCalendar.default())


def test_regular_session_semantics_unchanged() -> None:
    """정규장 판정(in_krx_session)은 불변 — 체결·수급·arm-check가 이 축을 쓴다."""
    cal = MarketCalendar()
    assert in_krx_session(_kst(2026, 9, 14, 10, 0), cal)
    assert not in_krx_session(_kst(2026, 9, 14, 17, 0), cal)  # 애프터마켓은 정규장이 아니다


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


# --- CAL-1 종결: 확인된 휴장일이 달력에 반영됐는가 ---

# 연속성 가드가 소스 무자료로 관측한 9일(data/market.sqlite no_data_days) — 전부 공식 확인분.
_OBSERVED_CLOSED = [
    date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8),      # 추석 연휴 + 대체
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),      # 설 연휴
    date(2026, 3, 2),                                              # 삼일절 대체
    date(2026, 5, 25),                                             # 부처님오신날 대체
    date(2026, 6, 3),                                              # 지방선거
]


@pytest.mark.parametrize("d", _OBSERVED_CLOSED)
def test_observed_closures_registered_in_calendar(d: date) -> None:
    """관측된 휴장일이 달력에 등록됐다 — 미등록이면 갭 오경보·거래일 오판이 재발한다."""
    assert not MarketCalendar.default().is_trading_day(d)


def test_future_closures_from_krx_notice() -> None:
    """관측 이후(미래) 휴장일 — KRX 공지 확인분. 미등록이면 잡이 휴장일에 거래일로 착각한다."""
    cal = MarketCalendar.default()
    assert not cal.is_trading_day(date(2026, 7, 17))   # 제헌절(2026 재지정)
    assert not cal.is_trading_day(date(2026, 8, 17))   # 광복절 대체
    assert not cal.is_trading_day(date(2026, 9, 24))   # 추석 연휴
    assert not cal.is_trading_day(date(2026, 9, 25))   # 추석
    assert not cal.is_trading_day(date(2026, 10, 5))   # 개천절 대체


def test_constitution_day_is_year_scoped_not_fixed() -> None:
    """제헌절은 2026년부터 공휴일 — 고정 목록에 넣으면 2025년 거래일을 휴장으로 오판한다."""
    cal = MarketCalendar.default()
    assert cal.is_trading_day(date(2025, 7, 17))       # 목요일, 당시 정상 거래일
    assert not cal.is_trading_day(date(2026, 7, 17))


def test_chuseok_2026_has_no_substitute_monday() -> None:
    """설·추석 대체공휴일은 일요일 중복 시에만 — 2026 추석(9/26 토)은 9/28 대체 없음(추측 금지)."""
    assert MarketCalendar.default().is_trading_day(date(2026, 9, 28))


def test_coverage_boundary_flags_unverified_years() -> None:
    """확인 범위를 넘긴 날짜는 is_covered=False — 음력·대체공휴일 미등록을 침묵시키지 않는다."""
    cal = MarketCalendar.default()
    assert cal.covered_through == date(2026, 12, 31)
    assert cal.is_covered(date(2026, 12, 31))
    assert not cal.is_covered(date(2027, 1, 4))


def test_from_file_accepts_object_entries(tmp_path: Path) -> None:
    p = tmp_path / "krx.json"
    p.write_text(
        json.dumps(
            {
                "covered_through": "2026-12-31",
                "holidays": [{"date": "2026-06-10", "name": "테스트 휴장"}, "2026-06-11"],
            }
        ),
        encoding="utf-8",
    )
    cal = MarketCalendar.from_file(p)
    assert not cal.is_trading_day(date(2026, 6, 10))  # 객체 항목
    assert not cal.is_trading_day(date(2026, 6, 11))  # 문자열 항목(구 포맷 호환)
    assert cal.covered_through == date(2026, 12, 31)
