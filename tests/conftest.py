"""테스트 공용 — 각 계약의 유효 kwargs 빌더(테스트마다 fresh dict)."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

KST = ZoneInfo("Asia/Seoul")
AS_OF = datetime(2026, 6, 8, 18, 0, tzinfo=KST)
FETCHED = datetime(2026, 6, 8, 18, 5, tzinfo=KST)


@pytest.fixture
def fact_kwargs() -> dict[str, Any]:
    return {
        "id": "krx.flows.foreign.2026-06-08",
        "region": "KR",
        "asset_class": "index",
        "metric": "kospi_foreign_net_buy_krw",
        "value": -1234500000000.0,
        "as_of": AS_OF,
        "fetched_at": FETCHED,
        "source": "sample_fake",
    }


@pytest.fixture
def event_kwargs() -> dict[str, Any]:
    return {
        "id": "evt.2026-06-08.0142",
        "type": "flow_anomaly",
        "entities": ["SK하이닉스"],
        "summary_1line": "외국인 순매도 가속",
        "evidence": ["krx.flows.foreign.2026-06-08"],
        "market_scope": ["KR.semis"],
        "as_of": AS_OF,
        "fetched_at": FETCHED,
        "source": "sample_fake",
    }


@pytest.fixture
def thesis_kwargs() -> dict[str, Any]:
    return {
        "id": "thesis.2026-06-08.supply.01",
        "persona": "supply",
        "thesis": "반대매매 소진 후 스윙 반등",
        "direction": "long",
        "instrument_class": "KR.semis.large",
        "trigger": "익일 시초 갭다운 후 30분 내 저점 미이탈",
        "invalidation": "플러시 저점 종가 이탈",
        "horizon_days": 5,
        "confidence": 0.55,
        "as_of": AS_OF,
        "fetched_at": FETCHED,
        "source": "sample_fake",
    }


@pytest.fixture
def playbook_kwargs() -> dict[str, Any]:
    return {
        "id": "pb.2026-06-09.hynix.flush_long",
        "thesis_ref": "thesis.2026-06-08.supply.01",
        "arm_conditions": {"gap_pct": "<-3.0", "premkt_volume_rank": "<=20"},
        "abort_conditions": {"new_low_after": "09:30"},
        "order_draft_ref": "order.2026-06-09.hynix.long",
        "as_of": AS_OF,
        "fetched_at": FETCHED,
        "source": "sample_fake",
    }


@pytest.fixture
def order_kwargs() -> dict[str, Any]:
    return {
        "id": "order.2026-06-09.hynix.long",
        "symbol": "000660",
        "side": "buy",
        "tranches": [
            {"label": "impatience_fee", "pct_of_plan": 20, "order_type": "limit"},
            {"label": "flush", "pct_of_plan": 50, "order_type": "limit"},
            {"label": "confirmation", "pct_of_plan": 30, "condition": "prev_day_high_reclaim"},
        ],
        "total_size_cap": "0.5 * normal_unit",
        "stop": {"type": "conditional_order_at_broker", "level": None},
        "time_stop_days": 5,
        "created_when_market": "closed",
        "as_of": AS_OF,
        "fetched_at": FETCHED,
        "source": "sample_fake",
    }
