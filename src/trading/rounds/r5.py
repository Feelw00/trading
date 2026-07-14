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
    EntryBand,
    ExitLevel,
    MarketState,
    OrderDraft,
    OrderType,
    Side,
    Stop,
    StopType,
    Tranche,
)
from trading.executor import derive_entry_band
from trading.contracts.playbook import FLOW_VARIABLES, Playbook
from trading.contracts.scenario import ScenarioAxis
from trading.flowsnap import OBSERVABLE_FLOW_DESC, OBSERVABLE_FLOW_VARS
from trading.contracts.thesis import Direction, ThesisRecord
from trading.collectors.base import now_kst
from trading.llm import LLMClient, LLMError, complete_json

TOTAL_SIZE_CAP = "0.5 * normal_unit"   # 설계서 §4 고정값
# 확인 트랜치 기본 조건은 폐지(운영자 2026-07-14: 조건은 분석이 정한다 — 고정 주입 금지).
# R5가 confirmation_condition 을 내지 않으면 그 플레이북은 규율 위반으로 폐기된다.


@dataclass(frozen=True)
class R5Config:
    max_playbooks: int = 8            # P-11 Stage B: 자동 집행 체제라 조건부 대안 셋업 상비(5→8)
    time_stop_max_days: int = 15      # 스윙 상한(설계서 §3 R3: 3~15일)


