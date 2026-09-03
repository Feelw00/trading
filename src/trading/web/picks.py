"""선정 후보 페이지(/picks) — 코어 후보의 결정론 기대 분해(계측·보고 전용).

운영자 요구(2026-09-01): "전부 살 수는 없다 — 과거·현재 기반의 예상으로 몇 종목을
고르는 페이지". 헌법 절대금지 2(판단에 LLM 금지)에 따라 "예상"은 예측이 아니라
**관측 가능한 산술 분해**로만 구성한다:

- 가치 회귀 여력: **목표 PBR = min(자기 역사 5년 PBR 밴드 중앙, 정당 PBR)**로 회귀한다고
  가정할 때의 산술 상승률(= 목표 PBR / 현재 PBR − 1, `valuation.band`). v2.13(운영자 결재
  2026-09-03) 밴드 중앙 — v2.12까지의 섹터 중앙 PBR은 KRX 버킷 이질성(일반서비스에 바이오·플랫폼
  혼재 — 와이엔텍 +362%·한국종합기술 +455%)으로 폐기. v2.14(같은 날 결재) 정당 PBR 상한 캡
  = (ROE 5년 중앙 − 1%) ÷ (10% − 1%) — ROE가 자기자본비용을 밑도는 종목의 과거 배수 회귀 가정
  차단. 회귀는 가정이지 예측이 아니며, 실현 조건(이익 회복·국면 진행)을 함께 표기한다.
- 배당 캐리: 최근 사업보고서 기준 현금배당수익률(보통주) — 보유 기간 수익의 하한 축.
- 이익방향(영업, v2.2): 최신 연간 영업이익/자본 − 5년 중앙. 양수 = 영업 회복 진행
  (순이익 기준은 영업외 스파이크가 중앙값을 부풀려 폐기 — 운영자 결재 2026-09-01).
- 리스크 플래그: 국면·⚠고PER·⚠분할·관측 부족 — 감점이 아니라 표기(운영자 veto 재료).

**구조(운영자 결정 2026-09-02 "1+2+3"):** 한 표가 "심사 결과 원장"과 "새 심사 추천 큐"를
겸하면서 산업당 캡이 승인 종목까지 잘랐다(대한약품 사례). 이를 분리한다.
1. **심사 원장** — 유효 판정(승인·조건부·거부)이 있는 종목은 순위·캡과 무관하게 전부.
   코어 자격을 잃거나 심사에서 탈락해도 숨기지 않고 "코어 이탈"·"심사 탈락" 배지로 표시.
2. **심사 대기 큐** — 판정 없는 코어 종목만 대상으로 산업당 ``QUEUE_INDUSTRY_CAP``·총
   ``QUEUE_N``. 심사한 종목은 캡 자리를 차지하지 않으므로 큐가 자연 순환한다.
3. 큐 **선발**은 기존 순위(국면 → 산업 내 PBR 깊이 → ROE 품질, rank.shortlist), **표시**는
   위험조정수익률 내림차순(운영자 결재 2026-09-01).
**승인 노출 하한(운영자 지시 2026-09-02):** "실현 예상 수익이 30% 미만인 종목은 승인 종목에
나오지 않는다". 실현 예상 수익 = 회귀 여력. 심사 판정(질)은 원장에 그대로 두고, 노출은
``approved ∧ 여력 ≥ MIN_UPSIDE_PCT``의 **파생 게이트**(`Pick.effective_verdict`)로 곱한다 —
여력은 주가 함수라 매일 움직이므로 판정 자체에 넣으면 원장이 가격 잡음을 기록하게 된다.
미달 승인 종목은 "⏸승인 보류"로 조건부 표에 내려가고, 여력이 회복되면 자동 복귀한다.

`_build_picks()` = 원장 + 큐 — 자동 심사(review.auto_review)·대시보드·페이퍼(실보유 편입
목표가)가 같은 목록/산식을 소비한다. 코어 판정 로직은 `screen/__main__.py`와 동일 기준.
"""

import html
from collections.abc import Iterable
from dataclasses import dataclass, field

