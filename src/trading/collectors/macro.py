"""거시지표 수집 오케스트레이터 — 결정론적 어댑터(FRED/ECOS) → SQLite landing.

``collect-macro`` 스킬이 ``python -m trading.collectors.macro`` 로 트리거(LLM은 데이터 미개입).
- 해외지수·유가: FRED(시리즈ID 확정, COLLECT-2).
- 금리·환율: ECOS — 통계코드 미확정(COLLECT-2) → 코드 미설정 항목은 ``blocked``(추측 금지).
  ECOS 키 확보 후 카탈로그(StatisticItemList)로 코드를 확정해 아래 레지스트리에 입력한다.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from trading.collectors.base import (
    CollectedFact,
    CollectError,
    default_db_path,
    now_kst,
    write_facts,
)
from trading.collectors.ecos import EcosClient
from trading.collectors.fred import FredClient

CLUSTER = "macro_indicators"


@dataclass(frozen=True)
class MacroItem:
    name: str
    metric: str
    asset_class: str
    source: str  # "FRED" | "ECOS"
    region: str | None = None
    unit: str | None = None
    # FRED
    series_id: str | None = None
    # ECOS — 통계코드 미확정(COLLECT-2). 확인 후 채우기 전까지 None → blocked.
    stat_code: str | None = None
    item_code: str | None = None
    cycle: str | None = None


# 해외지수·유가=FRED 시리즈ID 확정. 금리·환율=ECOS 통계표/항목 코드 확정
# (ECOS 카탈로그 StatisticTableList/StatisticItemList로 확인, 2026-06-08 — 추측 아님).
MACRO_ITEMS: tuple[MacroItem, ...] = (
    MacroItem("S&P500", "index_level", "index", "FRED", region="US", series_id="SP500"),
    MacroItem("NASDAQ", "index_level", "index", "FRED", region="US", series_id="NASDAQCOM"),
    MacroItem("SOX", "index_level", "index", "FRED", region="US", series_id="NASDAQSOX"),
    MacroItem("WTI", "oil", "macro", "FRED", unit="USD/bbl", series_id="DCOILWTICO"),
    MacroItem("Brent", "oil", "macro", "FRED", unit="USD/bbl", series_id="DCOILBRENTEU"),
    # ECOS: 731Y001 환율 / 817Y002 시장금리(일별) / 722Y001 한국은행 기준금리
    MacroItem("USD/KRW", "fx", "fx", "ECOS", region="KR", unit="KRW",
              stat_code="731Y001", item_code="0000001", cycle="D"),
    MacroItem("국고채3Y", "rate", "macro", "ECOS", region="KR", unit="%",
              stat_code="817Y002", item_code="010200000", cycle="D"),
    MacroItem("국고채10Y", "rate", "macro", "ECOS", region="KR", unit="%",
              stat_code="817Y002", item_code="010210000", cycle="D"),
    MacroItem("BOK기준금리", "rate", "macro", "ECOS", region="KR", unit="%",
              stat_code="722Y001", item_code="0101000", cycle="D"),
)


@dataclass
class Summary:
    collected: int = 0
    verified: int = 0
    blocked: list[str] = field(default_factory=list)
    facts: list[CollectedFact] = field(default_factory=list)


def collect_macro(
    fred: FredClient | None,
    ecos: EcosClient | None,
    *,
    items: Sequence[MacroItem] = MACRO_ITEMS,
) -> Summary:
    """각 항목을 승인 소스 어댑터로만 수집. 미설정·실패는 blocked(웹서치 대체 금지)."""
    summary = Summary()
    fetched = now_kst()
    end = fetched.strftime("%Y%m%d")
    start = (fetched - timedelta(days=14)).strftime("%Y%m%d")
    for it in items:
        try:
            if it.source == "FRED":
                if fred is None:
                    summary.blocked.append(f"{it.name}: FRED_API_KEY 미설정")
                    continue
                assert it.series_id is not None
                obs = fred.latest(it.series_id)
                if obs is None:
                    summary.blocked.append(f"{it.name}: FRED 데이터 없음")
                    continue
                as_of, value = obs
                summary.facts.append(
                    CollectedFact(
                        cluster=CLUSTER,
                        name=it.name,
                        metric=it.metric,
                        region=it.region,
                        asset_class=it.asset_class,
                        unit=it.unit,
                        value=value,
                        as_of=as_of,
                        fetched_at=fetched,
                        source=f"FRED:{it.series_id}",
                        verified=True,
                    )
                )
                summary.verified += 1
            elif it.source == "ECOS":
                if it.stat_code is None or it.item_code is None or it.cycle is None:
                    summary.blocked.append(f"{it.name}: ECOS 통계코드 미설정(COLLECT-2 확인 필요)")
                    continue
                if ecos is None:
                    summary.blocked.append(f"{it.name}: ECOS_API_KEY 미설정")
                    continue
                res = ecos.latest(it.stat_code, it.item_code, it.cycle, start, end)
                if res is None:
                    summary.blocked.append(f"{it.name}: ECOS 데이터 없음")
                    continue
                as_of, value, unit = res
                summary.facts.append(
                    CollectedFact(
                        cluster=CLUSTER,
                        name=it.name,
                        metric=it.metric,
                        region=it.region,
                        asset_class=it.asset_class,
                        unit=it.unit or unit,
                        value=value,
                        as_of=as_of,
                        fetched_at=fetched,
                        source=f"ECOS:{it.stat_code}/{it.item_code}",
                        verified=True,
                    )
                )
                summary.verified += 1
            else:
                summary.blocked.append(f"{it.name}: unknown source {it.source}")
        except CollectError as exc:
            summary.blocked.append(f"{it.name}: {exc}")
    summary.collected = len(summary.facts)
    return summary


def main() -> int:
    fred_key = os.environ.get("FRED_API_KEY", "")
    ecos_key = os.environ.get("ECOS_API_KEY", "")
    fred = FredClient(fred_key) if fred_key else None
    ecos = EcosClient(ecos_key) if ecos_key else None

    summary = collect_macro(fred, ecos)
    if summary.facts:
        path = default_db_path(CLUSTER)
        write_facts(path, summary.facts)
        print(f"적재 {summary.collected}건 (verified {summary.verified}) → {path}")
    else:
        print("적재 0건")
    if summary.blocked:
        print(f"blocked {len(summary.blocked)}건:")
        for item in summary.blocked:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
