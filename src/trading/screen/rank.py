"""R4 통과 후보 우선순위 정렬 — 순수 함수. P-18 "사이클은 도구(우선순위·정렬)"의 구현.

게이트가 아니다: 통과/탈락을 바꾸지 않고 **운영자가 보는 순서**만 정한다(계측·보고 전용).
R5 포트폴리오(§6 결재 예정) 전까지 통과 폭증(P-18 전 상장 확장 후 ~545종)을
사람이 생으로 훑지 않게 하는 결정론 층. 도입 배경: 기존 top-40이 시장 percentile
단일 키라 bottoming 자산주 2~3개 산업(화학 19·IT서비스 8)으로 채워져 변별력이 없었다.

정렬 키(우선순위 순):
1. 국면 — bottoming > recovering > unknown > declining > overheated
   (발동 존 먼저, 과열 ⚠는 자연 강등 — 결재 ① "탈락 아님 플래그"와 정합)
2. 이익 축(운영자 결재 2026-09-01) — PER > 15(이익수익률 6.7% 미만)는 같은 국면 내
   최하위로 강등. "장부는 깊은데 이익이 붕괴한" 종목이 가치 깊이를 타고 상위 도배되는
   것을 차단(실측: 기존 숏리스트 40 중 4종이 PER 23~338). PER 결측은 강등하지 않는다.
3. 가치 깊이 — min(산업 내, 시장 전체) PBR percentile 오름차순
   (결재 ③ 병행 OR 게이트의 미러: 어느 축이든 깊은 쪽을 인정)
4. 품질 — 5년 ROE 중앙값 내림차순(v1.2 가치 함정 방어 지표 재사용, 결측=0)
5. 심볼 오름차순(결정론 보장)

숏리스트: 심볼 중복 제거(다중 소속 종목은 최상위 레코드만) 후
**산업별 캡**으로 분산을 강제한다 — 상위가 한두 산업으로 도배되는 것을 차단.
"""

from collections import Counter
from collections.abc import Mapping, Sequence

from trading.contracts.longterm import CandidateRecord, CyclePhase

# 발동 존(bottoming·recovering) 우선 — ENTRY_PHASES(rules.py)와 같은 철학의 정렬판.
# v2(2026-09-01): slowing(둔화 — 과열 조정 중) 신설, declining은 v1 레거시(동급 취급).
PHASE_PRIORITY: dict[CyclePhase, int] = {
    CyclePhase.BOTTOMING: 0,
    CyclePhase.RECOVERING: 1,
    CyclePhase.UNKNOWN: 2,
    CyclePhase.SLOWING: 3,
    CyclePhase.DECLINING: 3,
    CyclePhase.OVERHEATED: 4,
}

DEFAULT_TOP_N = 40
DEFAULT_PER_INDUSTRY_CAP = 5
# 이익수익률 1/15 ≈ 6.7% 미만은 "저PBR이어도 이익 기준으론 비싸다" — 강등(탈락 아님).
# 통과군 분포 p75=12.0이라 전 산업 중앙값(4.7~10.9)보다 위의 보수적 컷.
HIGH_PER_THRESHOLD = 15.0


def value_depth(rec: CandidateRecord) -> float:
    """가치 깊이 — 산업 내·시장 전체 percentile 중 더 깊은(낮은) 쪽. 둘 다 결측이면 1.0(최하위)."""
    vals = [v for v in (rec.industry_pbr_pct, rec.market_pbr_pct) if v is not None]
    return min(vals) if vals else 1.0


def high_per(
    rec: CandidateRecord,
    per_by_symbol: Mapping[str, float],
    threshold: float = HIGH_PER_THRESHOLD,
) -> bool:
    """이익 축 강등 판정 — PER이 알려져 있고 임계 초과일 때만 True(결측은 강등 안 함)."""
    per = per_by_symbol.get(rec.symbol)
    return per is not None and per > threshold


def rank_key(
    rec: CandidateRecord,
    roe_by_symbol: Mapping[str, float],
    per_by_symbol: Mapping[str, float] | None = None,
    high_per_threshold: float = HIGH_PER_THRESHOLD,
) -> tuple[int, int, float, float, str]:
    return (
        PHASE_PRIORITY.get(rec.phase, 9),
        1 if high_per(rec, per_by_symbol or {}, high_per_threshold) else 0,
        value_depth(rec),
        -(roe_by_symbol.get(rec.symbol) or 0.0),
        rec.symbol,
    )


def shortlist(
    records: Sequence[CandidateRecord],
    *,
    roe_by_symbol: Mapping[str, float] | None = None,
    per_by_symbol: Mapping[str, float] | None = None,
    top_n: int = DEFAULT_TOP_N,
    per_industry_cap: int = DEFAULT_PER_INDUSTRY_CAP,
    high_per_threshold: float = HIGH_PER_THRESHOLD,
) -> list[CandidateRecord]:
    """통과 후보의 분산 숏리스트 — 정렬 후 심볼 dedup + 산업별 캡. 게이트 불변(표시 순서만)."""
    roe = roe_by_symbol or {}
    per = per_by_symbol or {}
    ranked = sorted(
        (r for r in records if r.passed),
        key=lambda r: rank_key(r, roe, per, high_per_threshold),
    )
    seen: set[str] = set()
    per_industry: Counter[str] = Counter()
    out: list[CandidateRecord] = []
    for rec in ranked:
        if rec.symbol in seen or per_industry[rec.industry] >= per_industry_cap:
            continue
        seen.add(rec.symbol)
        per_industry[rec.industry] += 1
        out.append(rec)
        if len(out) >= top_n:
            break
    return out


__all__ = [
    "DEFAULT_PER_INDUSTRY_CAP",
    "DEFAULT_TOP_N",
    "HIGH_PER_THRESHOLD",
    "PHASE_PRIORITY",
    "high_per",
    "rank_key",
    "shortlist",
    "value_depth",
]