from trading.collectors.fins import FinStore
from trading.collectors.returns import ReturnsStore
from trading.contracts.longterm import CandidateRecord, CyclePhase, phase_ko
from trading.paper import MIN_UPSIDE_PCT, PaperStore
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
from trading.screen.rank import PHASE_PRIORITY, high_per, shortlist
from trading.screen.store import CandidateStore
from trading.valuation.band import (
    JUSTIFIED_COE,
    JUSTIFIED_G,
    PbrBand,
    TargetPbr,
    pbr_bands,
    regression_upside,
    target_pbr,
)
from trading.valuation.store import ValuationStore
from trading.web.glossary import phase_pill
from trading.web.layout import page

QUEUE_N = 12             # 심사 대기 큐 상한(미심사 코어)
QUEUE_INDUSTRY_CAP = 3   # 큐의 산업당 캡 — 다양성 장치(원장에는 적용하지 않는다)


@dataclass(frozen=True)
class Pick:
    rec: CandidateRecord
    name: str
    pbr: float | None
    upside_pct: float | None      # 목표 PBR(min(밴드 중앙, 정당 PBR)) 회귀 가정 산술 상승률(v2.14)
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
    tier: str = "queue"           # ledger(판정 있음) | queue(미심사 코어)
    core_ok: bool = True          # 코어 6축 충족 여부(원장 종목의 '코어 이탈' 배지)
    screen_ok: bool = True        # 최신 심사 통과 여부(원장 종목의 '심사 탈락' 배지)
    core_fail: list[str] = field(default_factory=list)  # 미충족 축 이름
    held: bool = False            # 페이퍼(매매 가이드) open 포지션 존재 — 실보유·명시 편입
    band: PbrBand | None = None   # v2.13 자기 역사 밴드(회귀 여력 셀 호버·감사용)
    target: TargetPbr | None = None  # v2.14 목표 PBR(밴드 중앙 vs 정당 PBR 캡 — 어느 앵커인지)
    roe_median: float | None = None  # ROE 5년 중앙(정당 PBR 입력, 호버 표시)

    @property
    def upside_ok(self) -> bool:
        """승인 노출 가격 조건 — 회귀 여력 ≥ +30%(결측은 불충족)."""
        return self.upside_pct is not None and self.upside_pct >= MIN_UPSIDE_PCT

    @property
    def effective_verdict(self) -> str | None:
        """표시 판정 — 승인이라도 여력 하한 미달이면 'approved_blocked'(승인 보류). 원장 불변."""
        if self.verdict == "approved" and not self.upside_ok:
            return "approved_blocked"
        return self.verdict


def current_upside(symbols: Iterable[str]) -> dict[str, float | None]:
    """심볼 → 현재 회귀 여력(%) — 밴드(시세·재무 DB) + ROE 5년 중앙(밸류에이션 최신본)의 경량 경로.

    페이퍼 실보유 편입 목표가·보유 종목 목표가 괴리 표기·`retarget auto`가 쓴다(/picks와 같은 산식
    `valuation.band.regression_upside` — v2.14 정당 PBR 캡 포함).
    """
    syms = list(symbols)
    if not syms:
        return {}
    vs = ValuationStore()
    try:
        roe = {v.symbol: v.roe_median_5y for v in vs.all_latest()}
    finally:
        vs.close()
    bands = pbr_bands(syms)
    return {s: regression_upside(bands.get(s), roe.get(s)) for s in syms}


