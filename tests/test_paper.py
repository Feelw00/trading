"""페이퍼 투자(v2.5) — 트리거 재생·멱등·수익률 테스트."""

import sqlite3
from pathlib import Path

import pytest

from trading.paper import (
    PaperParams,
    PaperStore,
    current_targets,
    enroll_holding,
    mark,
    target_drift,
)


def _market(tmp_path: Path, closes: list[tuple[str, float]]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "market.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE daily_quotes (srtn_cd TEXT, bas_dt TEXT, clpr TEXT)")
    conn.executemany(
        "INSERT INTO daily_quotes VALUES ('000001',?,?)",
        [(d, str(c)) for d, c in closes],
    )
    conn.commit(); conn.close()
    return db


def test_ladder_buys_and_target_exit(tmp_path: Path) -> None:
    params = PaperParams(initial_qty=40.0,
                         add_levels=((-0.10, 30.0),), sell_levels=((0.90, 0.5),),
                         final_exit_multiple=1.15, time_horizon_days=1095)
    store = PaperStore(tmp_path / "p.sqlite")
    # 기준 1000 → 목표 1500. 900 이하 추가 매수, 1350(목표가 90%) 절반 매도, 1725(115%) 정리
    store.open_position("000001", "20260101", 1000.0, 1500.0, params)
    db = _market(tmp_path, [("20260101", 1000), ("20260102", 890), ("20260103", 1360),
                            ("20260104", 1730)])
    views = mark(store, market_db=db)
    v = views[0]
    fills = store.fills("000001")
    assert [f.trigger for f in fills] == ["1차 매수(초기)", "2차 매수(-10%)", "목표가 90% 매도", "정리(목표가 115%)"]
    assert v.status == "closed" and v.qty == 0 and v.next_buy is None
    # 수익: 40주@1000 + 30주@890 → 1360에 35주(절반 내림), 잔량 35주 1730 정리
    assert v.pnl_pct is not None and v.pnl_pct > 0.4
    # 멱등 — 재마킹해도 체결 불변
    mark(store, market_db=db)
    assert len(store.fills("000001")) == 4
    store.close()


def test_no_triggers_holds_with_guides(tmp_path: Path) -> None:
    params = PaperParams()
    store = PaperStore(tmp_path / "p.sqlite")
    store.open_position("000001", "20260101", 1000.0, 2000.0, params)
    db = _market(tmp_path, [("20260101", 1000), ("20260102", 980)])
    v = mark(store, market_db=db)[0]
    assert v.status == "open" and v.next_buy is None and v.next_sell is not None
    assert v.qty == 100.0                # v2.8: 등록 시 100주 일괄
    assert v.next_sell[1] == 1600.0      # 목표가 80%에서 첫 매도(20주)
    assert v.final_exit_price == 3000.0  # 정리 = 목표가 150%
    # 상한 = min(기준가→목표가 1/3 지점 1333.3, 첫 매도가 1600) — 목표가 앵커
    assert v.buy_ceiling is not None and abs(v.buy_ceiling - 4000 / 3) < 1e-6
    assert v.in_buy_zone                 # 980 < 상한 → 매수 가능
    assert v.pnl_pct is not None and abs(v.pnl_pct + 0.02) < 1e-9
    store.close()


def test_profit_protection_floor(tmp_path: Path) -> None:
    """v2.9: 90% 이상 매도선 터치 후 종가가 직전 매도선 아래로 → 잔량 전량 정리."""
    params = PaperParams()
    store = PaperStore(tmp_path / "p.sqlite")
    store.open_position("000001", "20260101", 1000.0, 2000.0, params)
    db = _market(tmp_path, [
        ("20260101", 1000),
        ("20260201", 1650),   # 80% 선(1600) → 20주 매도
        ("20260301", 1850),   # 90% 선(1800) → 20주 매도(잔여 60주)
        ("20260401", 1590),   # 80% 선 이탈 → 이익 보호 정리(60주)
    ])
    v = mark(store, market_db=db)[0]
    trig = [f.trigger for f in store.fills("000001")]
    assert trig == ["1차 매수(초기)", "목표가 80% 매도", "목표가 90% 매도", "이익 보호 정리"]
    assert v.status == "closed" and v.qty == 0.0
    assert v.closed_reason is not None and "90% 터치 후 80% 선 이탈" in v.closed_reason
    # 실현 165,400 / 투입 100,000 — 왕복인데도 +65.4% 보전
    assert v.pnl_pct is not None and abs(v.pnl_pct - 0.654) < 1e-9
    store.close()


