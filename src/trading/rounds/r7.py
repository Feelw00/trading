"""R7 — 평가·캘리브레이션 + 레짐 모니터 (주간 토 10:00. 설계서 §3 R7·§7).

**숫자는 코드가 계산하고 LLM은 해석만 한다**(설계서 §1 불변 원칙):
- 채점(페르소나 적중률·캘리브레이션·R4 기각 정확도)·레짐 프록시 = 이 모듈의 순수 함수.
- claude -p 는 계산된 성적표를 입력으로 **해석·프롬프트 개정안 제안**만 — 개정은
  자동 적용 금지(운영자 승인 후 버전 태깅, §3 R7). 제안 텍스트는 파일 박제.

채점 규약(보수):
- 논제 채점 = as_of 다음 거래일 종가(entry) → horizon 거래일 후 종가(exit) 방향 일치.
  트리거 발동 감지는 흐름 데이터 부재로 불가(R7-1) — "트리거 무관 방향 채점"임을 notes에 명시.
- 시계 미도래(immature)·가격 결측(no_data)은 **채점하지 않는다**(부분 채점·추측 금지).
- R4 정확도: 기각 이벤트의 affected가 window 내 |수익률| < 임계면 "기각 정확",
  생존 이벤트가 임계 이상 움직이면 "생존 정확".
- 레짐: 전종목 |등락률| 중앙값의 최근 N일 / 직전 기준선 비율(EOD 가용 프록시).
  설계 입력(시초 1시간 변동성·신용잔고·레버리지 ETF)은 미수집 — R7-1, notes 명시.
"""

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from trading.collectors.base import KST
from trading.collectors.market import MarketStore
from trading.contracts.event import EventRecord, Scope
from trading.contracts.score import CalibrationBucket, PersonaScore, ScoreRecord
from trading.contracts.thesis import Direction, Persona, ThesisRecord

_CALIB_EDGES = (0.0, 0.4, 0.55, 0.7, 1.0)


@dataclass(frozen=True)
class R7Config:
    r4_window_days: int = 3           # R4 정확도 관측 창(거래일)
    r4_move_threshold_pct: float = 3.0  # 유의미 이동 임계(%)
    regime_recent_days: int = 5       # 레짐 최근 창
    regime_baseline_days: int = 20    # 레짐 기준선 창


@dataclass(frozen=True)
class ThesisOutcome:
    thesis: ThesisRecord
    status: str                       # scored | immature | no_data | flat
    realized_pct: float | None = None
    hit: bool | None = None


def _srtn_of_thesis(t: ThesisRecord) -> str:
    parts = t.id.split(".")           # thesis.<YYYYMMDD>.<srtn>.<persona> (rounds/r3 규약)
    return parts[2] if len(parts) > 3 else ""


def score_thesis(
    thesis: ThesisRecord, store: MarketStore, trading_dates: Sequence[str]
) -> ThesisOutcome:
    """논제 1건 채점 — entry=as_of 다음 거래일 종가, exit=entry+horizon 거래일 종가."""
    if thesis.direction is Direction.FLAT:
        return ThesisOutcome(thesis, "flat")
    as_of_day = thesis.as_of.astimezone(KST).strftime("%Y%m%d")
    idx = bisect.bisect_right(trading_dates, as_of_day)   # as_of 이후 첫 거래일
    exit_idx = idx + thesis.horizon_days
    if exit_idx >= len(trading_dates):
        return ThesisOutcome(thesis, "immature")
    srtn = _srtn_of_thesis(thesis)
    closes = dict(store.closes_for(srtn, trading_dates[idx]))
    entry = closes.get(trading_dates[idx])
    exit_ = closes.get(trading_dates[exit_idx])
    if entry is None or exit_ is None or entry == 0:
        return ThesisOutcome(thesis, "no_data")
    realized = (exit_ - entry) / entry * 100.0
    hit = realized > 0 if thesis.direction is Direction.LONG else realized < 0
    return ThesisOutcome(thesis, "scored", realized_pct=realized, hit=hit)


