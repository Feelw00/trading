"""R2 — 촉매 분류·스코어 (단일 LLM 호출·배치, OPEN_QUESTIONS NEWS-R2).

R1 게이트를 통과한 뉴스를 scope 레이어별 배치(L1 후보 / L2 섹터 / L3 거시)로 묶어
**배치당 1회** LLM 호출 → 기사를 이벤트로 클러스터링하고 ``EventRecord``(촉매필드 포함) 산출.
멀티에이전트 아님(검증·다양성은 R4). LLM은 ``LLMClient`` 인터페이스로 주입 → 테스트는 프로세스 없이.

**환각가드:** ``affected`` 는 제공된 후보 universe 안에서만, ``evidence`` 는 배치 기사 id에만 귀속.
스키마 위반 이벤트는 폐기 + 카운트(설계서 §9 "스키마 위반 시 폐기"). R2 산출은 R3 grounding으로만 흐른다.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from trading.collectors.base import KST, now_kst
from trading.contracts.event import AffectedStock, EventRecord, EventType, Scope
from trading.contracts.news import NewsItem
from trading.domains import CatalystType
from trading.gates.news import NewsVerdict
from trading.llm import LLMClient, LLMError, complete_json


@dataclass(frozen=True)
class R2Config:
    max_items_per_batch: int = 12   # 배치 컨텍스트·비용 상한
    max_batches: int = 0            # 0 = 무제한(슬롯당 콜 수는 배치 수)
    include_stale: bool = False     # 기본: fresh만(stale/undated/future 제외 — R5 하드게이트 결함)


@dataclass(frozen=True)
class R2Result:
    events: list[EventRecord]
    batches: int                    # 호출한 배치 수
    rejected: int                   # 스키마 위반으로 폐기한 이벤트 수
    batch_errors: list[str] = field(default_factory=list)   # LLM 호출 실패 배치
    rejected_reasons: list[str] = field(default_factory=list)  # 폐기 사유(관측성·프롬프트 튜닝)


@dataclass(frozen=True)
class BatchProgress:
    """배치 직후 호출되는 콜백에 전달 — 운영 가시성 + EventStore incremental append용."""
    index: int                       # 1-based
    total: int                       # 전체 배치 수
    key: str                         # 배치 키(primary entity)
    events: list[EventRecord]        # 이 배치에서 스키마 통과한 이벤트
    rejected: int                    # 이 배치에서 폐기된 raw 이벤트 수
    error: str | None                # LLM 호출 실패 메시지 (실패 시 events=[])


# coarse EventType(5종)은 촉매 어휘(11종)와 입도가 달라 모델이 미끄러진다 → catalyst_type에서 보정.
# EventType은 레거시 coarse 축이고 catalyst_type이 authoritative(R3/R7이 후자를 씀).
_CATALYST_TO_EVENT: dict[CatalystType, EventType] = {
    CatalystType.EARNINGS: EventType.EARNINGS,
    CatalystType.GUIDANCE: EventType.EARNINGS,
    CatalystType.POLICY_REGULATION: EventType.POLICY,
    CatalystType.LEGAL: EventType.POLICY,
    CatalystType.MACRO: EventType.GEOPOLITICS,
    CatalystType.FLOW_DEMAND: EventType.FLOW_ANOMALY,
    CatalystType.MA_RESTRUCTURE: EventType.CORP_ACTION,
    CatalystType.SUPPLY_CHAIN: EventType.CORP_ACTION,
    CatalystType.PRODUCT_TECH: EventType.CORP_ACTION,
    CatalystType.MANAGEMENT: EventType.CORP_ACTION,
    CatalystType.RUMOR_UNCONFIRMED: EventType.CORP_ACTION,
}


def _coerce_event_type(raw: object, ctype: CatalystType | None) -> EventType:
    """event_type 보정 — 유효하면 그대로, 아니면 catalyst_type 맵, 그도 없으면 CORP_ACTION."""
    if isinstance(raw, str):
        try:
            return EventType(raw)
        except ValueError:
            pass
    if ctype is not None:
        return _CATALYST_TO_EVENT.get(ctype, EventType.CORP_ACTION)
    return EventType.CORP_ACTION


def _safe_catalyst(raw: object) -> CatalystType | None:
    if isinstance(raw, str):
        try:
            return CatalystType(raw)
        except ValueError:
            return None
    return None


def _safe_scope(raw: object) -> Scope | None:
    if isinstance(raw, str):
        try:
            return Scope(raw)
        except ValueError:
            return None
    return None


def _safe_score(v: object) -> float | None:
    """0~1 스코어 — 숫자 아니거나 범위 밖이면 None(추측·클램프 안 함)."""
    if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0:
        return float(v)
    return None


_SRTN = re.compile(r"^\d{6}$")


def _primary_key(item: NewsItem) -> str | None:
    """배치 키 — 가장 구체적인 엔티티 1개(종목>섹터>테마). 미분류는 None(스킵)."""
    srtn = [e for e in item.entities if _SRTN.match(e)]
    if srtn:
        return srtn[0]
    for prefix in ("sector:", "theme:"):
        hit = [e for e in item.entities if e.startswith(prefix)]
        if hit:
            return hit[0]
    return None


_EPOCH = datetime.min.replace(tzinfo=KST)


def _pub_key(item: NewsItem) -> tuple[bool, datetime]:
    return (item.published_at is not None, item.published_at or _EPOCH)


def build_batches(items: Sequence[NewsItem], config: R2Config) -> dict[str, list[NewsItem]]:
    """primary key별 그룹 → 발행일 최신순 정렬 + 배치당 상한. 각 기사는 정확히 1배치."""
    groups: dict[str, list[NewsItem]] = {}
    for it in items:
        key = _primary_key(it)
        if key is not None:
            groups.setdefault(key, []).append(it)
    out: dict[str, list[NewsItem]] = {}
    for key, lst in groups.items():
        out[key] = sorted(lst, key=_pub_key, reverse=True)[: config.max_items_per_batch]
    if config.max_batches:
        out = dict(list(out.items())[: config.max_batches])
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "-", text).strip("-") or "x"


_CATALYST_TYPES = ", ".join(c.value for c in CatalystType)
_EVENT_TYPES = ", ".join(e.value for e in EventType)
_SCOPES = ", ".join(s.value for s in Scope)


def build_prompt(batch_key: str, items: Sequence[NewsItem], candidates: Sequence[tuple[str, str]]) -> str:
    """배치 프롬프트 — 기사 목록 + 후보 universe + JSON 스키마 + 환각가드.

    L1(종목) 배치의 키 종목은 universe에 항상 포함 — 당일 스크리너 후보가 아니어도
    배치 자체가 그 종목 쿼리로 수집된 기사라 귀속 대상이다(2026-06-10 R4 실검증 결함①).
    """
    arts = "\n".join(
        f"[{it.id}] ({(it.published_at.date().isoformat() if it.published_at else '날짜미상')}"
        f"·{it.publisher or '발행처미상'}) {it.title}"
        + (f" — {it.snippet[:120]}" if it.snippet else "")
        for it in items
    )
    rows = list(candidates)
    if _SRTN.match(batch_key) and batch_key not in {cd for cd, _ in rows}:
        rows.insert(0, (batch_key, "(배치 키 종목)"))
    universe = "\n".join(f"  {cd} {nm}" for cd, nm in rows) or "  (없음)"
    return (
        "너는 한국 증시 뉴스의 촉매를 분류·스코어링하는 결정론적 추출기다. "
        "아래 기사들을 **사건 단위로 클러스터링**(같은 촉매를 다룬 다수 기사는 1개 이벤트)하고, "
        "각 이벤트를 JSON으로만 출력한다.\n\n"
        f"## 배치 키\n{batch_key}\n\n"
        f"## 기사 ({len(items)}건)\n{arts}\n\n"
        f"## 영향종목 universe (affected 는 이 목록의 srtn_cd 에서만)\n{universe}\n\n"
        "## 출력 스키마 (JSON, 다른 텍스트 금지)\n"
        '{"events": [{\n'
        f'  "event_type": <{_EVENT_TYPES} 중 1>,\n'
        f'  "catalyst_type": <{_CATALYST_TYPES} 중 1>,\n'
        f'  "scope": <{_SCOPES} 중 1>,\n'
        '  "catalyst_strength": <0~1, 시장 임팩트(종목 독립)>,\n'
        '  "novelty": <0~1, 신규성(재탕은 낮게)>,\n'
        '  "summary_1line": "<사실만, 형용사·전망 금지>",\n'
        '  "affected": [{"srtn_cd": "<universe 내>", "relevance": <0~1>}],\n'
        '  "evidence": ["<위 기사 id만>"]\n'
        "}]}\n\n"
        "## 절대 규칙\n"
        "- 방향(호재/악재)·목표가·확신은 출력하지 마라(그건 다음 단계 몫).\n"
        "- affected 는 universe srtn_cd 에만. 모르면 빈 배열. **종목 지어내기 금지.**\n"
        "- scope=single_stock 이고 배치 키가 종목코드면 그 종목을 affected 에 포함하라.\n"
        "- evidence 는 위 기사 id 에만. 기사에 없는 사실 금지.\n"
        "- 미확인 소문은 catalyst_type=rumor_unconfirmed, catalyst_strength 낮게.\n"
        "- 유의미한 촉매가 없으면 events 를 빈 배열로."
    )


def _to_event_record(
    ev: dict[str, object],
    *,
    batch_key: str,
    idx: int,
    now: datetime,
    source: str,
    items_by_id: dict[str, NewsItem],
) -> EventRecord:
    """파싱된 이벤트 dict → EventRecord. 잘못된 값은 pydantic/Enum이 거부(호출부에서 폐기)."""
    summary = str(ev.get("summary_1line") or "").strip()
    if not summary:
        raise ValueError("summary_1line 누락")  # 핵심 필드 — 보정 불가, 폐기
    raw_aff = ev.get("affected")
    affected_list = raw_aff if isinstance(raw_aff, list) else []
    affected: list[AffectedStock] = []
    for a in affected_list:
        if isinstance(a, dict) and "srtn_cd" in a and "relevance" in a:
            rel = _safe_score(a["relevance"])
            if rel is not None:
                affected.append(AffectedStock(srtn_cd=str(a["srtn_cd"]), relevance=rel))
    # 결정론 귀속(결함① 가드): L1 배치의 single_stock 이벤트는 배치 키 종목이 정의상 대상이다.
    # 배치가 그 종목 쿼리로 수집된 기사라 환각 아님 — 연결 강도의 사후 공격은 R4 linkage 몫.
    scope = _safe_scope(ev.get("scope"))
    if (
        scope is Scope.SINGLE_STOCK
        and _SRTN.match(batch_key)
        and not any(a.srtn_cd == batch_key for a in affected)
    ):
        affected.insert(0, AffectedStock(srtn_cd=batch_key, relevance=1.0))
    raw_ev = ev.get("evidence")
    ev_list = raw_ev if isinstance(raw_ev, list) else []
    evidence = [str(e) for e in ev_list if str(e) in items_by_id]
    pubs = [items_by_id[e].published_at for e in evidence if items_by_id[e].published_at]
    as_of = max(p for p in pubs if p is not None) if pubs else now
    ctype = _safe_catalyst(ev.get("catalyst_type"))
    return EventRecord(
        id=f"evt.{now:%Y%m%d}.{_slug(batch_key)}.{idx:02d}",
        as_of=as_of,
        fetched_at=now,
        source=source,
        type=_coerce_event_type(ev.get("event_type"), ctype),
        summary_1line=summary,
        entities=[batch_key, *(a.srtn_cd for a in affected)],
        evidence=evidence,
        catalyst_type=ctype,
        scope=scope,
        catalyst_strength=_safe_score(ev.get("catalyst_strength")),
        novelty=_safe_score(ev.get("novelty")),
        affected=affected,
    )


def run_r2(
    client: LLMClient,
    verdicts: Sequence[NewsVerdict],
    candidates: Sequence[tuple[str, str]],
    *,
    now: datetime | None = None,
    config: R2Config | None = None,
    source: str = "r2:claude",
    on_batch: Callable[[BatchProgress], None] | None = None,
) -> R2Result:
    """게이트 통과 뉴스 → 배치 LLM 호출 → EventRecord 목록. 호출/스키마 실패는 격리·카운트.

    ``on_batch`` 가 주어지면 매 배치 직후 호출(운영 가시성 + 캐러 incremental 적재용).
    배치 LLM에러는 ``BatchProgress.error`` 로 전달하고 ``events=[]`` 로 다음 배치 진행.
    """
    resolved_now = now if now is not None else now_kst()
    cfg = config if config is not None else R2Config()
    items = [v.item for v in verdicts if cfg.include_stale or v.fresh]
    batches = build_batches(items, cfg)
    total = len(batches)
    events: list[EventRecord] = []
    rejected = 0
    reasons: list[str] = []
    errors: list[str] = []
    for idx, (key, batch) in enumerate(batches.items(), start=1):
        items_by_id = {it.id: it for it in batch}
        batch_events: list[EventRecord] = []
        batch_rejected = 0
        batch_error: str | None = None
        try:
            data = complete_json(client, build_prompt(key, batch, candidates))
            raw_events = data.get("events", []) if isinstance(data, dict) else []
        except LLMError as e:
            batch_error = str(e)
            errors.append(f"{key}: {e}")
            raw_events = []
        for i, ev in enumerate(raw_events):
            if not isinstance(ev, dict):
                batch_rejected += 1
                reasons.append(f"{key}#{i}: 이벤트가 객체 아님")
                continue
            try:
                batch_events.append(
                    _to_event_record(
                        ev, batch_key=key, idx=i, now=resolved_now, source=source, items_by_id=items_by_id
                    )
                )
            except (ValidationError, ValueError, KeyError, TypeError) as e:
                batch_rejected += 1
                reasons.append(f"{key}#{i}: {str(e)[:120]}")
        events.extend(batch_events)
        rejected += batch_rejected
        if on_batch is not None:
            on_batch(BatchProgress(
                index=idx, total=total, key=key,
                events=batch_events, rejected=batch_rejected, error=batch_error,
            ))
    return R2Result(
        events=events, batches=total, rejected=rejected,
        batch_errors=errors, rejected_reasons=reasons,
    )


__all__ = [
    "BatchProgress",
    "R2Config",
    "R2Result",
    "build_batches",
    "build_prompt",
    "run_r2",
]
