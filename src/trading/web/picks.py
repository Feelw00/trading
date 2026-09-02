"""선정 후보 페이지(/picks) — 코어 후보의 결정론 기대 분해(계측·보고 전용).

운영자 요구(2026-09-01): "전부 살 수는 없다 — 과거·현재 기반의 예상으로 몇 종목을
고르는 페이지". 헌법 절대금지 2(판단에 LLM 금지)에 따라 "예상"은 예측이 아니라
**관측 가능한 산술 분해**로만 구성한다:

- 가치 회귀 여력: 섹터 중앙 PBR로 회귀한다고 가정할 때의 산술 상승률
  (= 섹터 중앙 PBR / 현재 PBR − 1). 회귀는 가정이지 예측이 아니며, 실현 조건
  (이익 회복·국면 진행)을 함께 표기한다.
- 배당 캐리: 최근 사업보고서 기준 현금배당수익률(보통주) — 보유 기간 수익의 하한 축.
- 이익방향(영업, v2.2): 최신 연간 영업이익/자본 − 5년 중앙. 양수 = 영업 회복 진행
  (순이익 기준은 영업외 스파이크가 중앙값을 부풀려 폐기 — 운영자 결재 2026-09-01).
- 리스크 플래그: 국면·⚠고PER·⚠분할·관측 부족 — 감점이 아니라 표기(운영자 veto 재료).

노출: 심사 원장 승인(✔) 종목만 상단 카드로(v2.4 — 구 제안 바스켓 폐지, 운영자 지시
2026-09-01). 하단 표는 심사순(✔→⏸→대기→✖) 정렬. 최종 기제는 §6 R5 포트폴리오.
코어 판정 로직은 `screen/__main__.py`와 동일 기준(v1.8·v1.9·v2.1·v2.3) — 개정 시 양쪽 동기.
"""

import html
from dataclasses import dataclass

from trading.collectors.fins import FinStore
from trading.collectors.returns import ReturnsStore
from trading.contracts.longterm import CandidateRecord, CyclePhase, phase_ko
from trading.screen.quality import (
    dividend_streak,
    earnings_quality_flag,
    has_cancellation,
    is_stable_core,
    meets_returns_core,
    op_roe_direction,
    revenue_trend,
    roe_cv,
    stability_metrics,
)
from trading.screen.rank import PHASE_PRIORITY, high_per, shortlist, value_depth
from trading.screen.store import CandidateStore
from trading.valuation.store import ValuationStore
from trading.web.glossary import phase_pill
from trading.web.layout import page

TABLE_N = 20           # 페이지 표 상한


@dataclass(frozen=True)
class Pick:
    rec: CandidateRecord
    name: str
    pbr: float | None
    upside_pct: float | None      # 섹터 중앙 PBR 회귀 가정 산술 상승률
    div_yield: float | None       # 최근 사업보고서 현금배당수익률(보통주, %)
    div_streak: int
    cancelled: bool
    roe_delta: float | None       # v2.2: 최신 영업이익/자본 − 5y 중앙(양수=영업 회복 진행)
    yoy_latest: float | None      # v2.3: 최근 연간 매출 YoY
    yoy_prev: float | None        # v2.3: 직전 연간 매출 YoY
    splits: int
    flags: list[str]
    earn_yield: float | None = None  # 이익수익률 = 100/PER — 밸류 불변 가정 기대수익 근사
    roe_cv5: float | None = None     # ROE 변동계수(5y) — 낮을수록 수익 안정
    risk_adj: float | None = None    # 위험조정수익률 = 이익수익률/(1+CV) — 표 2차 정렬 키
    verdict: str | None = None    # v2.4 심사 원장: approved|vetoed|hold|None(pending)
    verdict_note: str | None = None  # 승인 근거/hold 조건(카드 표시용)


