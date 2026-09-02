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
        _Pick(_Rec("A00003", "리츠"), None, 1.0, 70.0),                  # → hold(COLLECT-5)
        _Pick(_Rec("A00004", "화학"), None, 2.0, 400.0),                 # → hold(극단 여력)
        _Pick(_Rec("A00005", "유통"), None, 2.0, 60.0, ["⚠매출급감 -12%"]),  # → hold(급감)
        _Pick(_Rec("A00006", "화학"), "approved", -9.0, 50.0),           # 수동 존재 → 스킵
    ]
    monkeypatch.setattr("trading.web.picks._build_picks", lambda: picks)
    store = review_mod.ReviewStore(tmp_path / "r.sqlite")
    n_appr, n_hold = auto_review(store, "2025")
    assert (n_appr, n_hold) == (1, 4)
    assert store.current("A00001", "2025")["verdict"] == "approved"  # type: ignore[index]
    assert store.current("A00002", "2025")["verdict"] == "hold"  # type: ignore[index]
    assert store.current("A00006", "2025") is None                   # 자동이 안 건드림
    # veto는 자동 생성 안 함
    assert store.veto_tag_counts() == {}
    store.close()