def test_profit_protection_not_armed_at_80(tmp_path: Path) -> None:
    """v2.9: 첫 매도선(80%)만 터치하고 되밀리면 미발동 — 정상 보유 구간."""
    params = PaperParams()
    store = PaperStore(tmp_path / "p.sqlite")
    store.open_position("000001", "20260101", 1000.0, 2000.0, params)
    db = _market(tmp_path, [("20260101", 1000), ("20260201", 1650), ("20260301", 1200)])
    v = mark(store, market_db=db)[0]
    assert v.status == "open" and v.qty == 80.0  # 80% 매도 20주 후 보유 지속
    store.close()


def test_reentry_new_cycle_after_close(tmp_path: Path) -> None:
    """v2.10: 청산 후 재등록 = 새 사이클 — 과거 사이클 체결 원장과 섞이지 않는다."""
    params = PaperParams()
    store = PaperStore(tmp_path / "p.sqlite")
    store.open_position("000001", "20260101", 1000.0, 2000.0, params)
    db = _market(tmp_path, [
        ("20260101", 1000), ("20260201", 1650), ("20260301", 1850), ("20260401", 1590),
    ])
    mark(store, market_db=db)
    assert store.latest_positions()[0].status == "closed"
    store.open_position("000001", "20260501", 1100.0, 2200.0, params)
    pos = store.latest_positions()[0]
    assert pos.status == "open" and pos.cycle == 1
    db2 = _market(tmp_path / "m2", [
        ("20260101", 1000), ("20260201", 1650), ("20260301", 1850), ("20260401", 1590),
        ("20260501", 1100),
    ])
    v = mark(store, market_db=db2)[0]
    assert v.cycle == 1 and v.qty == 100.0 and v.invested == 110_000.0
    assert len(store.fills("000001", 0)) == 4  # 1사이클 이력 보존(매수 1 + 매도 3)
    assert [f.trigger for f in store.fills("000001", 1)] == ["1차 매수(초기)"]
    store.close()


_LEGACY_LADDER = PaperParams(  # v2.5~v2.7.1 분할 매수 — 엔진은 저장 파라미터 주도라 유지 검증
    initial_qty=25.0,
    add_levels=((-0.10, 25.0), (-0.20, 25.0), (-0.30, 25.0)),
    sell_levels=((0.90, 1 / 3), (1.00, 1 / 2)),
    final_exit_multiple=1.15,
)


def test_time_ladder_buys_without_dip(tmp_path: Path) -> None:
    """레거시 파라미터: 주가가 안 빠져도 8주 경과 + 저평가 유지면 다음 트랜치 매수."""
    params = _LEGACY_LADDER
    store = PaperStore(tmp_path / "p.sqlite")
    # 기준 1000 → 목표 2000(여력 100%). 가격은 1000~1050 횡보(하락 트리거 미발동)
    store.open_position("000001", "20260101", 1000.0, 2000.0, params)
    db = _market(tmp_path, [
        ("20260101", 1000), ("20260210", 1020),           # 40일 — 미발동
        ("20260301", 1030),                                # 59일 경과 → 2차 시간 매수
        ("20260315", 1040),                                # 14일 — 미발동
        ("20260501", 1050),                                # 2차 후 61일 → 3차 시간 매수
    ])
    views = mark(store, market_db=db)
    fills = store.fills("000001")
    trig = [f.trigger for f in fills]
    assert trig == ["1차 매수(초기)", "2차 매수(-10%)", "3차 매수(-20%)"]
    assert fills[1].bas_dt == "20260301" and fills[1].price == 1030.0  # 시간 체결(시가 기준)
    assert views[0].qty == 75.0


