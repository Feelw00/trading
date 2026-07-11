"""시장 캘린더·장중 게이팅 가드 (순수 코드, 스케줄러 아님 — SCHED-1).

스케줄은 openclaw cron/heartbeat가 전담하고, **시장시간·휴장일 판단은 각 잡이 호출하는
이 가드**가 수행한다(자체 스케줄러 금지). 설계서 §5:
- 장중 LLM 라운드 전면 휴면 → :func:`require_llm_rounds_allowed`.
- 주문 설계(R5)는 장 마감 후에만 → :func:`require_market_closed`.
- 미국 서머타임은 ``zoneinfo America/New_York`` 변환으로 처리(수동 DST 규칙 없음).

**"장중"의 범위 (CAL-3, 운영자 2026-07-11 결정):** 정규장(09:00–15:30) **+ 애프터마켓(16:00–20:00,
2026-09-14 시행)**. 애프터마켓은 실거래가 도는 시간이므로 휴면 창에 포함한다 —
:func:`in_extended_session`. 프리마켓(07:00–07:50)은 **2027년 말로 연기**돼 아직 창이 없다
(생기면 ``_PREMARKET_*`` 추가). 정규장 자체는 불변이라 :func:`in_krx_session`(정규장 판정 —
체결·수급·arm-check용)은 그대로 두고, LLM/주문 가드만 확장 창을 본다.

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

# 애프터마켓(CAL-3) — KRX 발표: 2026-09-14 시행, 16:00–20:00. 시행일 이전 날짜엔 창이 없다
# (과거 리플레이·백테스트가 실제로 열려 있던 시장을 휴장으로 오판하지 않게 연도·일자 경계를 둔다).
AFTER_OPEN = time(16, 0)
AFTER_CLOSE = time(20, 0)
AFTER_MARKET_EFFECTIVE = date(2026, 9, 14)

# 월-일 고정 법정 공휴일 + 연말 휴장(12-31). 음력·대체공휴일은 여기 절대 추가 금지(파일 주입).
_FIXED_CLOSED_MD: frozenset[tuple[int, int]] = frozenset(
    {(1, 1), (3, 1), (5, 1), (5, 5), (6, 6), (8, 15), (10, 3), (10, 9), (12, 25), (12, 31)}
)

_DEFAULT_HOLIDAYS_FILE = Path(__file__).parent / "krx_holidays.json"


class MarketGuardError(RuntimeError):
    """시장시간 가드 위반 — 잡은 실행하지 않고 종료한다(스킵은 정상 동작)."""


@dataclass(frozen=True)
class MarketCalendar:
    """KRX 거래일 판정. ``extra_holidays`` = KRX 공지로 확인된 명시 휴장일(YYYY-MM-DD).

    ``covered_through`` = 그 확인이 미치는 마지막 날. 이 날을 넘긴 시점의 판정은 음력·대체공휴일이
    비어 있을 수 있다(다음 해 공지 미반영) — :meth:`is_covered`로 드러내고 침묵하지 않는다.
    """

    extra_holidays: frozenset[date] = field(default_factory=frozenset)
    covered_through: date | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> "MarketCalendar":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        # 항목은 "YYYY-MM-DD" 또는 {"date": "YYYY-MM-DD", "name": ..., ...} 둘 다 허용.
        days = frozenset(
            date.fromisoformat(h if isinstance(h, str) else h["date"])
            for h in raw.get("holidays", [])
        )
        through = raw.get("covered_through")
        return cls(
            extra_holidays=days,
            covered_through=date.fromisoformat(through) if through else None,
        )

    def is_covered(self, d: date) -> bool:
        """d가 휴장일 확인 범위 안인가. False면 음력·대체공휴일 미등록 가능(CAL-1 갱신 필요)."""
        return self.covered_through is None or d <= self.covered_through

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

    def add_trading_days(self, d: date, n: int) -> date:
        """d로부터 n거래일 후(d 미포함). n=0이면 d 그대로. OrderDraft TTL 만료일 계산."""
        if n <= 0:
            return d
        cur = d
        for _ in range(n):
            cur = self.next_trading_day(cur)
        return cur


def _as_kst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise MarketGuardError("naive datetime 금지 — KST tz-aware로 호출하라")
    return dt.astimezone(KST)


def in_krx_session(dt: datetime, calendar: MarketCalendar | None = None) -> bool:
    """**정규장**(거래일 09:00–15:30 KST, 경계 포함 — 보수적) 여부.

    체결·수급·arm-check 등 "정규장 기준" 판정용. LLM/주문 가드는 :func:`in_extended_session`을 쓴다.
    """
    cal = calendar if calendar is not None else MarketCalendar.default()
    local = _as_kst(dt)
    if not cal.is_trading_day(local.date()):
        return False
    return KRX_OPEN <= local.time() <= KRX_CLOSE


def in_after_market(dt: datetime, calendar: MarketCalendar | None = None) -> bool:
    """애프터마켓(거래일 16:00–20:00 KST, 2026-09-14~) 여부. 시행일 전이면 항상 False."""
    cal = calendar if calendar is not None else MarketCalendar.default()
    local = _as_kst(dt)
    if local.date() < AFTER_MARKET_EFFECTIVE:
        return False
    if not cal.is_trading_day(local.date()):
        return False
    return AFTER_OPEN <= local.time() <= AFTER_CLOSE


def in_extended_session(dt: datetime, calendar: MarketCalendar | None = None) -> bool:
    """실거래가 도는 시간 = 정규장 ∪ 애프터마켓 (CAL-3 결정: 둘 다 LLM 휴면 창)."""
    return in_krx_session(dt, calendar) or in_after_market(dt, calendar)


def _session_label(dt: datetime, calendar: MarketCalendar | None = None) -> str:
    return "정규장" if in_krx_session(dt, calendar) else "애프터마켓"


def require_llm_rounds_allowed(
    now: datetime | None = None, calendar: MarketCalendar | None = None
) -> None:
    """LLM 라운드(R2~R5, R7) 진입 가드 — 장중이면 거부(설계서 §5: 장중 전면 휴면).

    장중 = 정규장 + 애프터마켓(CAL-3). cron 디스패치(`trading.run`)가 호출한다 — 수동 CLI는
    CAL-2대로 우회 가능.
    """
    resolved = now if now is not None else now_kst()
    if in_extended_session(resolved, calendar):
        raise MarketGuardError(
            f"LLM round blocked: KRX {_session_label(resolved, calendar)}"
            f"({_as_kst(resolved).isoformat()}) — §5 휴면"
        )


def require_market_closed(
    now: datetime | None = None, calendar: MarketCalendar | None = None
) -> None:
    """주문 설계 경로(R5) 가드 — 장중 주문 초안 생성 금지(설계서 §1 운영 전제).

    애프터마켓(16:00–20:00)도 실거래 시간이므로 초안 생성 금지(CAL-3).
    """
    resolved = now if now is not None else now_kst()
    if in_extended_session(resolved, calendar):
        raise MarketGuardError(
            f"order drafting blocked: KRX {_session_label(resolved, calendar)}"
            f"({_as_kst(resolved).isoformat()})"
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
    "AFTER_CLOSE",
    "AFTER_MARKET_EFFECTIVE",
    "AFTER_OPEN",
    "KRX_CLOSE",
    "KRX_OPEN",
    "MarketCalendar",
    "MarketGuardError",
    "in_after_market",
    "in_extended_session",
    "in_krx_session",
    "require_llm_rounds_allowed",
    "require_market_closed",
    "require_trading_day",
    "us_session_kst",
]
