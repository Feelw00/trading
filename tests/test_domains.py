"""도메인 taxonomy(26섹터) + FactRecord 도메인 제약 테스트."""

from typing import Any

import pytest
from pydantic import ValidationError

from trading.contracts.fact import FactRecord
from trading.domains import SECTORS, Sector, active_sectors


def test_sector_count_is_26() -> None:
    assert len(Sector) == 26
    assert len(SECTORS) == 26


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
