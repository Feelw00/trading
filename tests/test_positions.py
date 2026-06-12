"""포지션 관리(P-8) — 계약·스토어·점검(순수 코드)·CLI·arm-check/저녁보고 통합."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading import position_check, positions as positions_cli
from trading.contracts.position import PositionRecord, PositionStatus
from trading.journal.positions import PositionStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 12, 10, 0, tzinfo=KST)


def _pos(**over: Any) -> PositionRecord:
    base: dict[str, Any] = {
        "id": "pos.20260612.095610.buy",
        "as_of": NOW, "fetched_at": NOW, "source": "operator:manual",
        "symbol": "095610", "qty": 5, "avg_price": 196300.0,
        "hypothesis": "long 편향 — 기관 실매집",
        "invalidation_text": "종가 160,000 하회",
        "stop_level": 160000.0, "time_stop_days": 10, "confidence": 0.35,
        "plan_doc": "# 테스 토론\n...",
        "source_ref": "discuss:테스 v1",
    }
    base.update(over)
    return PositionRecord(**base)


# --- 계약 ---


def test_position_contract_validates() -> None:
    p = _pos()
    assert p.status is PositionStatus.OPEN and p.qty == 5


def test_position_rejects_bad_values() -> None:
    with pytest.raises(ValidationError):
        _pos(qty=0)
    with pytest.raises(ValidationError):
        _pos(avg_price=-1)
    with pytest.raises(ValidationError):
        _pos(confidence=1.5)


# --- 스토어 (append-only) ---


def test_store_roundtrip_and_close_versioning(tmp_path: Path) -> None:
    ps = PositionStore(tmp_path / "pos.sqlite")
    assert ps.append(_pos()) == 1
    assert len(ps.open_positions()) == 1
    closed = _pos().model_copy(update={"status": PositionStatus.CLOSED, "close_reason": "시간손절"})
    assert ps.append(closed) == 2          # 전이는 새 version
    assert ps.open_positions() == []       # 최신 version=closed → open 목록에서 제외
    latest = ps.get("pos.20260612.095610.buy")
    assert latest is not None and latest.close_reason == "시간손절"
    ps.close()


# --- 점검 (순수 코드) ---


class _Kis:
    def __init__(self, price: str) -> None:
        self._p = price

    def quote_ccnl(self, srtn_cd: str) -> dict[str, Any]:
        return {"stck_prpr": self._p}


class _Market:
    def nth_recent_date(self, n: int) -> str:
        return "20260610"

    def series_for(self, srtn_cd: str, cutoff: str) -> list[tuple[Any, ...]]:
        return [("095610", "테스", "KOSDAQ", "20260610", "173400", "176000", "", "", "")]

    def close(self) -> None:
        pass


def test_check_realtime_pnl_and_stop_distance(tmp_path: Path) -> None:
    store = PositionStore(tmp_path / "pos.sqlite")
    store.append(_pos())
    [v] = position_check.check_positions(
        now=NOW, position_store=store, kis_client=_Kis("186700"),  # type: ignore[arg-type]
        market_store=_Market(),  # type: ignore[arg-type]
    )
    store.close()
    assert v.current == 186700.0 and v.price_as_of == "실시간"
    assert v.pnl_pct is not None and round(v.pnl_pct, 1) == -4.9
    assert v.stop_distance_pct is not None and v.stop_distance_pct > 0
    assert not v.stop_breached and not v.review_needed
    assert v.time_stop_expiry is not None and v.trading_days_left == 10


def test_check_stop_breach_flags_review(tmp_path: Path) -> None:
    store = PositionStore(tmp_path / "pos.sqlite")
    store.append(_pos())
    [v] = position_check.check_positions(
        now=NOW, position_store=store, kis_client=_Kis("159000"),  # type: ignore[arg-type]
        market_store=_Market(),  # type: ignore[arg-type]
    )
    store.close()
    assert v.stop_breached and v.review_needed
    assert any("이탈" in ln for ln in position_check.render_lines([v]))


def test_check_time_stop_due_flags_review(tmp_path: Path) -> None:
    store = PositionStore(tmp_path / "pos.sqlite")
    store.append(_pos())
    far = datetime(2026, 7, 10, 10, 0, tzinfo=KST)  # 10거래일 한참 후
    [v] = position_check.check_positions(
        now=far, position_store=store, kis_client=_Kis("186700"),  # type: ignore[arg-type]
        market_store=_Market(),  # type: ignore[arg-type]
    )
    store.close()
    assert v.time_stop_due and v.review_needed


def test_check_eod_fallback_when_kis_fails(tmp_path: Path) -> None:
    class _Boom:
        def quote_ccnl(self, srtn_cd: str) -> dict[str, Any]:
            raise OSError("down")

    store = PositionStore(tmp_path / "pos.sqlite")
    store.append(_pos())
    [v] = position_check.check_positions(
        now=NOW, position_store=store, kis_client=_Boom(),  # type: ignore[arg-type]
        market_store=_Market(),  # type: ignore[arg-type]
    )
    store.close()
    assert v.current == 173400.0 and v.price_as_of.startswith("EOD")


# --- CLI ---


def test_cli_add_list_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    db = tmp_path / "pos.sqlite"
    monkeypatch.setattr("trading.positions.PositionStore", lambda: PositionStore(db))
    monkeypatch.setattr(
        "trading.position_check.kis_from_env", lambda: _Kis("186700")
    )
    monkeypatch.setattr("trading.position_check.MarketStore", lambda: _Market())
    rc = positions_cli.run([
        "add", "--symbol", "095610", "--qty", "5", "--price", "196300",
        "--stop", "160000", "--time-stop", "10", "--invalidation", "종가 160000 하회",
    ])
    assert rc == 0 and "등록" in capsys.readouterr().out
    rc = positions_cli.run([])
    out = capsys.readouterr().out
    assert rc == 0 and "보유 1건" in out and "스탑 160,000" in out
    pos_id = "pos." + f"{datetime.now(KST):%Y%m%d}" + ".095610.buy"
    rc = positions_cli.run(["close", pos_id, "--reason", "임의 정리"])
    assert rc == 0 and "정리" in capsys.readouterr().out
    rc = positions_cli.run([])
    assert "보유 포지션 없음" in capsys.readouterr().out


def test_cli_add_without_exit_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    db = tmp_path / "pos.sqlite"
    monkeypatch.setattr("trading.positions.PositionStore", lambda: PositionStore(db))
    rc = positions_cli.run(["add", "--symbol", "095610", "--qty", "1", "--price", "1000"])
    out = capsys.readouterr().out
    assert rc == 0 and "출구 없는 포지션" in out
