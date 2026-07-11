"""R6 — 모닝/저녁 보고 렌더 (정적 Jinja2, LLM 없음. 설계서 §3 R6·§8).

- 모든 수치에 as_of 병기(§8). 미수집 데이터는 **결측으로 명시** — 추측 대체 금지.
- **분량 가드(§8 "5분 내 독해 분량 강제")**: 초과 시 자동 축약이 아니라 **생성 실패 +
  P1 알림** — 분량 초과는 상류 설계 문제의 신호다(M4 지시).
- 산출: `.runtime/reports/<일자>-<종류>.md` 파일 + Telegram 채널 발송(채널 절단은 채널 몫,
  파일이 원본).
"""

import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from trading.alerts.model import Severity
from trading.alerts.store import AlertStore
from trading.collectors.base import KST, now_kst
from trading.collectors.market import DEFAULT_DB as MARKET_DB
from trading.contracts.order import OrderDraft, OrderStatus
from trading.contracts.scenario import ScenarioAxis
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
    pairs = [(pb, ps.draft(pb.order_draft_ref)) for pb in playbooks]
    names = _symbol_names(d.symbol for _, d in pairs if d is not None)
    rows: list[dict[str, Any]] = []
    for pb, draft in pairs:
        rows.append(
            {
                "id": pb.id,
                "draft_id": pb.order_draft_ref,
                "label": _label(draft.symbol, names) if draft else "",
                "status": draft.status.value if draft else "초안 없음",
                "arm": ", ".join(f"{k} {v}" for k, v in pb.arm_conditions.items()),
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


def _clip(text: str, width: int) -> str:
    """문장 경계 우선 절단 — width 내 마지막 문장 끝, 없으면 마지막 공백에서 말줄임."""
    if len(text) <= width:
        return text
    cut = text[:width]
    dot = cut.rfind(". ")
    if dot >= width // 2:
        return cut[: dot + 1]
    if " " in cut[width // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut + " …"


def _symbol_names(srtns: Iterable[str]) -> dict[str, str]:
    """시세 DB(daily_quotes)에서 종목명 조회 — DB 부재·미등재는 조용히 코드 표기 폴백."""
    wanted = sorted(set(srtns))
    if not wanted or not MARKET_DB.exists():
        return {}
    out: dict[str, str] = {}
    try:
        conn = sqlite3.connect(str(MARKET_DB))
        for s in wanted:
            row = conn.execute(
                "SELECT name FROM daily_quotes WHERE srtn_cd=? AND name IS NOT NULL "
                "ORDER BY bas_dt DESC LIMIT 1",
                (s,),
            ).fetchone()
            if row and row[0]:
                out[s] = str(row[0])
        conn.close()
    except sqlite3.Error:
        return out
    return out


def _label(srtn: str, names: dict[str, str]) -> str:
    name = names.get(srtn)
    return f"{name}({srtn})" if name else srtn


_CAP_EXPR = re.compile(r"^([0-9.]+)\s*\*\s*normal_unit$")


def _humanize_cap(cap: str | None) -> str:
    """"0.5 * normal_unit" → "기본단위의 50%". 그 외 표현식은 원문 유지."""
    m = _CAP_EXPR.match(cap or "")
    if not m:
        return cap or "(미지정)"
    return f"기본단위의 {float(m.group(1)) * 100:g}%"


def _r4_summary(events_limit: int = 200) -> tuple[dict[str, Any], str]:
    es = EventStore()
    events = es.recent(limit=events_limit)
    es.close()
    verified = [e for e in events if e.verification is not None]
    confirmed = [e for e in verified if e.verification is not None and e.verification.confirmed]
    refuted = [e for e in verified if e not in confirmed]
    refuted.sort(key=lambda e: e.catalyst_strength or 0.0, reverse=True)

    def _line(e: Any) -> str:
        return f"(강도 {e.catalyst_strength}) {_clip(e.summary_1line, 110)}"

    as_of = max((e.as_of for e in verified), default=None)
    return (
        {
            "total": len(verified),
            "confirmed": len(confirmed),
            "refuted": len(refuted),
            "confirmed_lines": [_line(e) for e in confirmed],   # 생존은 전부(의사결정 입력)
            "refuted_lines": [_line(e) for e in refuted[:5]],   # 기각은 강도 상위 5만
        },
        as_of.date().isoformat() if as_of else "(없음)",
    )


def _scenario_lines(axes: Sequence[ScenarioAxis]) -> list[str]:
    """ScenarioAxis 목록 → 축 제목(굵게) + 분기 불릿. 구조는 R5 산출 시점에 강제(scenario 계약)."""
    out: list[str] = []
    for ax in axes:
        if ax.title:
            out.append(f"**{ax.title}**")
        out.extend(f"- {ln}" for ln in ax.lines)
    return out


_SIDE_KO = {"buy": "매수", "sell": "매도"}


def _approval_rows(ps: PlaybookStore, day_compact: str) -> list[dict[str, Any]]:
    """승인 요청 행 — 결재가 이 섹션만으로 끝나도록 종목명·근거 1줄·arm 조건까지 병기."""
    pbs = [
        (pb, ps.draft(pb.order_draft_ref))
        for pb in ps.playbooks_for_day(day_compact)
    ]
    names = _symbol_names(d.symbol for _, d in pbs if d is not None)
    rows: list[dict[str, Any]] = []
    for pb, draft in pbs:
        if draft is None or draft.status is not OrderStatus.DRAFT:
            continue  # 승인 요청은 미결재(draft)만
        rows.append(
            {
                "id": draft.id,
                "label": _label(draft.symbol, names),
                "side": _SIDE_KO.get(draft.side.value, draft.side.value),
                "summary": pb.summary or "(근거 미기재 — 시나리오 섹션 참조)",
                "arm": ", ".join(f"{k} {v}" for k, v in pb.arm_conditions.items()),
                "stop": draft.stop.level if draft.stop else None,
                "time_stop": draft.time_stop_days,
                "cap": _humanize_cap(draft.total_size_cap),
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
    synth_as_of = run_meta[0] if run_meta else ""
    if synth_as_of and not synth_as_of[:1].isdigit():
        synth_as_of = ""  # "(no-playbook)" 류 코스메틱 값은 표기 생략

    # 보유 포지션(P-8, 수동 등록 기반) — §8 "무효화 조건 잔여 거리". EOD 가격으로 점검(밤 21:00).
    from trading.position_check import check_positions, render_lines

    position_lines = render_lines(check_positions(now=resolved, kis_client=None))

    swing_lines, swing_as_of = _swing_lines()

    # 미수집은 빈 섹션 나열 대신 한 군데 모아 명시(읽기 부담 제거, 추측 대체 없음은 유지)
    notes = [
        "집행 편차: KIS 잔고·체결 어댑터 미구현 — 포지션은 수동 등록 기반(실계좌 대사 후속)",
        "수급(투자자별 매매동향): KIS flows 수집 중(daily-eod) — 보고 섹션 배선은 후속",
    ]
    text = _env().get_template("evening.md.j2").render(
        day=today_iso,
        executions=[], positions=position_lines, flows=[],
        r4=r4, r4_as_of=r4_as_of,
        scenario_lines=_scenario_lines(run_meta[1] if run_meta else []),
        synth_as_of=synth_as_of,
        swing_lines=swing_lines, swing_as_of=swing_as_of,
        approvals=approvals, p2_alerts=p2, notes=notes,
    )
    return Rendered("evening", today_iso, _guard_length("evening", text, max_chars=max_chars))


def _swing_lines(limit: int = 8) -> tuple[list[str], str]:
    """P-9 스윙 기회 — 최신 스냅샷의 트리거 발화 종목(종목별 트리거 병합, 점수순 상한 limit).

    스윙 DB 비어 있으면 빈 리스트(섹션은 '트리거 없음' 문구로 렌더 — 침묵 생략 금지).
    """
    from trading.swing import SwingStore

    store = SwingStore()
    day = store.latest_bas_dt()
    if day is None:
        store.close()
        return [], ""
    merged: dict[str, tuple[str, float, list[str]]] = {}
    for cd, name, trigger, score in store.triggers_on(day):
        if cd in merged:
            merged[cd][2].append(trigger)
        else:
            merged[cd] = (name, score, [trigger])
    store.close()
    rows = sorted(merged.items(), key=lambda kv: kv[1][1], reverse=True)
    lines = [
        f"{name} — {', '.join(trigs)} (스윙 점수 {score:.2f}, `{cd}`)"
        for cd, (name, score, trigs) in rows[:limit]
    ]
    if len(rows) > limit:
        lines.append(f"…외 {len(rows) - limit}종목")
    return lines, day


__all__ = [
    "MAX_REPORT_CHARS",
    "Rendered",
    "ReportLengthError",
    "render_evening",
    "render_morning",
]
