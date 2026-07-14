"""재무 캐시(fins) — 파싱·스냅샷·사다리 수집 테스트. 픽스처는 2026-07-11 실호출 관측 형태."""

from pathlib import Path
from typing import Any

from trading.collectors.fins import FinStore, collect_fins, parse_amount


def _row(fs: str, sj: str, nm: str, th: str, fr: str) -> dict[str, Any]:
    return {"fs_div": fs, "sj_div": sj, "account_nm": nm,
            "thstrm_amount": th, "frmtrm_amount": fr, "currency": "KRW"}


# 팬오션 2026 1Q(11013) 실관측 축약 — IS: thstrm=당기 분기 / frmtrm=전기 동일분기
_Q1_ROWS = [
    _row("CFS", "IS", "매출액", "1,508,925,000,000", "1,393,446,000,000"),
    _row("CFS", "IS", "영업이익", "140,923,000,000", "113,293,000,000"),
    _row("CFS", "IS", "당기순이익(손실)", "94,516,000,000", "72,023,000,000"),
    _row("CFS", "IS", "당기순이익(손실)", "94,516,000,000", "72,023,000,000"),  # 실관측 중복 행
    _row("CFS", "BS", "부채총계", "5,610,871,000,000", "5,130,186,000,000"),
    _row("CFS", "BS", "자본총계", "6,059,356,000,000", "5,723,482,000,000"),
    _row("OFS", "IS", "매출액", "999", "888"),  # 별도 — CFS 우선이라 무시돼야
]


def test_parse_amount() -> None:
    assert parse_amount("1,508,925,000,000") == 1_508_925_000_000.0
    assert parse_amount("-12,345") == -12345.0
    assert parse_amount("") is None
    assert parse_amount("-") is None
    assert parse_amount(None) is None


def test_snapshot_prefers_cfs_and_computes_ratios(tmp_path: Path) -> None:
    store = FinStore(tmp_path / "f.sqlite")
    store.upsert("028670", "2026", "11013", _Q1_ROWS)
    snap = store.snapshot_for("028670")
    assert snap is not None and snap.fs_div == "CFS"
    assert snap.revenue == 1_508_925_000_000.0  # OFS 999 아님
    assert snap.rev_yoy is not None and abs(snap.rev_yoy - 0.08287) < 1e-3
    assert snap.op_yoy is not None and abs(snap.op_yoy - 0.24388) < 1e-3
    assert snap.op_margin is not None and abs(snap.op_margin - 0.0934) < 1e-3
    assert snap.debt_ratio is not None and abs(snap.debt_ratio - 0.926) < 1e-2
    store.close()


def test_snapshot_picks_latest_report(tmp_path: Path) -> None:
    store = FinStore(tmp_path / "f.sqlite")
    store.upsert("111110", "2025", "11011", [_row("CFS", "IS", "매출액", "100", "90")])
    store.upsert("111110", "2026", "11013", [_row("CFS", "IS", "매출액", "30", "20")])
    snap = store.snapshot_for("111110")
    assert snap is not None and (snap.bsns_year, snap.reprt_code) == ("2026", "11013")
    store.close()


def test_snapshot_missing_fields_are_none(tmp_path: Path) -> None:
    store = FinStore(tmp_path / "f.sqlite")
    store.upsert("222220", "2026", "11013", [_row("CFS", "IS", "매출액", "100", "")])
    snap = store.snapshot_for("222220")
    assert snap is not None
    assert snap.rev_yoy is None      # 전기 결측 → YoY None (0 폴백 금지)
    assert snap.op_margin is None    # 영업이익 결측
    assert snap.debt_ratio is None
    assert store.snapshot_for("없는종목") is None
    store.close()


def test_op_yoy_none_when_prev_nonpositive_and_turnaround_flag(tmp_path: Path) -> None:
    store = FinStore(tmp_path / "f.sqlite")
    store.upsert("333330", "2026", "11013", [
        _row("CFS", "IS", "매출액", "100", "100"),
        _row("CFS", "IS", "영업이익", "10", "-5"),
    ])
    snap = store.snapshot_for("333330")
    assert snap is not None
    assert snap.op_yoy is None            # 전기 적자 → 증감률 무의미
    assert snap.op_turned_positive is True
    store.close()


class _FakeDart:
    """financials 사다리 흉내 — (year, reprt) 키에 준비된 응답만 반환."""

    def __init__(self, data: dict[tuple[str, str], list[dict[str, Any]]]) -> None:
        self._data = data
        self.calls: list[tuple[str, str]] = []

    def financials(self, corp_code: str, bsns_year: str, reprt_code: str) -> list[dict[str, Any]]:
        self.calls.append((bsns_year, reprt_code))
        return self._data.get((bsns_year, reprt_code), [])


def test_collect_ladder_stops_at_first_available(tmp_path: Path) -> None:
    from datetime import datetime
    from trading.collectors.base import KST

    store = FinStore(tmp_path / "f.sqlite")
    dart = _FakeDart({("2026", "11013"): _Q1_ROWS})  # 3Q·반기 없음 → 1Q에서 적중
    now = datetime(2026, 7, 11, tzinfo=KST)
    loaded, skipped, errors = collect_fins(
        dart, store, {"028670": ("00123456", "팬오션")}, [("028670", "팬오션")], now=now  # type: ignore[arg-type]
    )
    assert (loaded, skipped, errors) == (1, 0, [])
    assert dart.calls == [("2026", "11014"), ("2026", "11012"), ("2026", "11013")]
    # 재실행: empty·ok 시도 기록으로 API 콜 0회
    dart.calls.clear()
    loaded2, _, _ = collect_fins(
        dart, store, {"028670": ("00123456", "팬오션")}, [("028670", "팬오션")], now=now  # type: ignore[arg-type]
    )
    assert loaded2 == 1 and dart.calls == []
    store.close()


def test_collect_no_corp_code_skips(tmp_path: Path) -> None:
    store = FinStore(tmp_path / "f.sqlite")
    dart = _FakeDart({})
    loaded, skipped, _ = collect_fins(dart, store, {}, [("999990", "코드없음")])  # type: ignore[arg-type]
    assert (loaded, skipped) == (0, 1)
    store.close()
