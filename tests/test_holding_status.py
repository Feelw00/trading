"""holding_status — 보유 종목 상태 전이 P1(SCREEN-1 후속, 운영자 결정 2026-09-04) 단위 테스트."""

from pathlib import Path

from trading.collectors.audit import AuditVerdict
from trading.collectors.status import KisStatusRow, StatusStore
from trading.holding_status import audit_adverse, check_kis, kis_transitions


def _row(symbol: str, as_of: str, *, stat: str = "55", mang: str | None = "N", price: float | None = 1000.0) -> KisStatusRow:
    return KisStatusRow(
        symbol=symbol, as_of=as_of, iscd_stat_cls_code=stat, mang_issu_cls_code=mang,
        mrkt_warn_cls_code="00", temp_stop_yn="N", sltr_yn="N", short_over_yn="N", invt_caful_yn="N",
        crdt_able_yn="Y", rprs_mrkt_kor_name="KOSDAQ", last_price=price,
    )


def test_kis_transitions_only_normal_to_flagged() -> None:
    held = ["A", "B", "C", "D", "E"]
    latest = {
        "A": _row("A", "2026-09-04", mang="Y"),           # 정상 → 관리종목: 전이
        "B": _row("B", "2026-09-04", stat="58"),          # 정상 → 거래정지: 전이
        "C": _row("C", "2026-09-04", mang="Y"),           # 직전도 관리: 전이 아님
        "D": _row("D", "2026-09-04"),                     # 관리 → 정상(해제): 이번 결정 범위 밖
        "E": _row("E", "2026-09-04", mang="Y"),           # 직전 없음: 침묵
    }
    previous = {
        "A": _row("A", "2026-09-03"), "B": _row("B", "2026-09-03"),
        "C": _row("C", "2026-09-03", mang="Y"), "D": _row("D", "2026-09-03", mang="Y"),
    }
    trs = kis_transitions(held, latest, previous)
    assert [t.symbol for t in trs] == ["A", "B"]
    assert "관리종목" in trs[0].reasons and "매매거래정지" in trs[1].reasons
    assert trs[0].key == "2026-09-04"


def test_kis_transitions_ignores_non_held_and_stale_previous() -> None:
    latest = {"X": _row("X", "2026-09-04", mang="Y"), "Y": _row("Y", "2026-09-03", mang="Y")}
    previous = {"X": _row("X", "2026-09-03"), "Y": _row("Y", "2026-09-04")}  # Y: 직전이 최신보다 늦음(비정상)
    assert kis_transitions(["Y"], latest, previous) == []
    assert [t.symbol for t in kis_transitions(["X"], latest, previous)] == ["X"]


def test_audit_adverse_only_adverse_with_receipt() -> None:
    verdicts = {
        "A": AuditVerdict(state="adverse", opinion="의견거절", rcept_no="20260401000123", adtor="OO회계법인"),
        "B": AuditVerdict(state="clean", opinion="적정의견", rcept_no="20260401000124", adtor="OO"),
        "C": AuditVerdict(state="unaudited", opinion=None, rcept_no="20260401000125", adtor=None),
        "D": AuditVerdict(state="missing", opinion=None, rcept_no=None, adtor=None),
    }
    trs = audit_adverse(["A", "B", "C", "D"], verdicts, "2025")
    assert [(t.symbol, t.key) for t in trs] == [("A", "20260401000123")]
    assert "의견거절" in trs[0].reasons


def test_check_kis_emits_once_and_dedupes(tmp_path: Path) -> None:
    """저장소 배선: 직전 정상 → 최신 관리종목이면 P1 1회, 같은 날 재실행은 침묵(holding_status_alerts)."""
    store = StatusStore(tmp_path / "status.sqlite")
    store.append_kis("A", "2026-09-03", {"iscd_stat_cls_code": "55", "mang_issu_cls_code": "N", "stck_prpr": "1000"}, fetched_at="t")
    store.append_kis("A", "2026-09-04", {"iscd_stat_cls_code": "51", "mang_issu_cls_code": "Y", "stck_prpr": "900"}, fetched_at="t")
    store.append_kis("B", "2026-09-04", {"iscd_stat_cls_code": "55", "mang_issu_cls_code": "N", "stck_prpr": "1000"}, fetched_at="t")
    got: list[str] = []
    lines = check_kis(store, {"A": "알파", "B": "베타"}, now_iso="2026-09-04T18:10:00+09:00", notify=got.append)
    assert len(lines) == 1 and lines == got
    assert lines[0].startswith("보유 종목 상태 전이: A 알파 — 관리종목") and "2026-09-03 정상 → 2026-09-04" in lines[0]
    # 재실행: 중복 없음
    assert check_kis(store, {"A": "알파"}, now_iso="2026-09-04T18:20:00+09:00", notify=got.append) == []
    assert len(got) == 1
    assert store.kis_previous("A", "2026-09-04") is not None and store.kis_previous("A", "2026-09-03") is None
    store.close()
