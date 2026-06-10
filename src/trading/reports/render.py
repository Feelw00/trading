"""R6 — 모닝/저녁 보고 렌더 (정적 Jinja2, LLM 없음. 설계서 §3 R6·§8).

- 모든 수치에 as_of 병기(§8). 미수집 데이터는 **결측으로 명시** — 추측 대체 금지.
- **분량 가드(§8 "5분 내 독해 분량 강제")**: 초과 시 자동 축약이 아니라 **생성 실패 +
  P1 알림** — 분량 초과는 상류 설계 문제의 신호다(M4 지시).
- 산출: `.runtime/reports/<일자>-<종류>.md` 파일 + Telegram 채널 발송(채널 절단은 채널 몫,
  파일이 원본).
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from trading.alerts.model import Severity
from trading.alerts.store import AlertStore
from trading.collectors.base import KST, now_kst
from trading.contracts.order import OrderDraft, OrderStatus
from trading.journal.events import EventStore
from trading.journal.playbooks import PlaybookStore

_TEMPLATES = Path(__file__).parent / "templates"
_MACRO_GLOB = "macro_indicators.sqlite"

# 5분 독해 분량(§8) — 한국어 분당 ~1,500자 가정의 보수 상한. 운영 결정으로 조정(knob).
MAX_REPORT_CHARS = 7000


class ReportLengthError(RuntimeError):
    """분량 가드 위반 — 자동 축약하지 않는다(상류 설계 문제의 신호)."""


@dataclass(frozen=True)
class Rendered:
    kind: str           # morning | evening
    day: str            # YYYY-MM-DD
    text: str


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        undefined=StrictUndefined,   # 누락 변수는 조용히 빈칸이 아니라 에러
        autoescape=False,            # 마크다운 평문
        trim_blocks=False,
    )


def _macro_lines() -> list[str]:
    """최신 거시 landing에서 verified 지표 — as_of 병기(reason_news와 동일 규약)."""
    base = Path(".runtime") / "collect"
    if not base.exists():
        return []
    dbs = sorted(base.glob(f"*/{_MACRO_GLOB}"))
    if not dbs:
        return []
    try:
        conn = sqlite3.connect(str(dbs[-1]))
        rows = conn.execute(
            "SELECT name, value, unit, as_of FROM facts WHERE verified=1 GROUP BY name "
            "ORDER BY name"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [f"{n}: {v}{u or ''} (as_of {a})" for n, v, u, a in rows]


def _guard_length(kind: str, text: str, *, max_chars: int) -> str:
    if len(text) > max_chars:
        raise ReportLengthError(
            f"{kind} 보고 분량 초과: {len(text)} > {max_chars}자 — "
            "자동 축약 금지, 상류(플레이북 수·체크리스트) 설계를 줄여라"
        )
    return text


def render_morning(
    *,
    now: datetime | None = None,
    playbook_store: PlaybookStore | None = None,
    max_chars: int = MAX_REPORT_CHARS,
) -> Rendered:
    """06:50 모닝 브리핑(읽기 전용) — 간밤 거시·오늘 플레이북/주문 상태·체크리스트."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day_compact = resolved.strftime("%Y%m%d")
    ps = playbook_store if playbook_store is not None else PlaybookStore()
    playbooks = ps.playbooks_for_day(day_compact)
    rows: list[dict[str, Any]] = []
    for pb in playbooks:
        draft = ps.draft(pb.order_draft_ref)
        rows.append(
            {
                "id": pb.id,
                "draft_id": pb.order_draft_ref,
                "status": draft.status.value if draft else "초안 없음",
                "arm": dict(pb.arm_conditions),
            }
        )
    run_meta = ps.latest_run()
    if playbook_store is None:
        ps.close()

    macro = _macro_lines()
    notes: list[str] = []
    if not macro:
        notes.append("거시 미수집 — 간밤 백드롭 부재(collect-macro 확인)")
    checklist = run_meta[2] if run_meta else []

    text = _env().get_template("morning.md.j2").render(
        day=resolved.date().isoformat(),
        macro=macro, playbooks=rows, checklist=checklist, notes=notes,
    )
    return Rendered("morning", resolved.date().isoformat(),
                    _guard_length("morning", text, max_chars=max_chars))


def _r4_summary(events_limit: int = 200) -> tuple[dict[str, Any], str]:
    es = EventStore()
    events = es.recent(limit=events_limit)
    es.close()
    verified = [e for e in events if e.verification is not None]
    confirmed = [e for e in verified if e.verification is not None and e.verification.confirmed]
    lines = [
        f"[{'생존' if (e.verification and e.verification.confirmed) else '기각'}] "
        f"({e.catalyst_strength}) {e.summary_1line[:60]}"
        for e in verified[:8]
    ]
    as_of = max((e.as_of for e in verified), default=None)
    return (
        {
            "total": len(verified),
            "confirmed": len(confirmed),
            "refuted": len(verified) - len(confirmed),
            "lines": lines,
        },
        as_of.date().isoformat() if as_of else "(없음)",
    )


def _approval_rows(ps: PlaybookStore, day_compact: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pb in ps.playbooks_for_day(day_compact):
        draft: OrderDraft | None = ps.draft(pb.order_draft_ref)
        if draft is None or draft.status is not OrderStatus.DRAFT:
            continue  # 승인 요청은 미결재(draft)만
        rows.append(
            {
                "id": draft.id, "symbol": draft.symbol, "side": draft.side.value,
                "stop": draft.stop.level if draft.stop else None,
                "time_stop": draft.time_stop_days, "cap": draft.total_size_cap,
                "status": draft.status.value,
            }
        )
    return rows


def render_evening(
    *,
    now: datetime | None = None,
    playbook_store: PlaybookStore | None = None,
    alert_store: AlertStore | None = None,
    max_chars: int = MAX_REPORT_CHARS,
) -> Rendered:
    """21:00 저녁 결재 보고 — R4 요약·시나리오·승인 요청·P2. 미수집 섹션은 결측 명시."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day_compact = resolved.strftime("%Y%m%d")

    ps = playbook_store if playbook_store is not None else PlaybookStore()
    run_meta = ps.latest_run()
    approvals = _approval_rows(ps, day_compact)
    if playbook_store is None:
        ps.close()

    als = alert_store if alert_store is not None else AlertStore()
    today_iso = resolved.date().isoformat()
    p2 = [
        f"{a.what} | 규칙: {a.rule} ({a.created_at.isoformat(timespec='minutes')})"
        for a in als.recent(limit=100)
        if a.severity is Severity.P2 and a.created_at.astimezone(KST).date().isoformat() == today_iso
    ]
    if alert_store is None:
        als.close()

    r4, r4_as_of = _r4_summary()
    text = _env().get_template("evening.md.j2").render(
        day=today_iso,
        executions=[], positions=[], flows=[],     # 어댑터 미구현 — 템플릿이 결측 명시
        r4=r4, r4_as_of=r4_as_of,
        scenario_tree=(run_meta[1] if run_meta else ""),
        synth_as_of=(run_meta[0] if run_meta else "(없음)"),
        approvals=approvals, p2_alerts=p2, notes=[],
    )
    return Rendered("evening", today_iso, _guard_length("evening", text, max_chars=max_chars))


__all__ = [
    "MAX_REPORT_CHARS",
    "Rendered",
    "ReportLengthError",
    "render_evening",
    "render_morning",
]
