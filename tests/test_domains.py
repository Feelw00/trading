"""도메인 taxonomy(29섹터) + FactRecord 도메인 제약 테스트."""

from typing import Any

import pytest
from pydantic import ValidationError

from trading.contracts.fact import FactRecord
from trading.domains import SECTORS, Sector, active_sectors


def test_sector_count_is_29() -> None:
    # 26 → 29: P-1 확장(해운·물류/운송/레저·카지노, 2026-07-11)
    assert len(Sector) == 29
    assert len(SECTORS) == 29


def test_only_semiconductor_active() -> None:
    assert active_sectors() == [Sector.SEMICONDUCTOR]


def test_every_sector_has_meta() -> None:
    for sector in Sector:
        meta = SECTORS[sector]
        assert meta.label_ko
        assert meta.group


def test_sector_fact_requires_sector(fact_kwargs: dict[str, Any]) -> None:
    fact_kwargs["asset_class"] = "sector"
    fact_kwargs["sector"] = []  # 비어 있으면 거부
    with pytest.raises(ValidationError):
        FactRecord(**fact_kwargs)


def test_non_sector_fact_rejects_sector(fact_kwargs: dict[str, Any]) -> None:
    fact_kwargs["asset_class"] = "index"
    fact_kwargs["sector"] = ["semiconductor"]  # index인데 섹터 → 거부
    with pytest.raises(ValidationError):
        FactRecord(**fact_kwargs)


def test_valid_sector_fact(fact_kwargs: dict[str, Any]) -> None:
    fact_kwargs["asset_class"] = "sector"
    fact_kwargs["sector"] = ["semiconductor", "ai_software"]
    fact = FactRecord(**fact_kwargs)
    assert Sector.SEMICONDUCTOR in fact.sector
    assert len(fact.sector) == 2
