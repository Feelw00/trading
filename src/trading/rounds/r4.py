"""R4 — 촉매 적대검증 (선별·perspective-diverse, claude -p / PROPOSALS P-4 §4).

R2가 산출한 EventRecord 중 **고강도·single_stock만 선별**해 적대적으로 검증한다(전수 검증 금지).
각 이벤트를 3개 렌즈(강도/종목연결/시점정합)로 **죽이려 시도** → 다수 생존이면 ``confirmed``.
저강도·broad는 검증 없이 통과(R3가 가중). 검증 결과는 ``Verification`` 으로 이벤트에 부착(새 version).

설계서 §3 R4("논제 살해 시도")의 뉴스-촉매 인스턴스. LLM은 ``LLMClient`` 주입 → 테스트는 프로세스 없이.
적대 기본값은 **회의적**: 근거 약하거나 호출 실패면 survived=False(refute).
"""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from trading.contracts.event import EventRecord, LensVerdict, Scope, Verification
from trading.contracts.news import NewsItem
from trading.llm import LLMClient, LLMError, complete_json

LENSES: tuple[str, ...] = ("strength", "linkage", "timing")

_LENS_TASK: dict[str, str] = {
    "strength": "이 촉매의 시장 임팩트(catalyst_strength)가 과대평가됐는지 공격하라. "
    "이미 가격에 반영됐거나 영향이 미미·일회성이면 refute.",
    "linkage": "affected 종목 연결(relevance)이 실제 근거가 있는지 공격하라. "
    "근거 기사가 해당 종목을 직접 다루지 않거나 연결이 비약이면 refute.",
    "timing": "촉매의 시점 정합성을 공격하라. 이미 지난 일이거나 신선도가 떨어져 지금은 작동 안 하면 refute.",
}


@dataclass(frozen=True)
class R4Config:
    """선별 임계 — 실데이터 분포 기반(2026-06-10, 6/9자 뉴스 395건→이벤트 131건 캘리브레이션).

    R2 산출 강도는 0.2~0.5에 질량 집중: single_stock p80=0.40·최대 0.50(구 기본 0.5는
    선별률 4%로 사실상 비활성), scope 무관 0.6 이상은 전체의 6%. 재캘리브레이션은
    같은 분포 분석으로 — .env(R4_*)로 코드 수정 없이 오버라이드 가능.
    """

    strength_threshold: float = 0.4   # single_stock은 이 이상이면 검증 (p80)
    high_strength: float = 0.6        # scope 무관 이 이상이면 검증 (상위 ~6%)
    min_survived: int = 2             # 3렌즈 중 생존 최소(다수결) → confirmed
    max_events: int = 20              # 비용 가드(선별 상한)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "R4Config":
        """.env 주입(하드코딩 금지 규약): R4_STRENGTH_THRESHOLD / R4_HIGH_STRENGTH /
        R4_MIN_SURVIVED / R4_MAX_EVENTS. 미설정 키는 코드 기본값."""
        e = env if env is not None else os.environ
        base = cls()
        return cls(
            strength_threshold=float(e.get("R4_STRENGTH_THRESHOLD") or base.strength_threshold),
            high_strength=float(e.get("R4_HIGH_STRENGTH") or base.high_strength),
            min_survived=int(e.get("R4_MIN_SURVIVED") or base.min_survived),
            max_events=int(e.get("R4_MAX_EVENTS") or base.max_events),
        )


@dataclass(frozen=True)
class R4Result:
    verified: list[EventRecord]       # verification 부착된 새 version 이벤트
    selected: int
    confirmed: int                    # 생존(confirmed=True) 수


def select_events(events: Sequence[EventRecord], config: R4Config) -> list[EventRecord]:
    """검증 대상 선별 — 미검증 + (single_stock·고강도 OR 초고강도). 강도 내림차순·상한."""
    sel = [
        e
        for e in events
        if e.verification is None
        and e.catalyst_strength is not None
        and (
            (e.scope == Scope.SINGLE_STOCK and e.catalyst_strength >= config.strength_threshold)
            or e.catalyst_strength >= config.high_strength
        )
    ]
    sel.sort(key=lambda e: e.catalyst_strength or 0.0, reverse=True)
    return sel[: config.max_events]