def _calibration(outcomes: Sequence[ThesisOutcome]) -> list[CalibrationBucket]:
    buckets: list[CalibrationBucket] = []
    for lo, hi in zip(_CALIB_EDGES, _CALIB_EDGES[1:]):
        scored = [
            o for o in outcomes
            if o.status == "scored" and lo <= o.thesis.confidence < (hi if hi < 1.0 else 1.01)
        ]
        buckets.append(
            CalibrationBucket(lo=lo, hi=hi, n=len(scored), hits=sum(1 for o in scored if o.hit))
        )
    return buckets


def score_personas(
    theses: Sequence[ThesisRecord], store: MarketStore, trading_dates: Sequence[str]
) -> tuple[list[PersonaScore], list[ThesisOutcome]]:
    outcomes = [score_thesis(t, store, trading_dates) for t in theses]
    scores: list[PersonaScore] = []
    for persona in Persona:
        mine = [o for o in outcomes if o.thesis.persona is persona]
        scored = [o for o in mine if o.status == "scored"]
        hits = sum(1 for o in scored if o.hit)
        scores.append(
            PersonaScore(
                persona=persona,
                n_scored=len(scored),
                n_immature=sum(1 for o in mine if o.status == "immature"),
                n_flat=sum(1 for o in mine if o.status == "flat"),
                n_no_data=sum(1 for o in mine if o.status == "no_data"),
                n_hit=hits,
                hit_rate=(hits / len(scored)) if scored else None,
                calibration=_calibration(mine),
            )
        )
    return scores, outcomes


def score_r4(
    events: Sequence[EventRecord],
    store: MarketStore,
    trading_dates: Sequence[str],
    config: R7Config,
) -> tuple[int, int, int, int]:
    """R4 정확도 — (기각 검사수, 기각 정확수, 생존 검사수, 생존 정확수). 미성숙은 제외."""
    ref_checked = ref_correct = conf_checked = conf_correct = 0
    for e in events:
        if e.verification is None or e.scope is not Scope.SINGLE_STOCK or not e.affected:
            continue
        day = e.as_of.astimezone(KST).strftime("%Y%m%d")
        idx = bisect.bisect_right(trading_dates, day)
        exit_idx = idx + config.r4_window_days
        if exit_idx >= len(trading_dates):
            continue  # 미성숙
        srtn = e.affected[0].srtn_cd
        closes = dict(store.closes_for(srtn, trading_dates[idx]))
        entry, exit_ = closes.get(trading_dates[idx]), closes.get(trading_dates[exit_idx])
        if entry is None or exit_ is None or entry == 0:
            continue
        moved = abs((exit_ - entry) / entry * 100.0) >= config.r4_move_threshold_pct
        if e.verification.confirmed:
            conf_checked += 1
            conf_correct += int(moved)
        else:
            ref_checked += 1
            ref_correct += int(not moved)
    return ref_checked, ref_correct, conf_checked, conf_correct


def regime_ratio(store: MarketStore, trading_dates: Sequence[str], config: R7Config) -> float | None:
    """레짐 변동성 프록시 — 최근 N일 |등락률| 중앙값 평균 / 직전 기준선 평균. 데이터 부족=None."""
    need = config.regime_recent_days + config.regime_baseline_days
    if not trading_dates:
        return None
    start = trading_dates[max(0, len(trading_dates) - need)]
    series = store.daily_change_medians(start)
    if len(series) < config.regime_recent_days + 2:  # 기준선 최소 2일
        return None
    recent = [v for _, v in series[-config.regime_recent_days:]]
    baseline = [v for _, v in series[: -config.regime_recent_days]]
    base_avg = sum(baseline) / len(baseline)
    if base_avg == 0:
        return None
    return (sum(recent) / len(recent)) / base_avg


