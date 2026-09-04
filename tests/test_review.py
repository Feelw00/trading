"""심사 원장(v2.4) — 3상태·만료·태그 규율·규칙 승격 카운트 테스트."""

from pathlib import Path

import pytest

from trading.review import RULE_PROMOTION_THRESHOLD, ReviewStore


def test_decide_and_current(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "r.sqlite")
    store.decide("007370", "vetoed", basis_year="2025", tags=["이익질"], note="투자부동산 평가익")
    rec = store.current("007370", "2025")
    assert rec is not None and rec["verdict"] == "vetoed" and "이익질" in rec["tags"]
    store.close()


def test_expiry_on_new_annual_year(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "r.sqlite")
    store.decide("067900", "hold", basis_year="2025", condition="증설 효과 확인")
    assert store.current("067900", "2025") is not None
    # 2026 연간 적재 → 판정 만료(pending 복귀)
    assert store.current("067900", "2026") is None
    # 재판정은 새 버전 append
    store.decide("067900", "approved", basis_year="2026")
    rec = store.current("067900", "2026")
    assert rec is not None and rec["verdict"] == "approved"
    store.close()


def test_reset_returns_to_pending_and_keeps_history(tmp_path: Path) -> None:
    # v2.13(운영자 지시 2026-09-03): 산식 변경 후 "종목 선정부터 다시" — 판정을 지우지 않고 초기화
    store = ReviewStore(tmp_path / "r.sqlite")
    store.decide("003800", "approved", basis_year="2025", note="배당 7.4%")
    v = store.reset("003800", basis_year="2025", reason="policy v2.13 재심사")
    assert v == 2
    assert store.current("003800", "2025") is None                 # pending 복귀
    assert "003800" not in store.all_current("2025")
    rows = store._conn.execute(
        "SELECT version, verdict FROM reviews WHERE symbol='003800' ORDER BY version"
    ).fetchall()
    assert rows == [(1, "approved"), (2, "reset")]                 # 이력 보존(append-only)
    # 초기화 후 재판정은 v3 — 유효 판정 복귀
    store.decide("003800", "hold", basis_year="2025", condition="회귀 여력 +30% 회복")
    rec = store.current("003800", "2025")
    assert rec is not None and rec["verdict"] == "hold"
    with pytest.raises(ValueError):
        store.reset("003800", basis_year="2025", reason="  ")     # 사유 필수
    with pytest.raises(ValueError):
        store.decide("003800", "reset", basis_year="2025")          # decide()로는 못 쓴다
    store.close()


def test_verdict_discipline(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "r.sqlite")
    with pytest.raises(ValueError):
        store.decide("X", "vetoed", basis_year="2025")          # 태그 없는 veto 금지
    with pytest.raises(ValueError):
        store.decide("X", "vetoed", basis_year="2025", tags=["아무말"])  # 미등록 태그 금지
    with pytest.raises(ValueError):
        store.decide("X", "hold", basis_year="2025")            # 조건 없는 hold 금지
    with pytest.raises(ValueError):
        store.decide("X", "maybe", basis_year="2025")           # 미정의 verdict 금지
    store.close()


def test_veto_tag_promotion_counts(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "r.sqlite")
    store.decide("A00001", "vetoed", basis_year="2025", tags=["버킷착시", "저마진"])
    store.decide("A00002", "vetoed", basis_year="2025", tags=["버킷착시"])
    counts = store.veto_tag_counts()
    assert counts["버킷착시"] == 2 >= RULE_PROMOTION_THRESHOLD
    assert counts["저마진"] == 1
    store.close()


def test_auto_review_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """v2.6 자동 심사 — 결정론 규칙, 수동 판정 우선, veto 미생성."""
    from dataclasses import dataclass, field

    import trading.review as review_mod
    from trading.review import auto_review

    @dataclass
    class _Rec:
        symbol: str
        industry: str

    @dataclass
    class _Pick:
        rec: _Rec
        verdict: str | None
        roe_delta: float | None
        upside_pct: float | None
        flags: list[str] = field(default_factory=list)

    picks = [
        _Pick(_Rec("A00001", "화학"), None, 1.0, 80.0),                  # → approved
        _Pick(_Rec("A00002", "제약"), None, -3.0, 90.0),                 # → hold(양전 확인)
        _Pick(_Rec("A00003", "리츠"), None, 1.0, 70.0),                  # → approved(v2.18: 리츠 hold 제거)
        _Pick(_Rec("A00004", "화학"), None, 2.0, 400.0),                 # → hold(극단 여력)
        _Pick(_Rec("A00005", "유통"), None, 2.0, 60.0, ["⚠매출급감 -12%"]),  # → hold(급감)
        _Pick(_Rec("A00006", "화학"), "approved", -9.0, 50.0),           # 수동 존재 → 스킵
        _Pick(_Rec("A00007", "화학"), None, 1.0, 20.0),                  # → hold(여력 <30%, LF 사례)
    ]
    monkeypatch.setattr("trading.web.picks._build_picks", lambda: picks)
    store = review_mod.ReviewStore(tmp_path / "r.sqlite")
    n_appr, n_hold = auto_review(store, "2025")
    assert (n_appr, n_hold) == (2, 4)
    assert store.current("A00003", "2025")["verdict"] == "approved"  # type: ignore[index]
    low = store.current("A00007", "2025")
    assert low is not None and low["verdict"] == "hold" and "회귀 여력 +30% 회복" in str(low["condition"])
    assert store.current("A00001", "2025")["verdict"] == "approved"  # type: ignore[index]
    assert store.current("A00002", "2025")["verdict"] == "hold"  # type: ignore[index]
    assert store.current("A00006", "2025") is None                   # 자동이 안 건드림
    # veto는 자동 생성 안 함
    assert store.veto_tag_counts() == {}
    store.close()
