"""시장 캘린더·장중 게이팅 가드 (순수 코드, 스케줄러 아님 — SCHED-1).

스케줄은 openclaw cron/heartbeat가 전담하고, **시장시간·휴장일 판단은 각 잡이 호출하는
이 가드**가 수행한다(자체 스케줄러 금지). 설계서 §5:
- 장중(09:00–15:30 KST) LLM 라운드 전면 휴면 → :func:`require_llm_rounds_allowed`.
- 주문 설계(R5)는 장 마감 후에만 → :func:`require_market_closed`.
- 미국 서머타임은 ``zoneinfo America/New_York`` 변환으로 처리(수동 DST 규칙 없음).

휴장일 데이터(환각 가드):
- **확실한 고정 공휴일(월-일 고정)만** 코드에 둔다. 음력(설날·추석·석가탄신일)·대체공휴일·
  임시휴장은 추측하지 않는다 — KRX 공지로 확인한 명시 날짜를 JSON 파일로 주입
  (``krx_holidays.json`` 또는 :meth:`MarketCalendar.from_file`). OPEN_QUESTIONS CAL-1.
- 미등록 휴장일의 실패 방향은 안전하다: 휴장일을 거래일로 오인하면 가드가 불필요하게
  LLM을 더 막을 뿐, 장중에 LLM이 풀리는 일은 없다.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.collectors.base import KST, now_kst

US_EASTERN = ZoneInfo("America/New_York")

KRX_OPEN = time(9, 0)
KRX_CLOSE = time(15, 30)
US_OPEN_ET = time(9, 30)
US_CLOSE_ET = time(16, 0)

# 월-일 고정 법정 공휴일 + 연말 휴장(12-31). 음력·대체공휴일은 여기 절대 추가 금지(파일 주입).
_FIXED_CLOSED_MD: frozenset[tuple[int, int]] = frozenset(
    {(1, 1), (3, 1), (5, 1), (5, 5), (6, 6), (8, 15), (10, 3), (10, 9), (12, 25), (12, 31)}
)

_DEFAULT_HOLIDAYS_FILE = Path(__file__).parent / "krx_holidays.json"


class MarketGuardError(RuntimeError):
    """시장시간 가드 위반 — 잡은 실행하지 않고 종료한다(스킵은 정상 동작)."""


@dataclass(frozen=True)
class MarketCalendar:
    """KRX 거래일 판정. ``extra_holidays`` = KRX 공지로 확인된 명시 휴장일(YYYY-MM-DD)."""

    extra_holidays: frozenset[date] = field(default_factory=frozenset)

    @classmethod
    def from_file(cls, path: str | Path) -> "MarketCalendar":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        days = frozenset(date.fromisoformat(d) for d in raw.get("holidays", []))
        return cls(extra_holidays=days)

    @classmethod
    def default(cls) -> "MarketCalendar":
        """패키지 동봉 ``krx_holidays.json`` (운영자가 KRX 공지 기준으로 유지)."""
        if _DEFAULT_HOLIDAYS_FILE.exists():
            return cls.from_file(_DEFAULT_HOLIDAYS_FILE)
        return cls()

    def is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:  # 토·일
            return False
        if (d.month, d.day) in _FIXED_CLOSED_MD:
            return False
        return d not in self.extra_holidays

    def latest_trading_day(self, d: date) -> date:
        """d 포함, 가장 가까운 과거 거래일."""
        cur = d
        for _ in range(366):
            if self.is_trading_day(cur):
                return cur
            cur -= timedelta(days=1)
        raise MarketGuardError(f"no trading day within a year before {d}")

    def next_trading_day(self, d: date) -> date:
        """d 이후(미포함) 첫 거래일."""
        cur = d + timedelta(days=1)
        for _ in range(366):
            if self.is_trading_day(cur):
                return cur
            cur += timedelta(days=1)
        raise MarketGuardError(f"no trading day within a year after {d}")


def _as_kst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise MarketGuardError("naive datetime 금지 — KST tz-aware로 호출하라")
    return dt.astimezone(KST)


def in_krx_session(dt: datetime, calendar: MarketCalendar | None = None) -> bool:
    """장중(거래일 09:00–15:30 KST, 경계 포함 — 보수적) 여부."""
    cal = calendar if calendar is not None else MarketCalendar.default()
    local = _as_kst(dt)
    if not cal.is_trading_day(local.date()):
        return False
    return KRX_OPEN <= local.time() <= KRX_CLOSE


def require_llm_rounds_allowed(
    now: datetime | None = None, calendar: MarketCalendar | None = None
) -> None:
    """LLM 라운드(R2~R5, R7) 진입 가드 — 장중이면 거부(설계서 §5: 장중 전면 휴면)."""
    resolved = now if now is not None else now_kst()
    if in_krx_session(resolved, calendar):
        raise MarketGuardError(
            f"LLM round blocked: KRX 장중({_as_kst(resolved).isoformat()}) — §5 휴면"
        )


def require_market_closed(
    now: datetime | None = None, calendar: MarketCalendar | None = None
) -> None:
    """주문 설계 경로(R5) 가드 — 장중 주문 초안 생성 금지(설계서 §1 운영 전제)."""
    resolved = now if now is not None else now_kst()
    if in_krx_session(resolved, calendar):
        raise MarketGuardError(
            f"order drafting blocked: KRX 장중({_as_kst(resolved).isoformat()})"
        )


def require_trading_day(
    now: datetime | None = None, calendar: MarketCalendar | None = None
) -> None:
    """거래일 한정 잡(수급 수집 등) 가드 — 휴장일이면 스킵 신호로 예외."""
    resolved = _as_kst(now if now is not None else now_kst())
    cal = calendar if calendar is not None else MarketCalendar.default()
    if not cal.is_trading_day(resolved.date()):
        raise MarketGuardError(f"not a trading day: {resolved.date().isoformat()}")


def us_session_kst(d: date) -> tuple[datetime, datetime]:
    """미국 정규장(09:30–16:00 ET, d=ET 기준 날짜)의 KST 시각 — DST는 zoneinfo가 처리."""
    open_et = datetime.combine(d, US_OPEN_ET, tzinfo=US_EASTERN)
    close_et = datetime.combine(d, US_CLOSE_ET, tzinfo=US_EASTERN)
    return open_et.astimezone(KST), close_et.astimezone(KST)


__all__ = [
    "KRX_CLOSE",
    "KRX_OPEN",
    "MarketCalendar",
    "MarketGuardError",
    "in_krx_session",
    "require_llm_rounds_allowed",
    "require_market_closed",
    "require_trading_day",
    "us_session_kst",
]