@dataclass(frozen=True)
class R5Result:
    playbooks: list[Playbook]
    drafts: list[OrderDraft]
    scenario_tree: list[ScenarioAxis]  # 시나리오 합성(축 구조 — 저녁 보고 렌더 단위)
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
    intraday_lines: Sequence[str] = (),
) -> str:
    # 이진 reclaim은 신규 계획 메뉴에서 폐지(운영자 2026-07-14 밤: "전고점 기준이 너무 강해")
    # — 가격 회복은 등급형 recovery 임계로만. 기존 풀 초안의 평가는 계속된다(관측은 유지).
    menu = {k: v for k, v in OBSERVABLE_FLOW_DESC.items() if k != "prev_day_high_reclaim"}
    observable = ", ".join(sorted(menu))
    observable_desc = "\n".join(f"  - {k}: {menu[k]}" for k in sorted(menu))
    unobserved = ", ".join(sorted(FLOW_VARIABLES - OBSERVABLE_FLOW_VARS))
    macro = "\n".join(f"- {m}" for m in macro_lines) or "  (없음)"
    intraday = "\n".join(f"- {m}" for m in intraday_lines)
    return (
        "너는 스윙 트레이딩 시스템의 야간 합성 라운드(R5)다. "
        "아래 생존 논제들을 시나리오 트리로 합성하고, "
        "**사전 승인 플레이북**과 주문 초안 제안을 JSON으로만 출력한다.\n\n"
        f"## 논제 (R3 산출)\n{_thesis_lines(theses)}\n\n"
        f"## 촉매 이벤트 (R4 검증 상태 포함)\n{_event_lines(events)}\n\n"
        f"## 가격 컨텍스트 (EOD, 결정론 산출)\n{_price_lines(packs)}\n\n"
        + (f"## 당일 가격 (KIS 실측 — 회복 임계 산정용)\n{intraday}\n\n" if intraday else "")
        + f"## 거시 백드롭\n{macro}\n\n"
        "## 출력 스키마 (JSON, 다른 텍스트 금지)\n"
        "{\n"
        '  "scenario_tree": [{"title": "<축 제목 — 테마/리스크 축 하나>",\n'
        '                     "lines": ["<분기·조건·리스크 1줄(예: 분기 A-1: …). 1줄 1항목, 통문단 금지>"]}],\n'
        '  "checklist": ["<익일 관측 항목>"],\n'
        '  "playbooks": [{\n'
        '    "thesis_ref": "<위 논제 id>",\n'
        '    "srtn_cd": "<6자리>", "side": "buy|sell",\n'
        f'    "arm_conditions": {{"<흐름변수>": "<조건식>"}},  // 현재 관측 가능 변수만: {observable}\n'
        '    "abort_conditions": {"<흐름변수>": "<조건식>"},\n'
        '    "stop_level": <가격 손절 레벨(숫자) — 심리적 합의 레벨(라운드 넘버·전저점·전고점)만>,\n'
        '    "soft_stop": {"level": <경고 레벨 — stop_level보다 위>, "pct": <축소 비중%, 통상 50>},  // 선택 — 선제 감축\n'
        '    "targets": [{"level": <익절1 — 부분 실현>, "pct": 50}, {"level": <최종 타깃 — 잔량 전량>, "pct": 50}],  // 기본 2단 사다리, 오름차순·합=100\n'
        '    "confirmation_condition": "<관측 가능 흐름변수 키 1개만 — 조건식·연산자 금지. 필수(기본값 없음)>",\n'
        '    "max_entries": <1|2 — 재진입 정책: 1=1회만(기본), 2=청산(익절·본전) 후 1회 재진입 허용>,\n'
        '    "entry_band": {"low": <매수 유효 하한>, "high": <상한>},  // 선택 — 코드 산출 밴드를 좁힐 때만\n'
        '    "time_stop_days": <거래일 단위>,\n'
        '    "summary": "<저녁 결재 보고용 1줄>"\n'
        "  }]\n"
        "}\n\n"
        "## 절대 규칙\n"
        "- scenario_tree 는 축(title)별로 나누고 lines 한 항목엔 분기 하나만(사실·조건문만) — "
        "통문단 금지, 한 줄 120자 이내.\n"
        f"- arm/abort 조건은 **현재 관측 가능 흐름변수만**(범위 준수 — 임계값을 범위 밖으로 "
        f"지어내지 마라, 예: orderbook_imbalance>1.15는 불가):\n{observable_desc}\n"
        f"  미관측 변수({unobserved})는 NXT/소스 부재로 매일 '관측치 없음'이라 영영 미충족이 된다 — "
        "절대 쓰지 마라(쓰면 그 플레이북은 발동 불가). 가치·내러티브 변수도 금지.\n"
        "- confirmation_condition 은 관측 가능 흐름변수 **키 1개만**(조건식·==true 등 붙이지 마라). "
        "**필수 — 기본값 주입은 없다. 논제가 요구하는 확인 신호를 분석으로 골라라.**\n"
        "- **조건은 분석에서 도출됐을 때만 건다 — 습관적·기본 조건 금지(운영자 2026-07-14).** "
        "가격 회복 확인이 논제에 필요하면 prev_day_high_recovery 로 등급을 명시하라"
        "(완전 회복 >=1.0, 일부 회복은 근거 가격 컨텍스트로 정당화되는 비율만). "
        "**트리거가 익절 타깃과 겹치는 설계 금지**: arm 충족 시점 가격에서 최종 타깃까지의 보상이 "
        "손절까지의 위험보다 작으면(잔여 R:R<1) 그 플레이북은 내지 마라(집행 가드가 어차피 차단한다).\n"
        "- 조건식 문법: 연속 변수(갭·거래량·체결강도·호가·prev_day_high_recovery)는 "
        "`<op><숫자>`(<,<=,>,>=,==), "
        "boolean 변수(volume_climax·new_low_renewal_fail·sector_ignition)는 "
        "`==true`/`==false`로. 시각·문자열 등 그 외 형식 금지(선택기가 평가 불가 → 미충족 처리).\n"
        "- **prev_day_high_reclaim(이진 완전 회복)은 폐지 — 쓰면 그 플레이북은 폐기된다"
        "(운영자 2026-07-14).** 가격 회복 확인은 prev_day_high_recovery 임계로만: "
        "**'## 당일 가격'의 고가 이격을 보고 도달 가능한 임계를 골라라.** 종가가 당일 고가 대비 "
        "-3% 이내면 완전 회복(>=1.0)도 현실적이지만, 이격이 크면 완전 회복은 사실상 미발동 계획이다 — "
        "일부 회복(예: >=0.95~0.98, 근거 레벨로 정당화) 또는 다른 확인 신호를 써라.\n"
        "- stop_level 은 '논리적 지지선'이 아니라 심리적 합의 레벨(라운드 넘버, 전저점·전고점)로.\n"
        "  근거 가격(위 컨텍스트)에 없는 레벨을 지어내지 마라 — 불확실하면 그 플레이북을 내지 마라.\n"
        "- targets(익절)·soft_stop(경고 축소)도 같은 규칙: **근거 가격 컨텍스트의 저항·전고·라운드 넘버만**. "
        "soft_stop.level 은 반드시 stop_level 위(경고→축소, 하드→전량).\n"
        "- **targets는 2단 사다리가 기본**(운영자 §계단 청산): 익절1에서 부분 실현(통상 50%) → "
        "코드가 잔량 손절을 본전으로 자동 상향 → 최종 타깃에서 잔량 전량. 레벨 오름차순·pct 합=100, "
        "**마지막 레벨=전량 청산 라인**(pct는 잔량 전부로 집행된다). 다음 저항이 구조적으로 없어 "
        "사다리를 못 세울 때만 단일 타깃 허용. 손절·익절 자체가 불확실하면 생략하라(코드가 보수 기본값).\n"
        "- max_entries=2(재진입 1회)는 재진입이 셋업 논리에 맞을 때만(예: 눌림 재매집형). "
        "하드 스탑 청산 후 재진입은 코드가 금지하고, 2회차 사이즈는 자동 절반이다. 모르면 1.\n"
        "- entry_band 는 코드가 손절·익절 레벨로 산출하는 매수 유효 범위와 **교집합**으로 적용된다 "
        "— 넓힐 수 없고, 좁힐 의도가 있을 때만 지정하라. 모르면 생략(코드 밴드가 기본).\n"
        "- 역추세 플레이북은 '과도하다'는 논리가 아니라 소진의 물리 신호(volume_climax, "
        "new_low_renewal_fail) 확인 조건으로만.\n"
        "- direction=flat 논제, invalidation 이 관측 불가한 논제로는 플레이북을 만들지 마라.\n"
        f"- 플레이북 최대 {config.max_playbooks}개. **조건이 안 서면 빈 배열이 정답이다 — "
        "대부분의 날은 비거래가 정상.**\n"
        "- 집행은 자동(감시기가 조건 충족 순간 매수)이므로, 확신 높은 주력 셋업 외에 "
        "**조건이 까다로운 대안 셋업**(발동 확률은 낮지만 발동하면 우위인 것 — 예: 눌림+체결강도 회복, "
        "섹터 점화 동반 돌파)을 함께 깔아 두는 것이 좋다. 단 조건 근거는 동일한 엄격함으로.\n"
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

    # 계단식 청산(EXEC-2, 선택) — 형식 오류는 해당 필드만 버림(플레이북 폐기 아님: 코드 기본값 경로)
    targets: list[ExitLevel] = []
    raw_targets = pb.get("targets")
    if isinstance(raw_targets, list):
        try:
            targets = [
                ExitLevel(level=float(t["level"]), pct=int(t["pct"]))
                for t in raw_targets
                if isinstance(t, dict) and isinstance(t.get("level"), (int, float))
            ]
        except (KeyError, TypeError, ValueError):
            targets = []
    soft_stop: ExitLevel | None = None
    raw_soft = pb.get("soft_stop")
    if isinstance(raw_soft, dict) and isinstance(raw_soft.get("level"), (int, float)):
        try:
            cand = ExitLevel(level=float(raw_soft["level"]), pct=int(raw_soft.get("pct") or 50))
            if cand.level > float(stop_level) and cand.pct < 100:
                soft_stop = cand
        except (TypeError, ValueError):
            soft_stop = None

    raw_ts = pb.get("time_stop_days")
    time_stop = (
        int(raw_ts)
        if isinstance(raw_ts, (int, float)) and 1 <= int(raw_ts) <= config.time_stop_max_days
        else thesis.horizon_days  # grounded 폴백(논제 시계)
    )

    confirmation_raw = str(pb.get("confirmation_condition") or "").strip()
    if not confirmation_raw:
        raise ValueError("confirmation_condition 미제공 — 기본 조건 주입 금지(운영자 2026-07-14)")
    # R5가 키에 조건식(==true 등)을 붙여 와도 흐름변수 키만 사용(코드 강제 — confirmation은 키 1개)
    key_match = re.match(r"[a-z_]+", confirmation_raw)
    confirmation = key_match.group(0) if key_match else confirmation_raw
    if confirmation not in FLOW_VARIABLES:
        raise ValueError(f"확인 트랜치 조건이 흐름 변수가 아님: {confirmation_raw!r}")

    arm = pb.get("arm_conditions")
    abort = pb.get("abort_conditions")
    arm_d = {str(k): str(v) for k, v in arm.items()} if isinstance(arm, dict) else {}
    abort_d = {str(k): str(v) for k, v in abort.items()} if isinstance(abort, dict) else {}
    # 이진 완전 회복 폐지(운영자 2026-07-14 밤) — 등급형 recovery 임계로만(도달 가능성 정당화 강제)
    if "prev_day_high_reclaim" in arm_d or "prev_day_high_reclaim" in abort_d:
        raise ValueError(
            "prev_day_high_reclaim 폐지 — prev_day_high_recovery 임계로 표현하라(운영자 2026-07-14)"
        )

    # 재진입 정책(EXEC-8) — R5 명시, 1|2 외 값은 보수 기본(1)
    raw_me = pb.get("max_entries")
    max_entries = int(raw_me) if isinstance(raw_me, (int, float)) and int(raw_me) in (1, 2) else 1

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
        targets=sorted(targets, key=lambda t: t.level),  # 계약이 순증가·합≤100 재검증
        soft_stop=soft_stop,
        max_entries=max_entries,
    )
    # 진입 밴드 조임(EXEC-8) — R5는 코드 산출 밴드를 **좁힐 때만** 지정 가능(확장=폐기)
    raw_band = pb.get("entry_band")
    if isinstance(raw_band, dict):
        try:
            r5_low, r5_high = float(raw_band["low"]), float(raw_band["high"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("entry_band 형식 오류(low/high 숫자 필수) — 폐기") from None
        code_band = derive_entry_band(draft)
        if code_band is None:
            raise ValueError("entry_band 지정 불가(가격 컨텍스트 부족) — 폐기")
        c_low, c_high = code_band
        # 조임만 유효 — 코드 밴드와 **교집합**(확장 시도는 절삭: R5는 코드 밴드 값을 모른다 —
        # 21:05 2차 산출에서 폐기 3건 발생해 절삭으로 완화, 운영자 재산출 지시 중 교정).
        # 교집합이 비면 계획이 코드 밴드와 구조적으로 모순 — 그것만 폐기.
        n_low, n_high = max(r5_low, c_low), min(r5_high, c_high)
        if n_high <= n_low:
            raise ValueError(
                f"entry_band 교집합 공백(코드 밴드 [{c_low:,.0f}, {c_high:,.0f}]와 모순) — 폐기"
            )
        draft = draft.model_copy(update={"entry_band": EntryBand(low=n_low, high=n_high)})
    playbook = Playbook(
        id=f"pb.{day}.{srtn}.{side.value}",
        as_of=now, fetched_at=now, source=source,
        thesis_ref=thesis_ref,
        arm_conditions=arm_d,                          # 화이트리스트는 계약이 검증
        abort_conditions=abort_d,
        order_draft_ref=draft.id,
        summary=str(pb.get("summary") or "").strip()[:120],  # 저녁 결재 근거 1줄
        # default=inactive — 발동 여부는 아침 R5.5(코드)가 결정
    )
    return playbook, draft


def _parse_scenario(raw: object) -> list[ScenarioAxis]:
    """LLM scenario_tree → ScenarioAxis 목록. 문자열(스키마 불복종)은 줄 단위 보존."""
    if isinstance(raw, str):
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return [ScenarioAxis(title="", lines=lines)] if lines else []
    if not isinstance(raw, list):
        return []
    out: list[ScenarioAxis] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("axis") or "").strip()
        raw_lines = item.get("lines")
        lines = (
            [str(ln).strip() for ln in raw_lines if str(ln).strip()]
            if isinstance(raw_lines, list)
            else []
        )
        if title or lines:
            out.append(ScenarioAxis(title=title, lines=lines))
    return out


def run_r5(
    client: LLMClient,
    theses: Sequence[ThesisRecord],
    events: Sequence[EventRecord],
    packs: Sequence[FactPack],
    *,
    macro_lines: Sequence[str] = (),
    intraday_lines: Sequence[str] = (),
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
            playbooks=[], drafts=[],
            scenario_tree=[ScenarioAxis(title="(논제 없음 — 비거래)")],
            checklist=[], rejected=0,
        )
    try:
        data = complete_json(
            client,
            build_prompt(actionable, events, packs, macro_lines, cfg, intraday_lines),
        )
    except LLMError as e:
        return R5Result(
            playbooks=[], drafts=[], scenario_tree=[], checklist=[], rejected=0,
            error=str(e),
        )
    if not isinstance(data, dict):
        return R5Result(
            playbooks=[], drafts=[], scenario_tree=[], checklist=[], rejected=0,
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
        scenario_tree=_parse_scenario(data.get("scenario_tree")),
        checklist=checklist, rejected=rejected, rejected_reasons=reasons,
    )


__all__ = ["R5Config", "R5Result", "TOTAL_SIZE_CAP", "build_prompt", "run_r5"]
