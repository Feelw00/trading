"""collectors.returns — 주주환원·분할 수집(v1.8 ③) 단위 테스트. 픽스처=실호출 관측(2026-09-01)."""

import json
from pathlib import Path

from trading.collectors.dart import DartClient
from trading.collectors.returns import ReturnsStore, collect_returns, collect_splits, is_par_based_yield

FIX = Path(__file__).resolve().parent.parent / "fixtures"
ALOT = json.loads((FIX / "alotMatter_005930_2025.json").read_text())
TESSTK = json.loads((FIX / "tesstk_005930_2025.json").read_text())
EMPTY = {"status": "013", "message": "조회된 데이터가 없습니다."}
CORP_MAP = {"005930": ("00126380", "삼성전자")}


def _dart(responses: dict[str, object]) -> DartClient:
    def fake_json(url: str) -> object:
        for frag, resp in responses.items():
            if frag in url:
                return resp
        return EMPTY
    return DartClient("test-key", json_fetch=fake_json)


def test_collect_returns_parses_fixture_and_reads_back(tmp_path: Path) -> None:
    store = ReturnsStore(tmp_path / "r.sqlite")
    dart = _dart({"alotMatter": ALOT, "tesstkAcqsDspsSttus": TESSTK})
    loaded, skipped, errors = collect_returns(
        dart, store, CORP_MAP, [("005930", "삼성전자")], years=1, year_now=2026,
    )
    assert (loaded, skipped, errors) == (1, 0, [])
    div = store.dividend_series("005930")["2025"]
    assert div["dps"] == 1668.0 and div["yield_pct"] == 1.5 and div["payout_pct"] == 25.1
    bb = store.buyback_series("005930")["2025"]
    assert bb["acqs"] == 134_779_992.0 and bb["incnr"] == 57_056_664.0  # 총계(보통+우선) 정본
    store.close()


def test_collect_returns_idempotent_rerun(tmp_path: Path) -> None:
    store = ReturnsStore(tmp_path / "r.sqlite")
    dart = _dart({"alotMatter": ALOT, "tesstkAcqsDspsSttus": TESSTK})
    args = (dart, store, CORP_MAP, [("005930", "삼성전자")])
    collect_returns(*args, years=1, year_now=2026)
    calls: list[str] = []
    def counting(url: str) -> object:
        calls.append(url)
        return EMPTY
    dart2 = DartClient("k", json_fetch=counting)
    loaded, _, _ = collect_returns(dart2, store, CORP_MAP, [("005930", "삼성전자")], years=1, year_now=2026)
    assert loaded == 1 and calls == []  # 기시도 연도는 API 재호출 없음
    store.close()


def test_collect_splits_filters_and_records(tmp_path: Path) -> None:
    store = ReturnsStore(tmp_path / "r.sqlite")
    listing = {
        "status": "000", "total_page": 1,
        "list": [
            {"rcept_no": "1", "rcept_dt": "20200917", "report_nm": "주요사항보고서(회사분할결정)"},
            {"rcept_no": "2", "rcept_dt": "20210105", "report_nm": "주요사항보고서(유상증자결정)"},
        ],
    }
    dart = _dart({"list.json": listing})
    found, skipped, errors = collect_splits(
        dart, store, CORP_MAP, [("005930", "삼성전자")], year_now=2026,
    )
    assert (found, skipped, errors) == (1, 0, [])
    hist = store.split_history("005930")
    assert hist == [("20200917", "주요사항보고서(회사분할결정)")]  # 비분할 공시 제외


def test_no_corp_code_is_skipped_not_error(tmp_path: Path) -> None:
    store = ReturnsStore(tmp_path / "r.sqlite")
    dart = _dart({})
    loaded, skipped, errors = collect_returns(dart, store, {}, [("999999", "미상")], years=1, year_now=2026)
    assert (loaded, skipped, errors) == (0, 1, [])
    found, s_skip, _ = collect_splits(dart, store, {}, [("999999", "미상")], year_now=2026)
    assert (found, s_skip) == (0, 1)