def _build_picks() -> list[Pick]:
    cand_store = CandidateStore()
    try:
        run = cand_store.latest_run()
    finally:
        cand_store.close()
    if not run:
        return []
    # 심볼당 대표 레코드(다중 소속은 통과 레코드 우선)
    rec_by_sym: dict[str, CandidateRecord] = {}
    for r in sorted(run, key=lambda x: (not x.passed, x.industry)):
        rec_by_sym.setdefault(r.symbol, r)
    records = [r for r in run if r.passed]

    from trading.review import ReviewStore, latest_annual_year

    rstore = ReviewStore()
    try:
        verdicts = rstore.all_current(latest_annual_year())
    finally:
        rstore.close()
    ledger_syms = {s for s in verdicts if s in rec_by_sym}
    syms = {r.symbol for r in records} | ledger_syms

    pstore = PaperStore()
    try:
        held = {p.symbol for p in pstore.latest_positions() if p.status == "open"}
    finally:
        pstore.close()

    vs = ValuationStore()
    try:
        latest = vs.all_latest()
    finally:
        vs.close()
    vals = {v.symbol: v for v in latest}

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
    per_by_symbol = {v.symbol: v.per for v in latest if v.per is not None}
    roe_by_symbol = {
        v.symbol: v.roe_median_5y for v in latest if v.roe_median_5y is not None
    }

    # 코어 6축 — screen/__main__와 동일(v1.8 안정·고PER / v1.9 환원·분할 / v2.1 이익질 / v2.3 역성장)
    def _core_fail(r: CandidateRecord) -> list[str]:
        fails: list[str] = []
        if not is_stable_core(metrics[r.symbol]):
            fails.append("안정")
        if high_per(r, per_by_symbol):
            fails.append("고PER")
        if not meets_returns_core(streaks[r.symbol], cancels[r.symbol], industry=r.industry):
            fails.append("환원")
        if splits[r.symbol] != 0:
            fails.append("분할")
        if eq_flags[r.symbol]:
            fails.append("이익질")
        if trends[r.symbol].consecutive_decline:
            fails.append("역성장")
        return fails

    core_fail = {r.symbol: _core_fail(r) for r in records}
    core_pool = [r for r in records if not core_fail[r.symbol]]
    # 2. 심사 대기 큐 — 판정 없는 코어만, 산업당 캡·총 상한(선발 순위 = 기존 shortlist)
    queue = shortlist(
        [r for r in core_pool if r.symbol not in verdicts],
        roe_by_symbol=roe_by_symbol, per_by_symbol=per_by_symbol,
        top_n=QUEUE_N, per_industry_cap=QUEUE_INDUSTRY_CAP,
    )
    # 1. 심사 원장 — 판정 있는 종목 전부(캡 무관), 통과 여부·코어 자격은 배지로
    ledger = [rec_by_sym[s] for s in sorted(ledger_syms)]
    # v2.13 회귀 여력 앵커 = 자기 역사 PBR 밴드 — 표시 대상(원장+큐)만 계산(시세 스캔 비용)
    bands = pbr_bands([r.symbol for r in ledger] + [r.symbol for r in queue])

    from trading.web.data import stock_names

    names = stock_names()
    picks: list[Pick] = []
    for tier, recs in (("ledger", ledger), ("queue", queue)):
        for r in recs:
            val = vals.get(r.symbol)
            pbr = val.pbr if val else None
            band = bands.get(r.symbol)
            roe_med = val.roe_median_5y if val else None
            upside = regression_upside(band, roe_med)
            tp = target_pbr(band, roe_med) if band is not None else None
            # v2.2(운영자 결재 2026-09-01): 이익 방향은 영업이익/자본 기준 — 영업외 스파이크가
            # 중앙값을 부풀려 회복 종목이 음수로 보이던 왜곡(신세계I&C·슈피겐 사례) 제거
            od = op_dirs.get(r.symbol)
            roe_delta = od * 100 if od is not None else None
            fails = core_fail.get(r.symbol) if r.passed else _core_fail(r)
            fails = fails or []
            flags: list[str] = []
            if not r.passed:
                reason = (r.reject_reasons[0].split("(")[0] if r.reject_reasons else "사유 미기록")
                flags.append(f"✖심사 탈락({reason})")
            elif fails:
                flags.append(f"⚠코어 이탈({'·'.join(fails)})")
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
            verdict = (verdicts.get(r.symbol) or {}).get("verdict")
            if verdict == "approved" and not (upside is not None and upside >= MIN_UPSIDE_PCT):
                # 운영자 지시(2026-09-02): 실현 예상 수익 < +30%는 승인 종목에서 제외 — 판정은
                # 원장에 남고 노출만 보류. 여력이 하한을 넘으면 자동 복귀(저장 없음).
                why_blocked = (
                    "여력 결측" if upside is None
                    else f"여력 {upside:+.0f}% < +{MIN_UPSIDE_PCT:.0f}%"
                )
                flags.append(f"⏸승인 보류({why_blocked} — 회복 시 자동 복귀)")
            if r.symbol in held:
                flags.append("보유 중(가이드)")
            picks.append(
                Pick(
                    rec=r, name=names.get(r.symbol, r.symbol), pbr=pbr,
                    upside_pct=upside, div_yield=yields_.get(r.symbol),
                    div_streak=streaks[r.symbol], cancelled=cancels[r.symbol],
                    roe_delta=roe_delta, yoy_latest=tr.yoy_latest,
                    yoy_prev=tr.yoy_prev, splits=splits[r.symbol], flags=flags,
                    earn_yield=(ey := (100.0 / val.per) if val and val.per and val.per > 0 else None),
                    roe_cv5=(cv5 := cvs.get(r.symbol)),
                    risk_adj=(ey / (1.0 + cv5)) if ey is not None and cv5 is not None else None,
                    verdict=(verdicts.get(r.symbol) or {}).get("verdict"),
                    verdict_note=(verdicts.get(r.symbol) or {}).get("note")
                    or (verdicts.get(r.symbol) or {}).get("condition"),
                    tier=tier, core_ok=not fails, screen_ok=bool(r.passed), core_fail=fails,
                    held=r.symbol in held, band=band, target=tp, roe_median=roe_med,
                )
            )
    # 정렬: 국면 → 회귀 여력 내림차순(결측은 뒤) — 소비자(자동 심사 등)의 기본 순서
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
    원장은 캡과 무관하므로 승인 종목이 순위에 밀려 사라지지 않는다(2026-09-02).
    단 **회귀 여력 < +30%는 제외**(운영자 지시 2026-09-02: "실현 예상 수익 30% 미만은
    승인 종목에 안 나왔으면") — `Pick.effective_verdict` 파생 게이트.
    """
    return sorted(
        (p for p in picks if p.effective_verdict == "approved"),
        key=lambda p: -(p.risk_adj if p.risk_adj is not None else -999.0),
    )


_VERDICT_BADGE = {
    "approved": "✔승인", "approved_blocked": "⏸승인 보류", "vetoed": "✖veto", "hold": "⏸조건부",
}
# 원장 표 정렬 순서(운영자 지시 2026-09-01): 승인 → 조건부 (거부는 접힘)
_VERDICT_ORDER = {"approved": 0, "hold": 1, "pending": 2, "vetoed": 3}
# 표 열(운영자 지시 2026-09-02 "열이 너무 많다"): 결정 열만 — 분해 지표 전체는 종목 상세(/stocks).
_TABLE_HEAD = (
    "<table><tr><th>종목</th><th>산업</th><th>국면</th><th>회귀 여력</th>"
    "<th class='hl'>위험조정수익률</th><th>심사</th><th>표기</th></tr>"
)


def _fmt(v: float | None, suffix: str = "%") -> str:
    return f"{v:+.0f}{suffix}" if v is not None else "—"


def _band_tip(p: Pick) -> str:
    """회귀 여력 셀 호버 — 목표 PBR의 앵커(밴드 중앙 vs 정당 PBR 캡)와 근거. 결측이면 이유."""
    b = p.band
    if b is None:
        return "밴드 결측 — 이력 500거래일 미만 또는 연간 자본총계 결측(지어내지 않음)"
    t = p.target
    if t is None:
        return (f"ROE 5년 중앙 결측 — 정당 PBR 캡 검증 불가(여력 결측) · 5년 밴드 중앙 {b.median:.2f} · "
                f"현재 {b.current:.2f}")
    roe = f"{p.roe_median * 100:.1f}%" if p.roe_median is not None else "결측"
    coe_g = f"COE {JUSTIFIED_COE:.0%} · g {JUSTIFIED_G:.0%}"
    if t.anchor == "justified":
        head = (f"목표 PBR {t.value:.2f} = 정당 PBR 캡 (ROE 5년 중앙 {roe}, {coe_g}) "
                f"< 밴드 중앙 {t.band_median:.2f}")
    else:
        head = (f"목표 PBR {t.value:.2f} = 5년 밴드 중앙 (정당 PBR {t.justified:.2f} 미발동, "
                f"ROE 5년 중앙 {roe})")
    return (
        f"{head} · 현재 {b.current:.2f} · 최저~최고 {b.low:.2f}~{b.high:.2f} · "
        f"{b.n_days:,}거래일 · {b.equity_basis}"
    )


def _row(p: Pick) -> str:
    r = p.rec
    return (
        f"<tr><td><a href='/stocks/{r.symbol}'>{html.escape(p.name)}</a> "
        f"<span class='meta'>{r.symbol}</span></td>"
        f"<td>{html.escape(r.industry)}</td><td>{phase_ko(r.phase)}</td>"
        f"<td title='{html.escape(_band_tip(p), quote=True)}'>{_fmt(p.upside_pct)}</td>"
        f"<td class='hl'><b>{f'{p.risk_adj:.1f}%' if p.risk_adj is not None else '—'}</b></td>"
        f"<td>{_VERDICT_BADGE.get(p.effective_verdict or '', '대기')}</td>"
        f"<td class='meta'>{' '.join(p.flags) or '—'}</td></tr>"
    )


def _by_risk_adj(ps: list[Pick]) -> list[Pick]:
    return sorted(ps, key=lambda p: -(p.risk_adj if p.risk_adj is not None else -999.0))


def render_picks() -> str:
    picks = _build_picks()
    parts = ["<h1>선정 후보 — 결정론 기대 분해</h1>"]
    parts.append(
        "<div class='meta'>코어 후보(안정·환원·분할 무이력·비고PER·이익질 정상·非역성장)만 대상. "
        "<b>회귀 여력은 예측이 아니라 산술이다</b>: '목표 PBR(자기 역사 5년 밴드 중앙, 단 정당 PBR을 "
        "상한으로)로 회귀한다면'의 상승률이며, 실현 조건은 영업 회복(이익방향(영업) 양수)과 국면 진행이다. "
        "배당수익률은 최근 사업보고서 기준(현재가 기준 아님). "
        "편입·비중의 최종 기제는 §6 R5 포트폴리오(결재 예정) — 이 페이지는 계측·보고 전용.</div>"
    )
    if not picks:
        parts.append("<div class='card'>코어 후보 없음 — 스크리닝·수집 선행 필요</div>")
        return page("선정 후보", "".join(parts), active="/picks")

    ledger = [p for p in picks if p.tier == "ledger"]
    queue = [p for p in picks if p.tier == "queue"]
    approved = approved_picks(ledger)
    # 조건부 표 = 원장 hold + 승인이나 여력 하한 미달(승인 보류, 파생)
    holds = _by_risk_adj([p for p in ledger if p.effective_verdict in ("hold", "approved_blocked")])
    blocked = [p for p in holds if p.effective_verdict == "approved_blocked"]
    vetoed = _by_risk_adj([p for p in ledger if p.verdict == "vetoed"])

    # 승인 종목 카드 — 원장 기준(캡 무관). 코어 이탈·심사 탈락은 배지로.
    parts.append(
        f"<h2>승인 종목 <span class='meta'>심사 승인 ∧ 회귀 여력 ≥ +{MIN_UPSIDE_PCT:.0f}% "
        f"✔ {len(approved)}종 · 조건부 {len(holds)}(승인 보류 {len(blocked)}) · "
        f"거부 {len(vetoed)} · 심사 대기 큐 {len(queue)} — 매수 지시 아님</span></h2>"
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
            status = ""
            if not p.screen_ok:
                status = " <span class='pill warn'>심사 탈락</span>"
            elif not p.core_ok:
                status = f" <span class='pill warn'>코어 이탈 · {'·'.join(p.core_fail)}</span>"
            note = (
                f"<div class='meta'>심사: {html.escape(p.verdict_note)}</div>"
                if p.verdict_note else ""
            )
            parts.append(
                f"<div class='card'><b><a href='/stocks/{p.rec.symbol}'>"
                f"{html.escape(p.name)}</a></b> <span class='meta'>{p.rec.symbol} · "
                f"{html.escape(p.rec.industry)}</span> {phase_pill(p.rec.phase)}{status}"
                f"<div class='meta'>{cond}</div>"
                f"<div class='meta'>{' · '.join(why) if why else '분해 지표 결측'}"
                f"{(' · ' + ' '.join(p.flags)) if p.flags else ''}</div>{note}</div>"
            )
        parts.append("</div>")
    else:
        parts.append("<div class='card meta'>승인 종목 없음 — 심사 원장(⏸/대기)에서 "
                     "판정 후 노출된다</div>")

    # 1. 심사 원장 표 — 승인 → 조건부(위험조정수익률 내림차순), 거부는 접힘
    parts.append(f"<h2>심사 원장 <span class='meta'>승인 {len(approved)} · 조건부 {len(holds)} "
                 "— 판정이 있는 종목 전부, 순위·캡 무관. ⏸승인 보류 = 심사는 승인이나 회귀 여력이 "
                 f"+{MIN_UPSIDE_PCT:.0f}% 미만(실현 예상 수익 부족) — 여력 회복 시 승인으로 자동 복귀</span></h2>")
    if approved or holds:
        parts.append("<div class='card scroll'>" + _TABLE_HEAD
                     + "".join(_row(p) for p in [*approved, *holds]) + "</table></div>")
    else:
        parts.append("<div class='card meta'>판정된 종목 없음</div>")
    if vetoed:
        parts.append(
            f"<details><summary class='meta'>거부(veto) {len(vetoed)}종 펼치기</summary>"
            "<div class='card scroll'>" + _TABLE_HEAD
            + "".join(_row(p) for p in vetoed) + "</table></div></details>"
        )

    # 2. 심사 대기 큐 — 미심사 코어, 산업당 캡·총 상한. 표시는 위험조정수익률 내림차순.
    parts.append(
        f"<h2>심사 대기 큐 <span class='meta'>{len(queue)}종 — 판정 없는 코어 종목 중 "
        f"산업당 {QUEUE_INDUSTRY_CAP}·총 {QUEUE_N}(국면 → 산업 내 PBR 깊이 → ROE 순 선발). "
        "판정하면 큐에서 빠지고 다음 순위가 올라온다</span></h2>"
    )
    if queue:
        parts.append("<div class='card scroll'>" + _TABLE_HEAD
                     + "".join(_row(p) for p in _by_risk_adj(queue)) + "</table></div>")
    else:
        parts.append("<div class='card meta'>대기 종목 없음 — 코어 풀이 전부 판정됨</div>")
    parts.append(
        "<div class='meta'>산식: 회귀 여력 = 목표 PBR ÷ 현재 PBR − 1, "
        "<b>목표 PBR = min(자기 역사 5년 PBR 밴드 중앙, 정당 PBR)</b> (v2.14 — 정당 PBR = (ROE 5년 중앙 − "
        f"{JUSTIFIED_G:.0%}) ÷ ({JUSTIFIED_COE:.0%} − {JUSTIFIED_G:.0%}): ROE가 자기자본비용을 밑돌면 과거 "
        "배수 회귀는 근거가 없으므로 상한으로만 작동. 밴드 = 최근 5년 일별 PBR, 시총 = 종가 × 그날 주식수, "
        "분모 = 연간 자본총계를 다음 해 4/1부터 적용, 이력 500거래일 미만·ROE 결측은 결측) = 실현 예상 수익. "
        "셀 호버에 앵커·밴드 근거. "
        "<b>위험조정수익률</b> = 이익수익률(1/PER) ÷ (1 + 5년 ROE 변동계수) — 표 정렬 키. "
        "모든 값은 EOD·연간 재무의 as-of 기준 — 실시간 아님. "
        "PBR·깊이·배당·연속배당·이익방향(영업)·매출YoY·ROE변동계수 등 분해 지표 전체는 "
        "종목 이름을 눌러 종목 상세에서. "
        "<b>회귀 여력 +100% 초과는 실현 기대치가 아니라 자기 역사 대비 극단 저PBR의 산술</b> — "
        "2021 버블 잔상(밴드 중앙이 높음)·자본 급증·사업 축소 같은 구조 변화가 원인일 수 있어 "
        "회귀 근거를 따로 확인해야 한다(자동 심사는 +150% 초과를 hold).</div>"
    )
    return page("선정 후보", "".join(parts), active="/picks")


__all__ = [
    "Pick", "QUEUE_INDUSTRY_CAP", "QUEUE_N", "approved_picks", "current_upside",
    "regression_upside", "render_picks",
]