def _build_picks() -> list[Pick]:
    cand_store = CandidateStore()
    try:
        records = [r for r in cand_store.latest_run() if r.passed]
    finally:
        cand_store.close()
    if not records:
        return []
    syms = {r.symbol for r in records}

    vs = ValuationStore()
    try:
        latest = vs.all_latest()
    finally:
        vs.close()
    vals = {v.symbol: v for v in latest}
    # 섹터 중앙 PBR(전 상장 기준) — 회귀 여력 분모
    by_sector: dict[str, list[float]] = {}
    for v in latest:
        if v.pbr is not None and v.sector_krx:
            by_sector.setdefault(v.sector_krx, []).append(v.pbr)
    sector_median = {
        s: sorted(ps)[len(ps) // 2] for s, ps in by_sector.items() if len(ps) >= 5
    }

    fin_store = FinStore()
    try:
        metrics = {}
        eq_flags = {}
        op_dirs = {}
        trends = {}
        cvs = {}
        for s in syms:
            series = fin_store.annual_series(s)
            metrics[s] = stability_metrics(series)
            eq_flags[s] = earnings_quality_flag(series)  # v2.1 이익 질
            op_dirs[s] = op_roe_direction(series)        # v2.2 영업 기준 방향
            trends[s] = revenue_trend(series)            # v2.3 역성장 가드
            cvs[s] = roe_cv(series)                      # 수익 안정성 표시축
    finally:
        fin_store.close()
    ret = ReturnsStore()
    try:
        streaks = {s: dividend_streak(ret.dividend_series(s)) for s in syms}
        yields_ = {}
        for s in syms:
            div = ret.dividend_series(s)
            years = sorted(div)
            yields_[s] = div[years[-1]].get("yield_pct") if years else None
        cancels = {s: has_cancellation(ret.buyback_series(s)) for s in syms}
        splits = {s: len(ret.split_history(s)) for s in syms}
    finally:
        ret.close()
    # v2.4 심사 원장 — 유효 판정(만료=pending). veto는 바스켓 제외, 표에는 배지로 노출
    from trading.review import ReviewStore, latest_annual_year

    rstore = ReviewStore()
    try:
        verdicts = rstore.all_current(latest_annual_year())
    finally:
        rstore.close()
    per_by_symbol = {v.symbol: v.per for v in latest if v.per is not None}
    roe_by_symbol = {
        v.symbol: v.roe_median_5y for v in latest if v.roe_median_5y is not None
    }

    # 코어 판정 — screen/__main__와 동일(v1.8 안정·고PER / v1.9 환원·분할 / v2.1 이익질 / v2.3 역성장)
    core_pool = [
        r for r in records
        if is_stable_core(metrics[r.symbol])
        and not high_per(r, per_by_symbol)
        and meets_returns_core(streaks[r.symbol], cancels[r.symbol], industry=r.industry)
        and splits[r.symbol] == 0
        and not eq_flags[r.symbol]
        and not trends[r.symbol].consecutive_decline
    ]
    ordered = shortlist(
        core_pool, roe_by_symbol=roe_by_symbol, per_by_symbol=per_by_symbol,
        top_n=TABLE_N, per_industry_cap=3,
    )

    from trading.web.data import stock_names

    names = stock_names()
    picks: list[Pick] = []
    for r in ordered:
        val = vals.get(r.symbol)
        pbr = val.pbr if val else None
        med = sector_median.get(val.sector_krx or "") if val else None
        upside = (med / pbr - 1) * 100 if (pbr and med and pbr > 0) else None
        # v2.2(운영자 결재 2026-09-01): 이익 방향은 영업이익/자본 기준 — 영업외 스파이크가
        # 중앙값을 부풀려 회복 종목이 음수로 보이던 왜곡(신세계I&C·슈피겐 사례) 제거
        od = op_dirs.get(r.symbol)
        roe_delta = od * 100 if od is not None else None
        flags: list[str] = []
        if r.cycle_caution:
            flags.append("⚠과열 산업")
        if r.phase is CyclePhase.UNKNOWN:
            flags.append("국면 미확정")
        m = metrics[r.symbol]
        if m.years_observed < 5:
            flags.append(f"관측 {m.years_observed}년")
        tr = trends[r.symbol]
        if tr.sharp_drop:
            flags.append(f"⚠매출급감 {tr.yoy_latest:+.0%}")
        picks.append(
            Pick(
                rec=r, name=names.get(r.symbol, r.symbol), pbr=pbr,
                upside_pct=upside, div_yield=yields_.get(r.symbol),
                div_streak=streaks[r.symbol], cancelled=cancels[r.symbol],
                roe_delta=roe_delta, yoy_latest=trends[r.symbol].yoy_latest,
                yoy_prev=trends[r.symbol].yoy_prev, splits=splits[r.symbol], flags=flags,
                earn_yield=(ey := (100.0 / val.per) if val and val.per and val.per > 0 else None),
                roe_cv5=(cv5 := cvs.get(r.symbol)),
                risk_adj=(ey / (1.0 + cv5)) if ey is not None and cv5 is not None else None,
                verdict=(verdicts.get(r.symbol) or {}).get("verdict"),
                verdict_note=(verdicts.get(r.symbol) or {}).get("note")
                or (verdicts.get(r.symbol) or {}).get("condition"),
            )
        )
    # 정렬: 국면 → 회귀 여력 내림차순(결측은 뒤)
    picks.sort(
        key=lambda p: (
            PHASE_PRIORITY.get(p.rec.phase, 9),
            -(p.upside_pct if p.upside_pct is not None else -999.0),
        )
    )
    return picks


def _direction_tier(p: Pick) -> int:
    """실현 조건 층위 — 0=이익방향(영업) 양전(조건 충족), 1=음수/결측(회복 대기).

    운영자 지적(2026-09-01): 여력 크기만으로 정렬하면 영업 하강 진행형(KX -9.8%p)이
    바스켓에 앉는다 — 제안 바스켓은 조건 충족 종목을 먼저 채운다.
    """
    return 0 if (p.roe_delta is not None and p.roe_delta >= 0) else 1


def approved_picks(picks: list[Pick]) -> list[Pick]:
    """심사 승인(✔) 종목 — 위험조정수익률 내림차순(하단 표 2차 정렬과 동일 기준).

    노출 자격은 산식이 아니라 **심사 원장의 approved 판정**이다 — 기계는 후보·분해
    지표를 대고 노출은 판정이, 순서는 위험조정수익률이 결정한다(운영자 지시 2026-09-01).
    """
    return sorted(
        (p for p in picks if p.verdict == "approved"),
        key=lambda p: -(p.risk_adj if p.risk_adj is not None else -999.0),
    )


_VERDICT_BADGE = {
    "approved": "✔승인", "vetoed": "✖veto", "hold": "⏸조건부",
}
# 하단 표 정렬 순서(운영자 지시 2026-09-01): 승인 → 조건부 → 대기 → veto
_VERDICT_ORDER = {"approved": 0, "hold": 1, "pending": 2, "vetoed": 3}


def _fmt(v: float | None, suffix: str = "%") -> str:
    return f"{v:+.0f}{suffix}" if v is not None else "—"


def render_picks() -> str:
    picks = _build_picks()
    parts = ["<h1>선정 후보 — 결정론 기대 분해</h1>"]
    parts.append(
        "<div class='meta'>코어 후보(안정·환원·분할 무이력·비고PER·이익질 정상·非역성장)만 대상. "
        "<b>회귀 여력은 예측이 아니라 산술이다</b>: '섹터 중앙 PBR로 회귀한다면'의 "
        "상승률이며, 실현 조건은 영업 회복(이익방향(영업) 양수)과 국면 진행이다. "
        "배당수익률은 최근 사업보고서 기준(현재가 기준 아님). "
        "편입·비중의 최종 기제는 §6 R5 포트폴리오(결재 예정) — 이 페이지는 계측·보고 전용.</div>"
    )
    if not picks:
        parts.append("<div class='card'>코어 후보 없음 — 스크리닝·수집 선행 필요</div>")
        return page("선정 후보", "".join(parts), active="/picks")

    # 승인 종목 노출(운영자 지시 2026-09-01) — 제안 바스켓 폐지: 기계는 후보·분해
    # 지표를 대고, 노출 자격은 심사 원장의 approved 판정이 결정한다.
    approved = approved_picks(picks)
    n_pending = sum(1 for p in picks if p.verdict is None)
    parts.append(
        f"<h2>승인 종목 (심사 원장 ✔ · {len(approved)}종 · 심사 대기 {n_pending}종 "
        f"— 매수 지시 아님)</h2>"
    )
    if approved:
        parts.append("<div class='grid3'>")
        for p in approved:
            why = []
            if p.upside_pct is not None:
                why.append(f"섹터 중앙 회귀 시 {p.upside_pct:+.0f}%")
            if p.div_yield:
                why.append(f"배당 {p.div_yield:.1f}%")
            if p.roe_delta is not None:
                why.append(f"이익방향(영업) {p.roe_delta:+.1f}%p")
            cond = (
                "<b>✅ 실현 조건 충족</b>" if _direction_tier(p) == 0
                else "⏳ 회복 대기(여력만 확보)"
            )
            note = (
                f"<div class='meta'>심사: {html.escape(p.verdict_note)}</div>"
                if p.verdict_note else ""
            )
            parts.append(
                f"<div class='card'><b><a href='/stocks/{p.rec.symbol}'>"
                f"{html.escape(p.name)}</a></b> <span class='meta'>{p.rec.symbol} · "
                f"{html.escape(p.rec.industry)}</span> {phase_pill(p.rec.phase)}"
                f"<div class='meta'>{cond}</div>"
                f"<div class='meta'>{' · '.join(why) if why else '분해 지표 결측'}"
                f"{(' · ' + ' '.join(p.flags)) if p.flags else ''}</div>{note}</div>"
            )
        parts.append("</div>")
    else:
        parts.append("<div class='card meta'>승인 종목 없음 — 심사 원장(⏸/대기)에서 "
                     "판정 후 노출된다</div>")

    # 하단 표 — 심사순(✔→⏸→대기→✖) → 위험조정수익률 내림차순(운영자 결재 2026-09-01:
    # "수익에 이득이 되는 순" 2차 정렬. 회귀 여력은 상한 시나리오라 배제 — 착시 학습 반영)
    picks = sorted(
        picks,
        key=lambda p: (
            _VERDICT_ORDER.get(p.verdict or "pending", 2),
            -(p.risk_adj if p.risk_adj is not None else -999.0),
        ),
    )
    parts.append(f"<h2>코어 후보 전체 (상위 {len(picks)} — 심사순 정렬)</h2>")
    rows = [
        "<div class='card scroll'><table><tr><th>종목</th><th>산업</th><th>국면</th>"
        "<th>PBR</th><th>깊이</th><th>회귀 여력</th><th>배당수익률</th><th class='hl'>이익수익률</th>"
        "<th>연속배당</th><th>이익방향(영업)</th><th>매출YoY(최근/직전)</th><th class='hl'>ROE변동계수</th><th class='hl'>위험조정수익률</th><th>심사</th><th>표기</th></tr>"
    ]
    for p in picks:
        r = p.rec
        ret_s = f"{p.div_streak}y" + (" · 소각" if p.cancelled else "")
        rows.append(
            f"<tr><td><a href='/stocks/{r.symbol}'>{html.escape(p.name)}</a> "
            f"<span class='meta'>{r.symbol}</span></td>"
            f"<td>{html.escape(r.industry)}</td><td>{phase_ko(r.phase)}</td>"
            f"<td>{f'{p.pbr:.2f}' if p.pbr is not None else '—'}</td>"
            f"<td>{value_depth(r):.0%}</td>"
            f"<td>{_fmt(p.upside_pct)}</td>"
            f"<td>{f'{p.div_yield:.1f}%' if p.div_yield else '—'}</td>"
            f"<td class='hl'>{f'{p.earn_yield:.1f}%' if p.earn_yield else '—'}</td>"
            f"<td>{ret_s}</td><td>{_fmt(p.roe_delta, '%p')}</td>"
            f"<td>{_fmt(p.yoy_latest * 100 if p.yoy_latest is not None else None)}"
            f" / {_fmt(p.yoy_prev * 100 if p.yoy_prev is not None else None)}</td>"
            f"<td class='hl'>{f'{p.roe_cv5:.2f}' if p.roe_cv5 is not None else '—'}</td>"
            f"<td class='hl'><b>{f'{p.risk_adj:.1f}%' if p.risk_adj is not None else '—'}</b></td>"
            f"<td>{_VERDICT_BADGE.get(p.verdict or '', '대기')}</td>"
            f"<td class='meta'>{' '.join(p.flags) or '—'}</td></tr>"
        )
    rows.append("</table></div>")
    parts += rows
    parts.append(
        "<div class='meta'>산식: 회귀 여력 = 섹터 중앙 PBR ÷ 현재 PBR − 1 "
        "(섹터 표본 ≥5 전 상장 기준). 이익방향(영업) = 최신 연간 영업이익/자본 − 5년 중앙(v2.2 — 순이익 기준은 영업외 스파이크가 중앙값을 부풀려 폐기). "
        "모든 값은 EOD·연간 재무의 as-of 기준 — 실시간 아님. <b>강조 열</b>: 이익수익률=1/PER(밸류 불변 가정의 장기 기대수익 근사) · ROE변동계수=5년 ROE 표준편차÷평균(낮을수록 수익 안정, 평균≤0·관측<4년 결측) · <b>위험조정수익률=이익수익률÷(1+변동계수)</b> — 표의 2차 정렬 키(심사순 내). "
        "<b>회귀 여력 +100% 초과는 실현 기대치가 아니라 극단 저PBR의 산술</b> — "
        "지주·연결 구조 할인(자본 대비 시총 괴리)이 원인일 수 있으며, 그 할인은 "
        "지배구조 이벤트 없이 좀처럼 해소되지 않는다.</div>"
    )
    return page("선정 후보", "".join(parts), active="/picks")


__all__ = ["Pick", "approved_picks", "render_picks"]