def test_dividend_series_single_class_dash_fallback(tmp_path: Path) -> None:
    """stock_knd '-'(단일 주식 종류) 폴백 — 신세계I&C 2024~ 실관측 오독 버그 회귀 테스트."""
    store = ReturnsStore(tmp_path / "r.sqlite")
    store.upsert_alot("035510", "2025", [
        {"se": "주당 현금배당금(원)", "stock_knd": "-", "thstrm": "560"},
        {"se": "현금배당수익률(%)", "stock_knd": "-", "thstrm": "3.1"},
    ])
    store.upsert_alot("035510", "2023", [
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "350"},
        {"se": "주당 현금배당금(원)", "stock_knd": "우선주", "thstrm": "9,999"},
    ])
    div = store.dividend_series("035510")
    assert div["2025"]["dps"] == 560.0 and div["2025"]["yield_pct"] == 3.1
    assert div["2023"]["dps"] == 350.0  # 우선주 무시, 보통주 우선
    store.close()


def test_dividend_series_par_based_yield_is_dropped(tmp_path: Path) -> None:
    """P-20 ⑥(2026-09-04): '현금배당수익률' 행에 액면 배당률(DPS÷액면가)을 적은 공시 — 흥국 2025 실관측
    (280원 ÷ 액면 500원 = 56.0%). 시가 수익률이 아니므로 None, dps는 유지(지급 판정 불변)."""
    store = ReturnsStore(tmp_path / "r.sqlite")
    store.upsert_alot("010240", "2025", [
        {"se": "주당액면가액(원)", "stock_knd": "-", "thstrm": "500"},
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "280"},
        {"se": "현금배당수익률(%)", "stock_knd": "보통주", "thstrm": "56.0"},
    ])
    # 정상 공시(삼성전자형): 수익률 1.5 ≠ 1668/100 → 유지
    store.upsert_alot("010240", "2024", [
        {"se": "주당액면가액(원)", "stock_knd": "-", "thstrm": "100"},
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,668"},
        {"se": "현금배당수익률(%)", "stock_knd": "보통주", "thstrm": "1.5"},
    ])
    div = store.dividend_series("010240")
    assert div["2025"]["dps"] == 280.0 and div["2025"]["yield_pct"] is None
    assert div["2024"]["dps"] == 1668.0 and div["2024"]["yield_pct"] == 1.5
    store.close()


def test_dividend_series_normalizes_common_stock_labels(tmp_path: Path) -> None:
    """'보통주식'·공백 라벨(황금에스티 등 실관측)은 보통주로 읽는다. 그 외 어휘는 해석하지 않는다."""
    store = ReturnsStore(tmp_path / "r.sqlite")
    store.upsert_alot("032560", "2022", [
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주식", "thstrm": "150"},
        {"se": "현금배당수익률(%)", "stock_knd": "보통주 ", "thstrm": "4.2"},
    ])
    store.upsert_alot("032560", "2021", [
        {"se": "주당 현금배당금(원)", "stock_knd": "종류주식", "thstrm": "999"},
    ])
    div = store.dividend_series("032560")
    assert div["2022"]["dps"] == 150.0 and div["2022"]["yield_pct"] == 4.2
    assert div["2021"]["dps"] is None
    store.close()


def test_is_par_based_yield_pure() -> None:
    assert is_par_based_yield(56.0, 280.0, 500.0)          # 흥국 2025
    assert is_par_based_yield(100.0, 100.0, 100.0)         # 유에스티
    assert not is_par_based_yield(25.6, 8750.0, 5000.0)    # 예스코홀딩스 2023 — 액면 175% ≠ 25.6(시가 기준)
    assert not is_par_based_yield(None, 280.0, 500.0)
    assert not is_par_based_yield(56.0, 280.0, None)
    assert not is_par_based_yield(0.0, 0.0, 500.0)
