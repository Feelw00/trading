"""R3 산업 사이클 온도계 — 순수 코드(설계서 v0.3 §3 R3). Phase 1: 히스토리 밴드(1차 축) 재료."""

from trading.cycle.bands import SectorYear, band_positions, build_sector_years

__all__ = ["SectorYear", "band_positions", "build_sector_years"]