def build_lens_prompt(event: EventRecord, lens: str, evidence: Sequence[NewsItem]) -> str:
    arts = "\n".join(
        f"[{n.id}] ({n.publisher or '발행처미상'}) {n.title}" + (f" — {n.snippet[:120]}" if n.snippet else "")
        for n in evidence
    ) or "  (근거 기사 없음 — 그 자체가 약점)"
    aff = ", ".join(f"{a.srtn_cd}({a.relevance})" for a in event.affected) or "(없음)"
    return (
        "너는 적대적 검증자다. 아래 촉매 가설을 **죽이려 시도**하라(살릴 이유가 아니라 죽일 이유를 찾아라).\n\n"
        f"## 촉매\n타입: {event.catalyst_type.value if event.catalyst_type else '미상'} / "
        f"scope: {event.scope.value if event.scope else '미상'} / "
        f"strength: {event.catalyst_strength}\n요약: {event.summary_1line}\naffected: {aff}\n\n"
        f"## 근거 기사\n{arts}\n\n"
        f"## 공격 관점\n{_LENS_TASK.get(lens, lens)}\n\n"
        '## 출력 (JSON만)\n{"survived": <true=공격 실패(촉매 유효) / false=refute(촉매 기각)>, '
        '"reason": "<근거 한 문장>"}\n'
        "기본값은 회의적: 근거가 약하거나 불확실하면 survived=false."
    )


def verify_lens(client: LLMClient, event: EventRecord, lens: str, evidence: Sequence[NewsItem]) -> LensVerdict:
    """렌즈 1개 적대검증 — 호출/파싱 실패는 회의적 기본(survived=False)."""
    try:
        data = complete_json(client, build_lens_prompt(event, lens, evidence))
    except LLMError as e:
        return LensVerdict(lens=lens, survived=False, reason=f"검증 호출 실패: {str(e)[:80]}")
    survived = bool(data.get("survived")) if isinstance(data, dict) else False
    reason = (str(data.get("reason")).strip() if isinstance(data, dict) and data.get("reason") else "") or "(사유 없음)"
    return LensVerdict(lens=lens, survived=survived, reason=reason)


def verify_event(
    client: LLMClient,
    event: EventRecord,
    evidence: Sequence[NewsItem],
    config: R4Config,
    *,
    source: str = "r4:claude",
) -> Verification:
    """3렌즈 perspective-diverse 검증 → 다수결 confirmed."""
    verdicts = [verify_lens(client, event, lens, evidence) for lens in LENSES]
    survived = sum(1 for v in verdicts if v.survived)
    notes = [f"{survived}/{len(verdicts)} 렌즈 생존(min={config.min_survived})"]
    return Verification(
        verified_by=source,
        confirmed=survived >= config.min_survived,
        lens_verdicts=verdicts,
        notes=notes,
    )


def run_r4(
    client: LLMClient,
    events: Sequence[EventRecord],
    evidence_by_id: Mapping[str, NewsItem],
    *,
    config: R4Config | None = None,
    source: str = "r4:claude",
) -> R4Result:
    """선별 → 이벤트별 3렌즈 검증 → verification 부착한 새 version 이벤트 반환."""
    cfg = config if config is not None else R4Config()
    selected = select_events(events, cfg)
    verified: list[EventRecord] = []
    for e in selected:
        evidence = [evidence_by_id[i] for i in e.evidence if i in evidence_by_id]
        verification = verify_event(client, e, evidence, cfg, source=source)
        verified.append(e.model_copy(update={"verification": verification}))
    confirmed = sum(1 for e in verified if e.verification is not None and e.verification.confirmed)
    return R4Result(verified=verified, selected=len(selected), confirmed=confirmed)


__all__ = [
    "LENSES",
    "R4Config",
    "R4Result",
    "build_lens_prompt",
    "run_r4",
    "select_events",
    "verify_event",
    "verify_lens",
]
