"""섹터 히스토리 밴드 — R3 온도계 1차 축 재료(PIVOT-7 ②). 순수 코드·DB-first.

축(자체 데이터만, 외부 소스 무의존):
- **섹터 PBR**: Σ시총(연말 스냅샷) / Σ자본총계(같은 연도 연간 재무) — **짝이 있는 종목만** 합산
  (분자·분모 구성 불일치 왜곡 방지). 시세 커버리지 실측: data.go.kr 2020~ (2016~2019 무자료).
- **섹터 마진**: Σ영업이익 / Σ매출 (연간, DART 10년).
- **섹터 매출**: Σ매출 (연간) — 증감 사이클·구조적 사양(장기 추세) 판정 원료.

무결성 가드(설계서 §3 R3):
- 연도별 합산 구성 종목 수가 MIN_COMPOSITION 미만이면 그 연도 축=None(작은 표본 왜곡 방지).
- 구성 종목 수(n_pbr/n_fin)를 함께 반환 — 급변 플래그(밴드 연속성)는 소비자가 판정.
- 한계 명시: 섹터 소속은 **현재 시점 태깅**을 과거에 소급 적용(생존·구성 변화 편향 잔존,
  업종지수 교차검증은 후속).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from trading.collectors.fins import FinStore
from trading.collectors.market import MarketStore
from trading.sectors import KRX_SOURCE
from trading.valuation.metrics import percentile_rank

MIN_COMPOSITION = 3


@dataclass(frozen=True)
class SectorYear:
    year: str            # "2024" — 현재 시점 행은 "current"
    pbr: float | None
    margin: float | None
    revenue: float | None
    n_pbr: int           # PBR 합산(시총·자본 짝) 구성 종목 수
    n_fin: int           # 재무 합산 구성 종목 수


@dataclass(frozen=True)
class BandPosition:
    sector: str
    pbr_band_pct: float | None      # 현재 PBR의 히스토리 대비 하위 percentile
    margin_band_pct: float | None
    rev_yoy: float | None           # 최근 연간 매출 YoY(사이클 방향 재료)
    pbr_years: int                  # PBR 히스토리 관측 연수(현재 포함)
    fin_years: int


def _sector_of(sector_map: Mapping[str, list[str]], srtn_cd: str) -> str | None:
    tags = sector_map.get(srtn_cd)
    return tags[0] if tags else None


def build_sector_years(
    fin_store: FinStore,
    market_store: MarketStore,
    *,
    year_end_dates: Mapping[str, str],
    extra_groups: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[SectorYear]]:
    """섹터별 연도 시계열. ``year_end_dates``: {"2024": "20241230", ..., "current": <최신일>}.

    ``extra_groups``: 큐레이션 그룹(policy-v1.0 ① 조선 등) — {그룹명: [종목코드]}.
    KRX 버킷 파생 그룹에 **추가**된다(동명이면 큐레이션이 우선).
    """
    sector_map = market_store.sector_map(KRX_SOURCE)
    annuals: dict[str, dict[str, dict[str, float | None]]] = {}
    for srtn_cd in fin_store.symbols():
        annuals[srtn_cd] = dict(fin_store.annual_series(srtn_cd))

    caps_by_label: dict[str, dict[str, float | None]] = {
        label: market_store.quotes_on(ymd) for label, ymd in year_end_dates.items()
    }
    fin_years = sorted({y for series in annuals.values() for y in series}, reverse=True)

    groups: dict[str, list[str]] = {
        sector: [cd for cd in annuals if _sector_of(sector_map, cd) == sector]
        for sector in sorted({s for tags in sector_map.values() for s in tags})
    }
    for name, codes in (extra_groups or {}).items():
        groups[name] = [cd for cd in codes if cd in annuals]  # 재무 미적재 종목은 정직 제외

    out: dict[str, list[SectorYear]] = {}
    for sector, members in groups.items():
        rows: list[SectorYear] = []
        for year in fin_years:
            # 재무 축 — 그 연도 연간 재무가 있는 구성 종목 합산
            rev_pairs = [
                (annuals[cd][year].get("revenue"), annuals[cd][year].get("op_income"))
                for cd in members
                if year in annuals[cd]
            ]
            revs = [r for r, _o in rev_pairs if r is not None]
            both = [(r, o) for r, o in rev_pairs if r is not None and o is not None]
            n_fin = len(revs)
            revenue = sum(revs) if n_fin >= MIN_COMPOSITION else None
            both_rev = sum(r for r, _o in both)
            margin = (
                sum(o for _r, o in both) / both_rev
                if len(both) >= MIN_COMPOSITION and both_rev > 0
                else None
            )
            # PBR 축 — 그 연도 연말 시총과 연간 자본 **짝이 모두 있는** 종목만
            caps = caps_by_label.get(year, {})
            pairs = [
                (caps.get(cd), annuals[cd][year].get("equity"))
                for cd in members
                if year in annuals[cd]
            ]
            matched = [(c, e) for c, e in pairs if c is not None and e is not None and e > 0]
            n_pbr = len(matched)
            pbr = (
                sum(c for c, _e in matched) / sum(e for _c, e in matched)
                if n_pbr >= MIN_COMPOSITION
                else None
            )
            rows.append(SectorYear(year, pbr, margin, revenue, n_pbr, n_fin))

        # 현재 행 — 최신 시총 × 최신 연간 자본(밴드의 "지금 위치" 판정용)
        if "current" in caps_by_label:
            caps_now = caps_by_label["current"]
            matched_now = []
            for cd in members:
                series = annuals[cd]
                latest_eq = next(
                    (v["equity"] for _y, v in sorted(series.items(), reverse=True) if v["equity"]),
                    None,
                )
                cap = caps_now.get(cd)
                if cap is not None and latest_eq is not None and latest_eq > 0:
                    matched_now.append((cap, latest_eq))
            pbr_now = (
                sum(c for c, _e in matched_now) / sum(e for _c, e in matched_now)
                if len(matched_now) >= MIN_COMPOSITION
                else None
            )
            rows.insert(0, SectorYear("current", pbr_now, None, None, len(matched_now), 0))
        out[sector] = rows
    return out


def band_positions(sector_years: Mapping[str, Sequence[SectorYear]]) -> list[BandPosition]:
    """현재 값의 히스토리 대비 하위 percentile — 관측 연수와 함께(짧은 창 정직 표기)."""
    out: list[BandPosition] = []
    for sector, rows in sector_years.items():
        current = next((r for r in rows if r.year == "current"), None)
        hist = [r for r in rows if r.year != "current"]

        pbr_obs = [r.pbr for r in hist if r.pbr is not None]
        pbr_pct = None
        if current is not None and current.pbr is not None and pbr_obs:
            pbr_pct = percentile_rank([*pbr_obs, current.pbr], current.pbr)

        margin_obs = [(r.year, r.margin) for r in hist if r.margin is not None]
        margin_pct = None
        if len(margin_obs) >= 2:
            latest_margin = margin_obs[0][1]
            assert latest_margin is not None
            margin_pct = percentile_rank([m for _y, m in margin_obs if m is not None], latest_margin)

        revs = [(r.year, r.revenue) for r in hist if r.revenue is not None]
        rev_yoy = None
        if len(revs) >= 2 and revs[1][1]:
            latest_rev, prev_rev = revs[0][1], revs[1][1]
            assert latest_rev is not None and prev_rev is not None
            rev_yoy = latest_rev / prev_rev - 1

        out.append(
            BandPosition(
                sector=sector,
                pbr_band_pct=pbr_pct,
                margin_band_pct=margin_pct,
                rev_yoy=rev_yoy,
                pbr_years=len(pbr_obs) + (1 if current is not None and current.pbr is not None else 0),
                fin_years=len(margin_obs),
            )
        )
    return sorted(out, key=lambda b: (b.pbr_band_pct if b.pbr_band_pct is not None else 2.0))


__all__ = ["BandPosition", "MIN_COMPOSITION", "SectorYear", "band_positions", "build_sector_years"]