def test_time_ladder_stops_above_buy_zone(tmp_path: Path) -> None:
    """레거시 파라미터: 진행률 ≥ 1/3(매수 존 이탈)이면 시간 매수 중단, 존 안이면 지속."""
    params = _LEGACY_LADDER
    store = PaperStore(tmp_path / "p.sqlite")
    # 기준 1000 → 목표 2000 → 정리 2300. 매수 존 상한 = 1000 + (2300-1000)/3 ≈ 1433
    store.open_position("000001", "20260101", 1000.0, 2000.0, params)
    db = _market(tmp_path, [("20260101", 1000), ("20260401", 1500)])  # 진행률 38% — 존 밖
    mark(store, market_db=db)
    assert [f.trigger for f in store.fills("000001")] == ["1차 매수(초기)"]
    # 존 안(1400, 진행률 31%)으로 복귀 + 8주 경과 → 지속 매수 재개(진입 창 없음)
    store2 = PaperStore(tmp_path / "p2.sqlite")
    store2.open_position("000001", "20260101", 1000.0, 2000.0, params)
    db2 = _market(tmp_path / "m2", [("20260101", 1000), ("20260901", 1400)])
    mark(store2, market_db=db2)
    assert [f.trigger for f in store2.fills("000001")] == ["1차 매수(초기)", "2차 매수(-10%)"]


def test_enroll_holding_uses_avg_price_and_estimated_target(tmp_path: Path) -> None:
    """운영자 지시(2026-09-02): 페이퍼 편입은 실투자 — 시작가 = 실평단, 목표 = 편입 시점 추정."""
    store = PaperStore(tmp_path / "p.sqlite")
    targets = {"000001": ("20260901", 1000.0, 1500.0)}
    line = enroll_holding(store, "000001", 950.0, targets=targets)
    assert line is not None and "시작가 950" in line and "1,500" in line
    pos = store.latest_positions()[0]
    assert (pos.base_price, pos.target_price, pos.opened_bas_dt) == (950.0, 1500.0, "20260901")
    # 이미 open → 중복 편입 없음 · 평단/추정 목표 결측 → 편입 불가(None, 호출자가 P1)
    assert enroll_holding(store, "000001", 950.0, targets=targets) is None
    assert enroll_holding(store, "000002", None, targets=targets) is None
    assert enroll_holding(store, "000002", 900.0, targets={"000002": None}) is None
    assert len(store.latest_positions()) == 1
    store.close()


def test_current_targets_and_drift_alert(tmp_path: Path) -> None:
    """추정 목표가 = 최근 종가 × (1 + 회귀 여력) · 등록 목표 대비 ±15% 이상이면 ⚠(표기만)."""
    db = _market(tmp_path, [("20260101", 1000), ("20260102", 1100)])
    tg = current_targets(["000001", "000009"], market_db=db,
                         upside={"000001": 50.0, "000009": 20.0})
    got = tg["000001"]
    assert got is not None and got[0] == "20260102" and got[1] == 1100.0
    assert got[2] == pytest.approx(1650.0)
    assert tg["000009"] is None                        # 시세 없음 → 결측(지어내지 않음)
    store = PaperStore(tmp_path / "p.sqlite")
    store.open_position("000001", "20260101", 1000.0, 1400.0, PaperParams())
    views = mark(store, market_db=db)
    (d,) = target_drift(views, tg)
    assert d.registered == 1400.0 and d.pct == pytest.approx(17.857, abs=0.01) and d.alert
    (d2,) = target_drift(views, {"000001": ("20260102", 1100.0, 1500.0)})
    assert d2.pct == pytest.approx(7.143, abs=0.01) and not d2.alert
    assert target_drift(views, {"000001": None}) == []
    store.close()


def test_close_appends_closed_version_and_keeps_fills(tmp_path: Path) -> None:
    # 운영자 실정리(2026-09-03): 실보유가 사라진 가이드는 삭제(unregister)가 아니라 closed 새 버전으로
    store = PaperStore(tmp_path / "p.sqlite")
    params = PaperParams()
    store.open_position("000001", "20260101", 1000.0, 1500.0, params)
    store.add_fill("000001", 0, "1차 매수(초기)", "buy", "20260101", 1000.0, 100.0, 100000.0)
    store.close_position("000001", "운영자 실정리")
    pos = next(p for p in store.latest_positions() if p.symbol == "000001")
    assert pos.status == "closed" and pos.closed_reason == "운영자 실정리"
    versions = store._conn.execute(
        "SELECT version, status FROM positions WHERE symbol='000001' ORDER BY version"
    ).fetchall()
    assert versions == [(1, "open"), (2, "closed")]          # append-only, 이력 보존
    assert len(store.fills("000001", 0)) == 1                 # 체결 유지
    store.close_position("000001", "중복 호출")               # 이미 closed — 무동작
    assert store._conn.execute(
        "SELECT COUNT(*) FROM positions WHERE symbol='000001'"
    ).fetchone()[0] == 2
    store.close()
