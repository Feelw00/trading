"""스크리너 — 전종목 EOD에서 후보를 추리는 순수-코드 신호 분석(LLM 금지, R5.5 성격).

신호 3종(거래대금 급증·모멘텀·신고가 근접)을 **횡단면 백분위 랭크**로 정규화해 가중합.
이건 "오른다" 예측이 아니라 **관심 필터** — 왜 후보인지(발화 신호)는 LLM 전망 분석으로 넘긴다.

입력: ``data/market.sqlite``(MarketStore.daily_quotes). 임계치·가중치는 ScreenConfig로 튜닝.
실행: ``python -m trading.screener``.
"""

from dataclasses import dataclass

from trading.collectors.market import MarketStore


@dataclass(frozen=True)
class ScreenConfig:
    min_tr_prc: float = 1e10  # 거래대금 게이트(100억)
    min_mrkt_cap: float = 1e11  # 시총 게이트(1000억)
    top_n: int = 30
    lookback_surge: int = 20  # 거래대금 평균 기간
    mom_short: int = 20
    mom_long: int = 60
    lookback_high: int = 252  # 신고가 기준(52주)
    w_surge: float = 1.0
    w_momentum: float = 1.0
    w_high: float = 1.0
    common_only: bool = True  # 보통주만(단축코드 末 '0')
    # 액면병합/분할/증자 아티팩트 가드: 윈도우 내 상장주식수 max/min 비율이 이 값 초과면 제외
    # (raw 가격 시리즈가 구조적 불연속 → 모멘텀·신고가 신호 오염. 크레오에스지 5:1 병합 사례).
    exclude_adjustment_artifacts: bool = True
    max_share_ratio: float = 1.5
    # 하락장 절대필터(기본 off — 전략 선택). 설정 시 횡단면 랭크와 무관하게 절대 컷.
    min_mom_long: float | None = None       # 장기수익률 하한(예: -0.3 = 60일 -30% 미만 제외)
    min_high_proximity: float | None = None  # 52주 최고가 근접 하한(예: 0.5)


@dataclass(frozen=True)
class SignalSet:
    tr_value_surge: float  # 당일 거래대금 / N일 평균
    mom_short: float  # 단기 수익률
    mom_long: float  # 장기 수익률
    high_proximity: float  # 종가 / 252일 최고가
    # 커버리지 — 히스토리가 lookback에 못 미치면 위 값은 폴백(0.0 / 짧은 창)이다.
    # 폴백을 그럴듯한 숫자로 흘려보내지 않기 위해 "실제로 확보했는가"를 함께 싣는다.
    mom_short_ok: bool = True   # False = 히스토리 부족 → mom_short는 0.0 폴백
    mom_long_ok: bool = True    # False = 히스토리 부족 → mom_long은 0.0 폴백
    high_window_days: int = 0   # high_proximity 계산에 실제 쓰인 거래일 수(0=미상)


@dataclass(frozen=True)
class Candidate:
    srtn_cd: str
    name: str
    market: str | None
    clpr: float
    score: float
    signals: SignalSet


@dataclass(frozen=True)
class ScreenResult:
    as_of: str
    universe: int  # 게이트 통과 종목수
    candidates: list[Candidate]
    history_days: int = 0            # DB 보유 거래일 수
    warnings: tuple[str, ...] = ()   # 히스토리 부족 등 — 신호가 폴백이면 침묵하지 않는다


