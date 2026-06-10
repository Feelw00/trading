"""R5 — 합성·플레이북·주문 초안 (claude -p, 20:30 고정 — 장중 실행 금지. 설계서 §3 R5·§6).

역할 분리(설계서): 밤의 R5는 **내러티브 언어**로 "어느 종목·어느 방향 플레이북을 승인할지"만
결정한다. 아침의 발동 판정(R5.5)은 흐름 변수 화이트리스트로만 — 그래서 LLM이 산출한
arm/abort 조건은 ``Playbook`` 계약(화이트리스트 검증)이 로드 시점에 거른다.

**LLM 출력 불신 — 규율 파라미터는 코드가 강제(M3 지시):**
- 3트랜치는 LLM 제안을 무시하고 코드가 주입: impatience_fee 20%(limit) / flush 50%(limit) /
  confirmation 30%(가격 상승으로만 충족되는 조건, §6 — 하락 중 대량 매수 구조 차단).
- ``total_size_cap`` = "0.5 * normal_unit" 고정(§4).
- 손절 2종 모두 필수(§6): 가격 스탑 레벨은 **LLM이 제시한 심리적 합의 레벨만** 사용 —
  코드가 가격을 지어내지 않는다(환각 가드). 레벨 미제공이면 그 플레이북은 **폐기**.
  시간 손절은 LLM 값 또는 논제 horizon_days(grounded 폴백).
- ``created_when_market=closed``·시장가 부재는 계약이 거부.

대부분의 날의 정상 출력은 **플레이북 0개**다(설계서 §3 R5.5: 갭 전략의 엣지는
분포가 쏠린 날만 여는 데서 나온다) — 프롬프트가 이를 명시한다.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from trading.contracts.event import EventRecord
from trading.contracts.factpack import FactPack
from trading.contracts.order import (
    MarketState,
    OrderDraft,
    OrderType,
    Side,
    Stop,
    StopType,
    Tranche,
)
from trading.contracts.playbook import FLOW_VARIABLES, Playbook
from trading.contracts.thesis import Direction, ThesisRecord
from trading.collectors.base import now_kst
from trading.llm import LLMClient, LLMError, complete_json

TOTAL_SIZE_CAP = "0.5 * normal_unit"   # 설계서 §4 고정값
_CONFIRMATION_DEFAULT = "prev_day_high_reclaim"  # §6 확인 트랜치 기본 조건(가격 상승으로만 충족)


@dataclass(frozen=True)
class R5Config:
    max_playbooks: int = 5            # 비용·집중 가드(승인 요청은 저녁 보고 5분 분량)
    time_stop_max_days: int = 15      # 스윙 상한(설계서 §3 R3: 3~15일)


@dataclass(frozen=True)
class R5Result:
    playbooks: list[Playbook]
    drafts: list[OrderDraft]
    scenario_tree: str                # 시나리오 합성 요약(저녁 보고용)
    checklist: list[str]              # 익일 관측 체크리스트
    rejected: int                     # 규율·스키마 위반으로 폐기된 제안 수
    rejected_reasons: list[str] = field(default_factory=list)
    error: str | None = None          # LLM 호출 실패(§9: 초안 갱신 불가 알림은 호출측)


def _thesis_lines(theses: Sequence[ThesisRecord]) -> str:
    return "\n".join(
        f"[{t.id}] ({t.persona.value}/{t.direction.value}/h{t.horizon_days}d/conf{t.confidence}) "
        f"{t.thesis} | trigger: {t.trigger} | invalidation: {t.invalidation}"
        for t in theses
    ) or "  (없음)"


def _event_lines(events: Sequence[EventRecord]) -> str:
    out = []
    for e in events[:20]:
        ver = ""
        if e.verification is not None:
            ver = " [R4 생존]" if e.verification.confirmed else " [R4 기각]"
        out.append(f"- ({e.catalyst_strength}) {e.summary_1line}{ver}")
    return "\n".join(out) or "  (없음)"


def _price_lines(packs: Sequence[FactPack]) -> str:
    return "\n".join(
        f"- {p.srtn_cd} {p.name}: 종가 {p.price.close} (as_of {p.price.as_of}) "
        f"거래대금배 {p.price.tr_value_surge:.1f} 신고가근접 {p.price.high_252_proximity:.3f}"
        for p in packs
    ) or "  (없음)"


def build_prompt(
    theses: Sequence[ThesisRecord],
    events: Sequence[EventRecord],
    packs: Sequence[FactPack],
    macro_lines: Sequence[str],
    config: R5Config,
) -> str:
    flow_vars = ", ".join(sorted(FLOW_VARIABLES))
    macro = "\n".join(f"- {m}" for m in macro_lines) or "  (없음)"
    return (
        "너는 스윙 트레이딩 시스템의 야간 합성 라운드(R5)다. "
        "아래 생존 논제들을 시나리오 트리로 합성하고, "
        "**사전 승인 플레이북**과 주문 초안 제안을 JSON으로만 출력한다.\n\n"
        f"## 논제 (R3 산출)\n{_thesis_lines(theses)}\n\n"
        f"## 촉매 이벤트 (R4 검증 상태 포함)\n{_event_lines(events)}\n\n"
        f"## 가격 컨텍스트 (EOD, 결정론 산출)\n{_price_lines(packs)}\n\n"
        f"## 거시 백드롭\n{macro}\n\n"
        "## 출력 스키마 (JSON, 다른 텍스트 금지)\n"
        "{\n"
        '  "scenario_tree": "<시나리오 분기 요약, 사실·조건문만>",\n'
        '  "checklist": ["<익일 관측 항목>"],\n'
        '  "playbooks": [{\n'
        '    "thesis_ref": "<위 논제 id>",\n'
        '    "srtn_cd": "<6자리>", "side": "buy|sell",\n'
        f'    "arm_conditions": {{"<흐름변수>": "<조건식>"}},  // 허용 변수: {flow_vars}\n'
        '    "abort_conditions": {"<흐름변수>": "<조건식>"},\n'
        '    "stop_level": <가격 손절 레벨(숫자) — 심리적 합의 레벨(라운드 넘버·전저점·전고점)만>,\n'
        '    "confirmation_condition": "<확인 트랜치 조건(가격 상승으로만 충족) — 흐름변수 키>",\n'
        '    "time_stop_days": <거래일 단위>,\n'
        '    "summary": "<저녁 결재 보고용 1줄>"\n'
        "  }]\n"
        "}\n\n"
        "## 절대 규칙\n"
        "- arm/abort 조건 키는 위 흐름 변수만. 가치·내러티브 변수(밸류에이션·컨센서스·목표가) 금지.\n"
        "- stop_level 은 '논리적 지지선'이 아니라 심리적 합의 레벨(라운드 넘버, 전저점·전고점)로.\n"
        "  근거 가격(위 컨텍스트)에 없는 레벨을 지어내지 마라 — 불확실하면 그 플레이북을 내지 마라.\n"
        "- 역추세 플레이북은 '과도하다'는 논리가 아니라 소진의 물리 신호(volume_climax, "
        "new_low_renewal_fail) 확인 조건으로만.\n"
        "- direction=flat 논제, invalidation 이 관측 불가한 논제로는 플레이북을 만들지 마라.\n"
        f"- 플레이북 최대 {config.max_playbooks}개. **조건이 안 서면 빈 배열이 정답이다 — "
        "대부분의 날은 비거래가 정상.**\n"
        "- 트랜치 비율·총량 상한은 출력하지 마라(코드가 강제 주입한다)."
    )


_SRTN = re.compile(r"^\d{6}$")


def _build_tranches(confirmation_condition: str) -> list[Tranche]:
    """§6 3트랜치 고정 구조 — LLM 제안 무시, 코드 주입."""
    return [
        Tranche(label="impatience_fee", pct_of_plan=20, order_type=OrderType.LIMIT),
        Tranche(label="flush", pct_of_plan=50, order_type=OrderType.LIMIT),
        Tranche(label="confirmation", pct_of_plan=30, condition=confirmation_condition),
    ]


def _to_records(
    pb: dict[str, object],
    *,
    theses_by_id: dict[str, ThesisRecord],
    now: datetime,
    source: str,
    config: R5Config,
) -> tuple[Playbook, OrderDraft]:
    """LLM 제안 1건 → (Playbook, OrderDraft). 규율 위반·근거 부재는 예외(호출측 폐기)."""
    thesis_ref = str(pb.get("thesis_ref") or "")
    thesis = theses_by_id.get(thesis_ref)
    if thesis is None:
        raise ValueError(f"thesis_ref 불명: {thesis_ref!r}")  # 논제에 없는 플레이북 금지
    if thesis.direction is Direction.FLAT:
        raise ValueError(f"flat 논제로 플레이북 생성 금지: {thesis_ref}")

    srtn = str(pb.get("srtn_cd") or "")
    if not _SRTN.match(srtn):
        raise ValueError(f"srtn_cd 형식 오류: {srtn!r}")

    side_raw = str(pb.get("side") or "")
    side = Side(side_raw)  # 잘못된 값은 ValueError
    # 방향-수단 정합(§3 R4 공격 벡터를 사전 차단): long→buy, short→sell
    if (thesis.direction is Direction.LONG) != (side is Side.BUY):
        raise ValueError(f"논제 방향({thesis.direction.value})과 side({side.value}) 불일치")

    stop_level = pb.get("stop_level")
    if not isinstance(stop_level, (int, float)):
        raise ValueError("stop_level 미제공 — 코드가 가격을 지어내지 않는다(폐기)")

    raw_ts = pb.get("time_stop_days")
    time_stop = (
        int(raw_ts)
        if isinstance(raw_ts, (int, float)) and 1 <= int(raw_ts) <= config.time_stop_max_days
        else thesis.horizon_days  # grounded 폴백(논제 시계)
    )

    confirmation = str(pb.get("confirmation_condition") or _CONFIRMATION_DEFAULT)
    if confirmation not in FLOW_VARIABLES:
        raise ValueError(f"확인 트랜치 조건이 흐름 변수가 아님: {confirmation!r}")

    arm = pb.get("arm_conditions")
    abort = pb.get("abort_conditions")
    arm_d = {str(k): str(v) for k, v in arm.items()} if isinstance(arm, dict) else {}
    abort_d = {str(k): str(v) for k, v in abort.items()} if isinstance(abort, dict) else {}

    day = f"{now:%Y%m%d}"
    draft = OrderDraft(
        id=f"order.{day}.{srtn}.{side.value}",
        as_of=now, fetched_at=now, source=source,
        symbol=srtn, side=side,
        tranches=_build_tranches(confirmation),       # 코드 강제(§6)
        total_size_cap=TOTAL_SIZE_CAP,                # 코드 강제(§4)
        stop=Stop(type=StopType.CONDITIONAL_ORDER_AT_BROKER, level=float(stop_level)),
        time_stop_days=time_stop,                     # 손절 2종 모두 충족(§6)
        created_when_market=MarketState.CLOSED,
    )
    playbook = Playbook(
        id=f"pb.{day}.{srtn}.{side.value}",
        as_of=now, fetched_at=now, source=source,
        thesis_ref=thesis_ref,
        arm_conditions=arm_d,                          # 화이트리스트는 계약이 검증
        abort_conditions=abort_d,
        order_draft_ref=draft.id,
        # default=inactive — 발동 여부는 아침 R5.5(코드)가 결정
    )
    return playbook, draft


def run_r5(
    client: LLMClient,
    theses: Sequence[ThesisRecord],
    events: Sequence[EventRecord],
    packs: Sequence[FactPack],
    *,
    macro_lines: Sequence[str] = (),
    now: datetime | None = None,
    config: R5Config | None = None,
    source: str = "r5:claude",
) -> R5Result:
    """논제 합성 → 플레이북·주문 초안. LLM 1회 호출, 제안별 규율 검증(위반=개별 폐기)."""
    resolved_now = now if now is not None else now_kst()
    cfg = config if config is not None else R5Config()
    actionable = [t for t in theses if t.direction is not Direction.FLAT]
    if not actionable:
        return R5Result(
            playbooks=[], drafts=[], scenario_tree="(논제 없음 — 비거래)",
            checklist=[], rejected=0,
        )
    try:
        data = complete_json(client, build_prompt(actionable, events, packs, macro_lines, cfg))
    except LLMError as e:
        return R5Result(
            playbooks=[], drafts=[], scenario_tree="", checklist=[], rejected=0,
            error=str(e),
        )
    if not isinstance(data, dict):
        return R5Result(
            playbooks=[], drafts=[], scenario_tree="", checklist=[], rejected=0,
            error="응답이 객체 아님",
        )

    theses_by_id = {t.id: t for t in actionable}
    raw_pbs = data.get("playbooks")
    proposals = raw_pbs if isinstance(raw_pbs, list) else []
    playbooks: list[Playbook] = []
    drafts: list[OrderDraft] = []
    rejected = 0
    reasons: list[str] = []
    for i, pb in enumerate(proposals[: cfg.max_playbooks]):
        if not isinstance(pb, dict):
            rejected += 1
            reasons.append(f"#{i}: 제안이 객체 아님")
            continue
        try:
            playbook, draft = _to_records(
                pb, theses_by_id=theses_by_id, now=resolved_now, source=source, config=cfg
            )
        except (ValidationError, ValueError, KeyError, TypeError) as e:
            rejected += 1
            reasons.append(f"#{i}: {str(e)[:120]}")
            continue
        playbooks.append(playbook)
        drafts.append(draft)

    raw_cl = data.get("checklist")
    checklist = [str(c) for c in raw_cl if str(c).strip()] if isinstance(raw_cl, list) else []
    return R5Result(
        playbooks=playbooks, drafts=drafts,
        scenario_tree=str(data.get("scenario_tree") or "").strip(),
        checklist=checklist, rejected=rejected, rejected_reasons=reasons,
    )


__all__ = ["R5Config", "R5Result", "TOTAL_SIZE_CAP", "build_prompt", "run_r5"]