def evaluate(
    theses: Sequence[ThesisRecord],
    events: Sequence[EventRecord],
    store: MarketStore,
    *,
    now: datetime,
    config: R7Config | None = None,
    source: str = "r7:code",
) -> tuple[ScoreRecord, list[ThesisOutcome]]:
    """주간 평가 — 전부 결정론. LLM 비개입(해석은 별도)."""
    cfg = config if config is not None else R7Config()
    trading_dates = store.dates()
    persona_scores, outcomes = score_personas(theses, store, trading_dates)
    ref_c, ref_ok, conf_c, conf_ok = score_r4(events, store, trading_dates, cfg)
    ratio = regime_ratio(store, trading_dates, cfg)

    notes = [
        "트리거 발동 감지 불가(흐름 데이터 부재, R7-1) — 방향 채점은 트리거 무관",
        "레짐 설계 입력(시초 1시간 변동성·신용잔고·레버리지 ETF) 미수집 — |등락률| 중앙값 프록시만(R7-1)",
        "운영자 준수율: 집행 데이터 미수집(KIS 어댑터 미구현) — 측정 불가",
    ]
    if ratio is None:
        notes.append("레짐 프록시: 거래일 데이터 부족으로 미산출")

    period_start = trading_dates[0] if trading_dates else "00000000"
    period_end = trading_dates[-1] if trading_dates else "00000000"
    record = ScoreRecord(
        id=f"score.{now.astimezone(KST):%Y%m%d}",
        as_of=now, fetched_at=now, source=source,
        period_start=period_start, period_end=period_end,
        personas=persona_scores,
        r4_refuted_checked=ref_c, r4_refuted_correct=ref_ok,
        r4_confirmed_checked=conf_c, r4_confirmed_correct=conf_ok,
        regime_volatility_ratio=ratio,
        notes=notes,
    )
    return record, outcomes


def build_interpretation_prompt(record: ScoreRecord, outcomes: Sequence[ThesisOutcome]) -> str:
    """claude -p 해석·개정안 프롬프트 — 입력은 코드 계산 성적표만(수치 재계산 금지)."""
    lines = []
    for p in record.personas:
        hr = f"{p.hit_rate:.0%}" if p.hit_rate is not None else "N/A"
        lines.append(
            f"- {p.persona.value}: 채점 {p.n_scored} 적중률 {hr} "
            f"(미성숙 {p.n_immature}, flat {p.n_flat}, 결측 {p.n_no_data})"
        )
    scored = [o for o in outcomes if o.status == "scored"]
    detail = "\n".join(
        f"  [{o.thesis.persona.value}/{'적중' if o.hit else '빗나감'}] {o.thesis.thesis[:60]} "
        f"(conf {o.thesis.confidence}, 실현 {o.realized_pct:+.1f}%)"
        for o in scored[:15]
    ) or "  (채점된 논제 없음)"
    return (
        "너는 트레이딩 시스템의 주간 평가 라운드(R7) 해석자다. 아래 성적표는 코드가 계산했다 — "
        "**수치를 재계산하거나 새 수치를 만들지 마라.** 해석과 프롬프트 개정 제안만 출력한다.\n\n"
        f"## 기간\n{record.period_start} ~ {record.period_end}\n\n"
        f"## 페르소나 성적\n" + "\n".join(lines) + "\n\n"
        f"## 채점 상세(최대 15)\n{detail}\n\n"
        f"## R4 정확도\n기각 {record.r4_refuted_correct}/{record.r4_refuted_checked} 정확, "
        f"생존 {record.r4_confirmed_correct}/{record.r4_confirmed_checked} 정확\n\n"
        f"## 레짐\n변동성 비율(최근/기준): {record.regime_volatility_ratio}\n\n"
        f"## 측정 한계(결측)\n" + "\n".join(f"- {n}" for n in record.notes) + "\n\n"
        "## 출력 (마크다운)\n"
        "1. 페르소나별 해석(과신/과소신 — 캘리브레이션 관점, 표본 부족하면 부족하다고).\n"
        "2. R4 공격 강도 조정 제안(성적 나쁜 페르소나를 더 가혹하게 — 설계서 §3 R4).\n"
        "3. 프롬프트 개정안(있다면): 어느 라운드의 프롬프트를 왜·어떻게. "
        "**자동 적용되지 않는다 — 운영자 승인용 제안서다.** 표본이 부족하면 '개정 보류'가 정답이다."
    )


__all__ = [
    "R7Config",
    "ThesisOutcome",
    "build_interpretation_prompt",
    "evaluate",
    "regime_ratio",
    "score_personas",
    "score_r4",
    "score_thesis",
]
