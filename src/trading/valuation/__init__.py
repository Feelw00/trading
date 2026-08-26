"""R2 밸류에이션·환원 계산기 — 순수 코드(설계서 v0.3 §3 R2). LLM 미개입."""

from trading.valuation.build import build_valuation_records
from trading.valuation.metrics import Metrics, derive_metrics, loss_years, percentile_rank
from trading.valuation.store import ValuationStore

__all__ = [
    "Metrics",
    "ValuationStore",
    "build_valuation_records",
    "derive_metrics",
    "loss_years",
    "percentile_rank",
]
