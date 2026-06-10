"""R1 일반 FactRecord 게이트 — 신선도(stale)·정합성(conflict) 순수 코드 단위 테스트.

M2 AC: stale 입력이 R5 입력 게이트에서 차단 / 충돌 데이터가 평균·임의 선택되지 않고
제외 마킹 / 소스별 신선도 허용치 설정 주입.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.contracts.fact import FactFlag, FactRecord
from trading.gates.facts import (
    FactGateConfig,
    GateError,
    apply_flags,
    gate_facts,
    require_decision_grade,
    split_decision_inputs,
    summarize,
)

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 10, 16, 0, tzinfo=KST)


def _fact(**over: Any) -> FactRecord:
    base: dict[str, Any] = {
        "id": "ecos.fx.usdkrw.2026-06-10",
        "region": "KR",
        "asset_class": "fx",
        "metric": "usdkrw_basis_rate",
        "value": 1518.4,
        "as_of": NOW - timedelta(hours=4),
        "fetched_at": NOW,
        "source": "ECOS:731Y001/0000001",
    }
    base.update(over)
    return FactRecord(**base)


class _SpyHook:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, message: str, *, severity: str, context: Any) -> None:
        self.calls.append({"message": message, "severity": severity, "context": dict(context)})


# --- 신선도 ---


def test_fresh_fact_no_flags() -> None:
    [v] = gate_facts([_fact()], now=NOW)
    assert v.flags == frozenset()
    assert v.fresh and v.decision_usable


def test_stale_fact_flagged_not_discarded() -> None:
    old = _fact(as_of=NOW - timedelta(hours=200))
    [v] = gate_facts([old], now=NOW)
    assert FactFlag.STALE in v.flags
    assert not v.fresh
    # 폐기 아님 — 레코드는 verdict에 그대로 보존
    assert v.record is old


def test_future_dated_flagged() -> None:
    [v] = gate_facts([_fact(as_of=NOW + timedelta(hours=2))], now=NOW)
    assert FactFlag.FUTURE_DATED in v.flags
    assert not v.fresh


def test_future_within_skew_ok() -> None:
    [v] = gate_facts([_fact(as_of=NOW + timedelta(minutes=30))], now=NOW)
    assert v.flags == frozenset()


def test_per_metric_tolerance_override() -> None:
    # 유가류 구조적 공표 지연 — per_metric 완화 주입 시 stale 아님
    old = _fact(
        id="fred.oil.wti.2026-06-01",
        asset_class="macro",
        metric="wti_spot_usd",
        as_of=NOW - timedelta(days=9),
        source="FRED:DCOILWTICO",
    )
    cfg = FactGateConfig(per_metric={"wti_spot_usd": 336.0})
    [v] = gate_facts([old], now=NOW, config=cfg)
    assert v.flags == frozenset()
    # 동일 입력, 기본 허용치(96h)면 stale
    [v2] = gate_facts([old], now=NOW)
    assert FactFlag.STALE in v2.flags


def test_per_source_tolerance_wins_over_metric() -> None:
    old = _fact(as_of=NOW - timedelta(hours=120))
    cfg = FactGateConfig(
        per_metric={"usdkrw_basis_rate": 96.0},
        per_source={"ECOS:731Y001/0000001": 144.0},
    )
    [v] = gate_facts([old], now=NOW, config=cfg)
    assert v.flags == frozenset()


def test_config_from_file(tmp_path: Path) -> None:
    p = tmp_path / "r1.json"
    p.write_text(
        json.dumps(
            {
                "default_max_age_hours": 48,
                "per_metric": {"wti_spot_usd": 336},
                "per_source": {"ECOS:731Y001/0000001": 144},
                "conflict_threshold_pct": 1.0,
            }
        ),
        encoding="utf-8",
    )
    cfg = FactGateConfig.from_file(p)
    assert cfg.default_max_age_hours == 48.0
    assert cfg.per_metric["wti_spot_usd"] == 336.0
    assert cfg.per_source["ECOS:731Y001/0000001"] == 144.0
    assert cfg.conflict_threshold_pct == 1.0


# --- 정합성 (이중 소스 conflict) ---


def _dual_source_pair(v1: float, v2: float, **over: Any) -> list[FactRecord]:
    a = _fact(id="src-a", value=v1, source="ECOS:731Y001/0000001", **over)
    b = _fact(id="src-b", value=v2, source="OTHER:usdkrw", **over)
    return [a, b]


def test_dual_source_conflict_flags_whole_group_and_alerts() -> None:
    hook = _SpyHook()
    records = _dual_source_pair(1518.4, 1540.0)  # ~1.4% 괴리 > 0.5%
    verdicts = gate_facts(records, now=NOW, alert_hook=hook)
    assert all(FactFlag.CONFLICT in v.flags for v in verdicts)
    assert len(hook.calls) == 1
    assert hook.calls[0]["severity"] == "P1"
    assert hook.calls[0]["context"]["metric"] == "usdkrw_basis_rate"
    # 평균·합성값 생성 없음 — 원본 값 그대로 보존
    assert sorted(v.record.value for v in verdicts) == [1518.4, 1540.0]


def test_dual_source_within_threshold_no_conflict() -> None:
    hook = _SpyHook()
    verdicts = gate_facts(_dual_source_pair(1518.4, 1519.0), now=NOW, alert_hook=hook)
    assert all(not v.flags for v in verdicts)
    assert hook.calls == []


def test_single_source_never_conflicts() -> None:
    records = [
        _fact(id="a", value=1518.4),
        _fact(id="b", value=1600.0),  # 같은 source — 이중 소스 아님
    ]
    verdicts = gate_facts(records, now=NOW)
    assert all(FactFlag.CONFLICT not in v.flags for v in verdicts)


def test_non_critical_class_never_conflicts() -> None:
    # conflict는 핵심 지표(환율·지수) 한정 — macro(금리 등)는 괴리해도 비대상
    records = _dual_source_pair(3.8, 4.6, asset_class="macro", metric="ktb_3y_yield")
    verdicts = gate_facts(records, now=NOW)
    assert all(FactFlag.CONFLICT not in v.flags for v in verdicts)


def test_different_as_of_dates_not_compared() -> None:
    a = _fact(id="a", value=1518.4)
    b = _fact(
        id="b", value=1545.0, source="OTHER:usdkrw", as_of=NOW - timedelta(days=1)
    )
    verdicts = gate_facts([a, b], now=NOW)
    assert all(FactFlag.CONFLICT not in v.flags for v in verdicts)


# --- 의사결정 제외 / R5 하드 게이트 ---


def test_split_excludes_conflicted_without_picking() -> None:
    records = _dual_source_pair(1518.4, 1540.0) + [
        _fact(id="clean", metric="kospi_close", asset_class="index", value=8096.93)
    ]
    split = split_decision_inputs(gate_facts(records, now=NOW))
    assert [r.id for r in split.usable] == ["clean"]
    # 충돌 쌍은 어느 쪽도 선택되지 않고 둘 다 제외 마킹
    assert sorted(v.record.id for v in split.excluded) == ["src-a", "src-b"]


def test_r5_hard_gate_blocks_stale_input() -> None:
    stale = _fact(as_of=NOW - timedelta(days=10))
    verdicts = gate_facts([stale, _fact(id="ok")], now=NOW)
    with pytest.raises(GateError, match="stale"):
        require_decision_grade(verdicts)


def test_r5_hard_gate_blocks_conflict_input() -> None:
    verdicts = gate_facts(_dual_source_pair(1518.4, 1540.0), now=NOW)
    with pytest.raises(GateError, match="conflict"):
        require_decision_grade(verdicts)


def test_r5_hard_gate_passes_clean_inputs() -> None:
    verdicts = gate_facts([_fact(), _fact(id="x2", metric="kosdaq_close",
                                          asset_class="index", value=967.81)], now=NOW)
    records = require_decision_grade(verdicts)
    assert len(records) == 2


# --- 플래그 부착(새 버전 레코드) / 집계 ---


def test_apply_flags_returns_new_version_record() -> None:
    stale = _fact(as_of=NOW - timedelta(days=10))
    [v] = gate_facts([stale], now=NOW)
    flagged = apply_flags(v)
    assert flagged.flags == [FactFlag.STALE]
    assert stale.flags == []  # 원본 불변(frozen) — append-only 새 버전
    assert flagged.id == stale.id


def test_summarize_counts() -> None:
    records = _dual_source_pair(1518.4, 1540.0) + [
        _fact(id="stale1", as_of=NOW - timedelta(days=10)),
        _fact(id="clean", metric="kospi_close", asset_class="index", value=8096.93),
    ]
    s = summarize(gate_facts(records, now=NOW))
    assert s["total"] == 4
    assert s["conflict"] == 2
    assert s["stale"] == 1
    assert s["decision_usable"] == 1
