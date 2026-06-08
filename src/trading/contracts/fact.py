"""FactRecord — R0 수집 결과(정량 사실). 설계서 §4."""

from enum import Enum

from pydantic import Field, model_validator

from trading.contracts.base import BaseRecord, NonEmptyStr
from trading.domains import AssetClass, Region, Sector


class FactFlag(str, Enum):
    """R1 게이트가 부착하는 플래그(설계서 §3 R1)."""

    STALE = "stale"
    CONFLICT = "conflict"


class FactRecord(BaseRecord):
    region: Region
    asset_class: AssetClass
    sector: list[Sector] = Field(default_factory=list)
    metric: NonEmptyStr
    value: float
    flags: list[FactFlag] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sector_consistency(self) -> "FactRecord":
        # sector 클래스 사실은 섹터 ≥1, 그 외 클래스는 섹터가 비어 있어야 함
        if self.asset_class is AssetClass.SECTOR and not self.sector:
            raise ValueError("asset_class=sector requires at least one sector")
        if self.asset_class is not AssetClass.SECTOR and self.sector:
            raise ValueError("sector must be empty unless asset_class=sector")
        return self


__all__ = ["FactFlag", "FactRecord"]