def _f(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _is_junk(name: str) -> bool:
    """스팩·리츠·우선주명 등 제외(보통주 코드 게이트의 보완)."""
    n = name or ""
    return ("스팩" in n) or n.endswith("리츠") or n.endswith("우") or n.endswith("우B")


def _percentiles(values: list[float]) -> list[float]:
    """각 값의 횡단면 백분위(0~1, 높을수록 상위). 동률은 순차 순위."""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    pct = [0.0] * n
    for rank, idx in enumerate(order):
        pct[idx] = rank / (n - 1)
    return pct


# 게이트 통과 종목: (srtn_cd, name, market, clpr, signals)
_Survivor = tuple[str, str, str | None, float, SignalSet]


def signals_from_series(recs: list[tuple[object, ...]], cfg: ScreenConfig) -> SignalSet:
    """시세 시리즈(bas_dt 오름차순) → 신호. 게이트 무관(단일종목 조회·스크리너 공용).

    recs 컬럼: (srtn_cd, name, market, bas_dt, clpr, hipr, tr_prc, mrkt_tot_amt).
    """
    closes = [c for c in (_f(r[4]) for r in recs) if c is not None]
    trs = [t for t in (_f(r[6]) for r in recs) if t is not None]
    highs = [h for h in (_f(r[5]) for r in recs) if h is not None and h > 0]
    clpr = closes[-1] if closes else 0.0
    tr_prc = trs[-1] if trs else 0.0
    recent_trs = trs[-cfg.lookback_surge :]
    surge = tr_prc / (sum(recent_trs) / len(recent_trs)) if recent_trs else 0.0
    short_ok = len(closes) > cfg.mom_short
    long_ok = len(closes) > cfg.mom_long
    mom_s = clpr / closes[-(cfg.mom_short + 1)] - 1 if short_ok else 0.0
    mom_l = clpr / closes[-(cfg.mom_long + 1)] - 1 if long_ok else 0.0
    high_prox = clpr / max(highs) if highs else 0.0
    return SignalSet(
        tr_value_surge=surge,
        mom_short=mom_s,
        mom_long=mom_l,
        high_proximity=high_prox,
        mom_short_ok=short_ok,
        mom_long_ok=long_ok,
        high_window_days=len(highs),
    )


def _survivor(srtn_cd: str, recs: list[tuple[object, ...]], as_of: str, cfg: ScreenConfig) -> _Survivor | None:
    last = recs[-1]
    if last[3] != as_of:  # 최근일 미거래
        return None
    if cfg.common_only and not srtn_cd.endswith("0"):
        return None
    name = str(last[1])
    if _is_junk(name):
        return None
    clpr = _f(last[4])
    tr_prc = _f(last[6])
    mcap = _f(last[7])
    if clpr is None or tr_prc is None or tr_prc < cfg.min_tr_prc:
        return None
    if mcap is not None and mcap < cfg.min_mrkt_cap:
        return None
    # 액면병합/분할/증자 아티팩트: 상장주식수가 윈도우 내 크게 바뀌면 raw 가격 시리즈 불연속 → 제외
    if cfg.exclude_adjustment_artifacts and _has_share_discontinuity(recs, cfg.max_share_ratio):
        return None
    s = signals_from_series(recs, cfg)
    # 하락장 절대필터(옵션) — 횡단면 랭크 무관 절대 컷
    if cfg.min_mom_long is not None and s.mom_long < cfg.min_mom_long:
        return None
    if cfg.min_high_proximity is not None and s.high_proximity < cfg.min_high_proximity:
        return None
    market = last[2] if isinstance(last[2], str) else None
    return (srtn_cd, name, market, clpr, s)


def _has_share_discontinuity(recs: list[tuple[object, ...]], max_ratio: float) -> bool:
    """**인접 거래일** 상장주식수(index 8) 비율이 임계 초과면 True.

    단일일 급변(액면분할·병합·무상증자=가격 레벨 불연속)만 잡는다 — 점진적 희석(CB전환·소규모
    증자)은 일별 변화가 작아 통과(false positive 방지). 크레오에스지 5:1 병합은 인접일 5배 점프.
    """
    shares = [v for v in (_f(r[8]) for r in recs) if v is not None and v > 0]
    return any(a / b > max_ratio or b / a > max_ratio for a, b in zip(shares, shares[1:]))


def _history_warnings(history_days: int, cfg: ScreenConfig) -> tuple[str, ...]:
    """DB 히스토리가 lookback에 못 미치면 경고 — 폴백을 조용히 내보내지 않는다.

    부족분은 에러가 아니라 *그럴듯한 숫자*로 나가기 때문에 위험하다:
    mom_long은 전 종목 0.0(모멘텀=단기 전용), high_proximity는 52주가 아닌 짧은 창의 고가 대비가 된다.
    후자는 점수에 그대로 반영되어 후보 순위를 바꾼다.
    """
    w: list[str] = []
    if history_days <= cfg.mom_long:
        w.append(
            f"히스토리 {history_days}거래일 ≤ mom_long {cfg.mom_long} → 장기 모멘텀 전 종목 0.0 폴백"
        )
    if history_days < cfg.lookback_high:
        w.append(
            f"히스토리 {history_days}거래일 < 신고가 창 {cfg.lookback_high} → "
            f"52주 아닌 {history_days}일 고가 대비로 계산(순위 영향)"
        )
    return tuple(w)


def screen(store: MarketStore, config: ScreenConfig | None = None) -> ScreenResult:
    cfg = config or ScreenConfig()
    as_of = store.latest_date()
    if as_of is None:
        return ScreenResult("", 0, [])
    history_days = len(store.dates())
    warnings = _history_warnings(history_days, cfg)
    cutoff = store.nth_recent_date(cfg.lookback_high) or as_of

    series: dict[str, list[tuple[object, ...]]] = {}
    for row in store.rows_since(cutoff):
        series.setdefault(str(row[0]), []).append(row)

    survivors: list[_Survivor] = []
    for srtn_cd, recs in series.items():
        recs.sort(key=lambda r: str(r[3]))  # bas_dt 오름차순
        s = _survivor(srtn_cd, recs, as_of, cfg)
        if s is not None:
            survivors.append(s)
    if not survivors:
        return ScreenResult(as_of, 0, [], history_days, warnings)

    surge_pct = _percentiles([s[4].tr_value_surge for s in survivors])
    mom_pct = _percentiles([(s[4].mom_short + s[4].mom_long) / 2 for s in survivors])
    high_pct = _percentiles([s[4].high_proximity for s in survivors])
    wsum = cfg.w_surge + cfg.w_momentum + cfg.w_high

    cands: list[Candidate] = []
    for i, s in enumerate(survivors):
        score = (
            cfg.w_surge * surge_pct[i] + cfg.w_momentum * mom_pct[i] + cfg.w_high * high_pct[i]
        ) / wsum
        cands.append(
            Candidate(srtn_cd=s[0], name=s[1], market=s[2], clpr=s[3], score=score, signals=s[4])
        )
    cands.sort(key=lambda c: c.score, reverse=True)
    return ScreenResult(as_of, len(survivors), cands[: cfg.top_n], history_days, warnings)


SECTOR_SOURCE = "llm-cls-v1"
# 병합 우선순위: 수기 큐레이션 > 멀티에이전트 LLM > grounded > LLM 폴백(P-2, 잔존 갭만).
# 앞 소스가 종목별 우선 — 폴백은 상위 소스를 절대 덮지 않는다.
SECTOR_SOURCES = ("manual-curated-v1", "llm-cls-v1", "dart-ksic-v1", "llm-fallback-v1")


def main() -> int:
    store = MarketStore()
    res = screen(store)
    secmap = store.sector_map_multi(SECTOR_SOURCES)
    store.close()
    for w in res.warnings:
        print(f"⚠️ {w}")
    if not res.candidates:
        print("후보 없음 (DB 비었거나 게이트 통과 종목 없음)")
        return 0
    print(
        f"스크리너 as_of={res.as_of} · 게이트 통과 {res.universe}종목 · "
        f"상위 {len(res.candidates)} · 히스토리 {res.history_days}거래일"
    )
    print(f"{'#':>3} {'종목':<12}{'점수':>5}{'거래대금배':>8}{'단기%':>7}{'장기%':>7}{'신고가':>7}  섹터")
    for i, c in enumerate(res.candidates, 1):
        g = c.signals
        secs = ",".join(secmap.get(c.srtn_cd, [])) or "미분류"
        short = f"{g.mom_short * 100:>6.1f}%" if g.mom_short_ok else f"{'n/a':>7}"
        long_ = f"{g.mom_long * 100:>6.1f}%" if g.mom_long_ok else f"{'n/a':>7}"
        print(
            f"{i:>3} {c.name:<12}{c.score:>5.2f}{g.tr_value_surge:>8.1f}"
            f"{short}{long_}{g.high_proximity:>7.3f}  {secs}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
