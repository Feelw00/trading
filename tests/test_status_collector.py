"""SCREEN-1 종목 상태 스냅샷 — 관측 봉투(2026-09-03 실호출) 기반 분류·멱등·격리 테스트."""

from pathlib import Path
from typing import Any

from trading.collectors.status import (
    KisStatusRow,
    StatusStore,
    classify_kis,
    collect_kis_status,
    flagged_summary,
)

TODAY = "2026-09-03"


def _out(stat: str | None, mang: str | None, prpr: str, warn: str | None = "00") -> dict[str, Any]:
    # 실호출 관측 봉투 축약 — 정상(55/57)·관리(51/Y)·정지(58/Y)·상폐(00/None/0)
    return {
        "iscd_stat_cls_code": stat, "mang_issu_cls_code": mang, "mrkt_warn_cls_code": warn,
        "temp_stop_yn": "N", "sltr_yn": "N" if mang is not None else None, "short_over_yn": "N",
        "invt_caful_yn": "N" if mang is not None else None, "crdt_able_yn": "N",
        "rprs_mrkt_kor_name": "KOSDAQ", "stck_prpr": prpr, "bstp_kor_isnm": "기타",
        "per": "12.3",  # 상태 외 필드는 열 승격 없이 무시
    }


ENVELOPES = {
    "005930": _out("55", "N", "250000"),      # 정상(코스피)
    "262840": _out("57", "N", "2720"),        # 정상(코스닥)
    "005320": _out("51", "Y", "1158"),        # 관리종목(온타이드 8/12)
    "294090": _out("58", "Y", "1490"),        # 거래정지(이오플로우)
    "106520": _out("00", None, "0", None),    # 상장폐지/무자료(노블엠앤비)
}


class _FakeKis:
    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[str] = []
        self._fail = fail

    def quote_price(self, srtn_cd: str) -> dict[str, Any]:
        self.calls.append(srtn_cd)
        if srtn_cd == self._fail:
            raise RuntimeError("boom")
        return ENVELOPES.get(srtn_cd, {})


def test_classify_matches_observed_semantics() -> None:
    def row(stat: str | None, mang: str | None, price: float | None) -> KisStatusRow:
        return KisStatusRow("X", TODAY, stat, mang, "00", "N", "N", "N", "N", "N", "KOSDAQ", price)

    ok = classify_kis(row("55", "N", 250000.0))
    assert (ok.managed, ok.halted, ok.delisted_suspect, ok.reasons) == (False, False, False, [])
    assert classify_kis(row("57", "N", 2720.0)).managed is False
    m = classify_kis(row("51", "Y", 1158.0))
    assert m.managed is True and m.halted is False and "관리종목" in m.reasons[0]
    h = classify_kis(row("58", "Y", 1490.0))
    assert h.managed is True and h.halted is True and any("거래정지" in r for r in h.reasons)
    d = classify_kis(row("00", None, 0.0))
    assert d.delisted_suspect and d.managed is None and d.halted is None
    # 정지 판정은 58로만 — temp_stop_yn(정지 종목도 'N')은 쓰지 않는다
    assert classify_kis(row("55", "N", 100.0)).halted is False
    # 상태 필드 전부 결측 → 관측 불가(None), 상폐 의심 아님(현재가 있음)
    u = classify_kis(row(None, None, 100.0))
    assert u.managed is None and u.halted is None and not u.delisted_suspect


def test_collect_snapshot_idempotent_and_flag_summary(tmp_path: Path) -> None:
    store = StatusStore(tmp_path / "s.sqlite")
    kis = _FakeKis()
    syms = list(ENVELOPES)
    added, calls, errors, remaining = collect_kis_status(kis, store, syms, today=TODAY)
    assert (added, calls, errors, remaining) == (5, 5, [], 0)
    row = store.latest_kis("294090")
    assert row is not None and row.iscd_stat_cls_code == "58" and row.mang_issu_cls_code == "Y"
    assert row.last_price == 1490.0
    gone = store.latest_kis("106520")
    assert gone is not None and gone.mang_issu_cls_code is None and gone.last_price == 0.0
    # 같은 날 재실행 → 무호출·무변화(멱등, 비용 0)
    added2, calls2, _, _ = collect_kis_status(kis, store, syms, today=TODAY)
    assert (added2, calls2) == (0, 0) and len(kis.calls) == 5
    # skip_observed=False면 호출은 하되 UNIQUE(symbol, as_of)로 적재 0
    added3, calls3, _, _ = collect_kis_status(kis, store, syms, today=TODAY, skip_observed=False)
    assert (added3, calls3) == (0, 5)
    # 다음 날 → 새 스냅샷 행(append-only 이력)
    added4, _, _, _ = collect_kis_status(kis, store, syms, today="2026-09-04")
    assert added4 == 5 and store.kis_coverage() == (5, 2, "2026-09-04")
    flags = flagged_summary(store)
    assert flags == {"managed": ["005320", "294090"], "halted": ["294090"], "delisted_suspect": ["106520"]}
    assert set(store.latest_kis_all()) == set(syms)
    store.close()


def test_one_symbol_failure_and_empty_response_are_isolated(tmp_path: Path) -> None:
    store = StatusStore(tmp_path / "s.sqlite")
    kis = _FakeKis(fail="005320")
    added, calls, errors, _ = collect_kis_status(kis, store, ["005930", "005320", "999999"], today=TODAY)
    assert added == 1 and calls == 3
    assert any(e.startswith("005320") for e in errors) and any("999999" in e and "빈 응답" in e for e in errors)
    store.close()


def test_budget_stops_after_first_chunk_and_reports_remaining(tmp_path: Path) -> None:
    # 시간 예산 초과 → 첫 워밍 호출 + 첫 청크까지만, 나머지는 미관측 수로 보고(다음 실행이 이어감)
    store = StatusStore(tmp_path / "s.sqlite")
    kis = _FakeKis()
    syms = [f"{i:06d}" for i in range(1, 60)]
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])       # 워밍 직후 첫 청크는 예산 안, 둘째 청크부터 초과
    added, calls, errors, remaining = collect_kis_status(
        kis, store, syms, today=TODAY, workers=2, budget_s=10.0, clock=lambda: next(ticks, 100.0)
    )
    assert calls == 1 + 16 and remaining == len(syms) - 17 and added == 0    # 빈 응답(미등록 심볼) → 적재 0
    assert len(errors) == 17
    store.close()
