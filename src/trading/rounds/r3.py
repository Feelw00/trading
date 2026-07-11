"""R3 — 페르소나 분석 (입력격리 병렬, claude -p / 설계서 §3).

후보 1종목에 대해 3 페르소나(수급/사이클/매크로)가 **각자 격리된 입력 슬라이스**로 ThesisRecord 생성.
입력격리는 catalyst_type으로 촉매를 페르소나별 분배(수급→flow_demand, 사이클→실적·공급망·제품,
매크로→거시·정책) + 페르소나별 데이터(사이클=재무, 매크로=거시 백드롭). 호출이 분리돼 컨텍스트 오염 없음.

설계서 규약: **invalidation 필수**(관측가능 무효화 조건) — 누락 시 1회 재생성, 재실패 폐기.
horizon은 거래일(스윙 3~15d). 미수집 핵심지표(투자자별 매매·DRAM가격 등 🔴)는 **보류**로 표기하고
confidence를 낮춘다(추측 금지). R3 산출은 R4 적대검증/R5 합성으로만 흐른다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from trading.collectors.base import now_kst
from trading.contracts.event import EventRecord
from trading.contracts.factpack import FactPack
from trading.contracts.thesis import Direction, Persona, ThesisRecord
from trading.domains import CatalystType
from trading.llm import LLMClient, LLMError, complete_json

# 촉매유형 → 담당 페르소나(입력격리 분배). None 촉매는 모든 슬라이스에 공통 노출.
_CATALYST_PERSONA: dict[CatalystType, Persona] = {
    CatalystType.FLOW_DEMAND: Persona.SUPPLY,
    CatalystType.RUMOR_UNCONFIRMED: Persona.SUPPLY,
    CatalystType.EARNINGS: Persona.CYCLE,
    CatalystType.GUIDANCE: Persona.CYCLE,
    CatalystType.SUPPLY_CHAIN: Persona.CYCLE,
    CatalystType.PRODUCT_TECH: Persona.CYCLE,
    CatalystType.MA_RESTRUCTURE: Persona.CYCLE,
    CatalystType.MANAGEMENT: Persona.CYCLE,
    CatalystType.MACRO: Persona.MACRO,
    CatalystType.POLICY_REGULATION: Persona.MACRO,
    CatalystType.LEGAL: Persona.MACRO,
}


@dataclass(frozen=True)
class PersonaSpec:
    persona: Persona
    question: str
    lens: str  # 강조 데이터 + 미수집 지표 보류 안내


PERSONAS: tuple[PersonaSpec, ...] = (
    PersonaSpec(
        Persona.SUPPLY,
        "누가 사고 팔며, 강제 매물이 남았는가?",
        "수급 렌즈. **투자자별 매매동향·신용잔고·공매도잔고·예탁금은 미수집(🔴)** — "
        "거래대금배(개인 강도 프록시)와 flow_demand 촉매로만 판단하라. 핵심 수급지표 부재는 "
        "confidence를 낮추고 invalidation에 '수급데이터 확보 시 재검증' 류를 포함.",
    ),
    PersonaSpec(
        Persona.CYCLE,
        "이익의 2차 미분이 어디를 향하는가?",
        "사이클 렌즈. **DRAM/NAND 고정·현물가·캐펙스 가이던스는 미수집** — 재무 YoY와 "
        "실적·가이던스·공급망·제품 촉매로만 이익 모멘텀 방향을 판단하라.",
    ),
    PersonaSpec(
        Persona.MACRO,
        "외생 변수가 수급 루프를 강화하는가 차단하는가?",
        "매크로 렌즈. 거시 백드롭(환율·금리·유가·지수)과 거시·정책 촉매로 외생 압력의 방향을 판단하라.",
    ),
)


@dataclass(frozen=True)
class R3Config:
    horizon_default: int = 7      # invalid horizon 시 기본(거래일)
    horizon_max: int = 30
    confidence_default: float = 0.3


@dataclass(frozen=True)
class R3Result:
    theses: list[ThesisRecord]
    rejected: int                 # invalidation 누락 등 폐기(재생성 후에도)
    persona_errors: list[str] = field(default_factory=list)  # LLM 호출 실패 페르소나


def events_for_persona(persona: Persona, events: Sequence[EventRecord]) -> list[EventRecord]:
    """페르소나 담당 촉매만(입력격리). 미분류(catalyst_type None)는 공통 노출."""
    return [
        e for e in events
        if e.catalyst_type is None or _CATALYST_PERSONA.get(e.catalyst_type) == persona
    ]


def _price_line(fp: FactPack | None) -> str:
    if fp is None:
        return "가격: (미수집)"
    p = fp.price
    # 미산출(None)은 "0%"가 아니라 미산출로 — 히스토리 부족을 수치인 척 넘기지 않는다.
    short = f"{p.mom_short_pct:.0f}%" if p.mom_short_pct is not None else "미산출"
    long_ = f"{p.mom_long_pct:.0f}%" if p.mom_long_pct is not None else "미산출"
    # 창이 252거래일에 못 미치면 "52주"라고 부르지 않는다(근접도 과대 → LLM 오판).
    win = p.high_window_days
    high_label = "52주근접" if win is None or win >= 252 else f"{win}일고가근접"
    return (
        f"가격(as_of {p.as_of}): 종가 {p.close} · 거래대금배 {p.tr_value_surge:.1f} · "
        f"단기 {short} · 장기 {long_} · {high_label} {p.high_252_proximity:.2f}"
    )


def _financial_lines(fp: FactPack | None) -> str:
    if fp is None or not fp.financials:
        return "재무: (미수집)"
    rows = [
        f"  {f.account}: 당기 {f.thstrm} / 전기 {f.frmtrm}" + (f" (YoY {f.yoy_pct:+.0f}%)" if f.yoy_pct is not None else "")
        for f in fp.financials[:6]
    ]
    return "재무(DART):\n" + "\n".join(rows)


def _event_lines(events: Sequence[EventRecord]) -> str:
    if not events:
        return "  (담당 촉매 없음)"
    out = []
    for e in events:
        conf = "" if e.verification is None else (" [R4 confirmed]" if e.verification.confirmed else " [R4 refuted]")
        ct = e.catalyst_type.value if e.catalyst_type else "기타"
        sc = e.scope.value if e.scope else "?"
        out.append(f"  [{e.id}] {ct}/{sc} str={e.catalyst_strength}{conf} :: {e.summary_1line}")
    return "\n".join(out)


def build_prompt(
    spec: PersonaSpec,
    candidate: tuple[str, str],
    fp: FactPack | None,
    events: Sequence[EventRecord],
    macro_lines: Sequence[str],
    *,
    strict_invalidation: bool = False,
) -> str:
    srtn, name = candidate
    sectors = ", ".join(fp.sectors) if fp and fp.sectors else "미분류"
    slice_parts = [f"종목: {name}({srtn}) · 섹터 {sectors}", _price_line(fp)]
    if spec.persona == Persona.CYCLE:
        slice_parts.append(_financial_lines(fp))
    if spec.persona == Persona.MACRO and macro_lines:
        slice_parts.append("거시 백드롭:\n" + "\n".join(f"  {m}" for m in macro_lines))
    slice_parts.append("담당 촉매(EventStore):\n" + _event_lines(events))
    grounding = "\n".join(slice_parts)
    strict = (
        "\n\n[재생성] 직전 출력의 invalidation이 비었거나 관측 불가했다. "
        "invalidation을 **관측 가능한 구체 조건**(가격/지표/이벤트)으로 반드시 채워라."
        if strict_invalidation else ""
    )
    return (
        f"너는 '{spec.persona.value}' 페르소나다. **아래 슬라이스만** 근거로 분석하라(외부 추론 금지).\n"
        f"담당 질문: {spec.question}\n{spec.lens}\n\n"
        f"## 입력 슬라이스\n{grounding}\n\n"
        "## 출력 (JSON만, ThesisRecord)\n"
        '{"direction": <long|short|flat>, "thesis": "<핵심 논제, 형용사 절제>", '
        '"instrument_class": "<수단(보통 해당 종목)>", "trigger": "<진입 트리거(관측 조건)>", '
        '"invalidation": "<무효화 조건 — 관측 가능, 필수>", "horizon_days": <거래일 정수 3~15>, '
        '"confidence": <0~1>, "evidence": ["<위 촉매 id 또는 데이터 근거>"]}\n\n'
        "## 규칙\n"
        "- invalidation은 **필수**이며 관측 가능해야 한다(없으면 논제 무효).\n"
        "- 슬라이스에 없는 사실 금지. 핵심 데이터 미수집이면 confidence를 낮추고 thesis에 '보류' 명시.\n"
        "- 방향이 불확실하면 flat." + strict
    )


def _clamp_horizon(v: object, cfg: R3Config) -> int:
    if isinstance(v, int) and 1 <= v <= cfg.horizon_max:
        return v
    if isinstance(v, float) and 1 <= v <= cfg.horizon_max:
        return int(v)
    return cfg.horizon_default


def _safe_conf(v: object, cfg: R3Config) -> float:
    if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0:
        return float(v)
    return cfg.confidence_default


def _to_thesis(
    data: object, spec: PersonaSpec, candidate: tuple[str, str], now: datetime, source: str, cfg: R3Config
) -> ThesisRecord:
    if not isinstance(data, dict):
        raise ValueError("응답이 객체 아님")
    inval = str(data.get("invalidation") or "").strip()
    if not inval:
        raise ValueError("invalidation 누락")
    srtn, name = candidate
    raw_ev = data.get("evidence")
    ev_list = raw_ev if isinstance(raw_ev, list) else []
    direction_raw = str(data.get("direction") or "flat")
    try:
        direction = Direction(direction_raw)
    except ValueError:
        direction = Direction.FLAT
    return ThesisRecord(
        id=f"thesis.{now:%Y%m%d}.{srtn}.{spec.persona.value}",
        as_of=now, fetched_at=now, source=source, persona=spec.persona,
        thesis=str(data.get("thesis") or "").strip() or "(논제 미명시)",
        direction=direction,
        instrument_class=str(data.get("instrument_class") or "").strip() or name,
        trigger=str(data.get("trigger") or "").strip() or "(트리거 미명시)",
        invalidation=inval,
        horizon_days=_clamp_horizon(data.get("horizon_days"), cfg),
        confidence=_safe_conf(data.get("confidence"), cfg),
        evidence=[str(e).strip() for e in ev_list if str(e).strip()],
    )


def run_r3(
    client: LLMClient,
    candidate: tuple[str, str],
    factpack: FactPack | None,
    events: Sequence[EventRecord],
    *,
    macro_lines: Sequence[str] = (),
    now: datetime | None = None,
    config: R3Config | None = None,
    source: str = "r3:claude",
) -> R3Result:
    """후보 1종목 → 3 페르소나 입력격리 분석 → ThesisRecord. invalidation 누락은 1회 재생성 후 폐기."""
    resolved_now = now if now is not None else now_kst()
    cfg = config if config is not None else R3Config()
    theses: list[ThesisRecord] = []
    rejected = 0
    errors: list[str] = []
    for spec in PERSONAS:
        p_events = events_for_persona(spec.persona, events)
        thesis: ThesisRecord | None = None
        for strict in (False, True):  # 1회 재생성(strict invalidation)
            prompt = build_prompt(spec, candidate, factpack, p_events, macro_lines, strict_invalidation=strict)
            try:
                data = complete_json(client, prompt)
            except LLMError as e:
                errors.append(f"{spec.persona.value}: {e}")
                break
            try:
                thesis = _to_thesis(data, spec, candidate, resolved_now, source, cfg)
                break
            except (ValidationError, ValueError, KeyError, TypeError):
                thesis = None  # 재생성 시도
        if thesis is not None:
            theses.append(thesis)
        elif not any(spec.persona.value in e for e in errors):
            rejected += 1
    return R3Result(theses=theses, rejected=rejected, persona_errors=errors)


__all__ = [
    "PERSONAS",
    "PersonaSpec",
    "R3Config",
    "R3Result",
    "build_prompt",
    "events_for_persona",
    "run_r3",
]
