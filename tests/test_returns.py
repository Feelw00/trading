"""collectors.returns — 주주환원·분할 수집(v1.8 ③) 단위 테스트. 픽스처=실호출 관측(2026-09-01)."""

import json
from pathlib import Path

from trading.collectors.dart import DartClient
from trading.collectors.returns import (
    ReturnsStore,
    SplitAssessment,
    SplitDecision,
    classify_split_method,
    collect_returns,
    collect_split_decisions,
    collect_splits,
    is_par_based_yield,
)

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


def _rows(rcept: str, stlm: str, dps: str, yld: str, knd: str = "보통주식") -> list[dict[str, str]]:
    return [
        {"rcept_no": rcept, "stlm_dt": stlm, "se": "주당액면가액(원)", "stock_knd": "-", "thstrm": "1,000"},
        {"rcept_no": rcept, "stlm_dt": stlm, "se": "주당 현금배당금(원)", "stock_knd": knd, "thstrm": dps},
        {"rcept_no": rcept, "stlm_dt": stlm, "se": "현금배당수익률(%)", "stock_knd": knd, "thstrm": yld},
        {"rcept_no": rcept, "stlm_dt": stlm, "se": "(연결)현금배당성향(%)", "stock_knd": "-", "thstrm": "159.0"},
    ]


def test_dividend_series_sums_semiannual_receipts_and_dedupes_corrections(tmp_path: Path) -> None:
    """COLLECT-5 ①(2026-09-04): 반기 결산 리츠 — 한 연도에 접수분 2건(결산일 6/30·12/31) → 합산.
    정정(같은 결산일, 더 큰 접수번호)은 최신만. 성향은 접수분 2+면 None(비율 가산 불가)."""
    store = ReturnsStore(tmp_path / "r.sqlite")
    rows = _rows("20250922000198", "2025-06-30", "150", "3.6") + _rows("20260320000738", "2025-12-31", "150", "3.8")
    store.upsert_alot("350520", "2025", rows)
    d = store.dividend_series("350520")["2025"]
    assert d["dps"] == 300.0 and d["yield_pct"] == 7.4 and d["payout_pct"] is None and d["n_reports"] == 2.0
    # 12/31 결산분 정정 공시(접수번호 증가) → 12/31은 정정값 160으로 대체, 6/30은 유지
    store.upsert_alot("350520", "2025", _rows("20260401000001", "2025-12-31", "160", "4.0"))
    d = store.dividend_series("350520")["2025"]
    assert d["dps"] == 310.0 and d["n_reports"] == 2.0
    store.close()


def test_dividend_series_legacy_table_fallback_and_recollect(tmp_path: Path) -> None:
    """접수분 표 도입 전 적재(alot_facts만) — 읽기는 폴백(n_reports None), 수집은 'ok' 시도라도 1회 재수집."""
    store = ReturnsStore(tmp_path / "r.sqlite")
    store._conn.execute(
        "INSERT INTO alot_facts VALUES (?,?,?,?,?,?,?)",
        ("005930", "2025", "주당 현금배당금(원)", "보통주", 1668.0, "dart:alotMatter", "t"),
    )
    store._conn.commit()
    store.record_attempt("005930", "alot", "2025", "ok")
    store.record_attempt("005930", "tesstk", "2025", "ok")
    d = store.dividend_series("005930")["2025"]
    assert d["dps"] == 1668.0 and d["n_reports"] is None
    calls: list[str] = []

    def fetch(url: str) -> object:
        calls.append(url)
        return ALOT if "alotMatter" in url else EMPTY

    collect_returns(DartClient("k", json_fetch=fetch), store, CORP_MAP, [("005930", "삼성전자")], years=1, year_now=2026)
    assert any("alotMatter" in u for u in calls) and not any("tesstk" in u for u in calls)
    assert store.has_alot_report("005930", "2025")
    assert store.dividend_series("005930")["2025"]["n_reports"] == 1.0
    store.close()


def test_classify_split_method_observed_vocabulary() -> None:
    assert classify_split_method("…지분율에 비례하여 … 단순·인적분할의 방법으로 분할하며…") == "인적"
    assert classify_split_method("…발행주식총수를 배정받는 단순·물적분할 방식으로 분할한다") == "물적"
    assert classify_split_method("단순ㆍ물적분할의 방법으로") == "물적"          # 서흥 — 가운뎃점 변형
    assert classify_split_method("인적 분할 후 일부는 물적 분할") == "혼합"
    assert classify_split_method(None, "물적분할") == "물적"                     # 원문 없음 → ex_sm_r 보조
    assert classify_split_method(None, "-") == "미상"                          # 빈 레코드(골프존 2024)


def test_split_assessment_downgrade_rules() -> None:
    def dv(cls: str, rn: str = "1") -> SplitDecision:
        return SplitDecision(kind="분할", rcept_no=rn, bddd=None, cls=cls)

    assert SplitAssessment(0, ()).downgrade == 0 and SplitAssessment(0, ()).summary == ""
    assert SplitAssessment(3, ()).downgrade == 1 and SplitAssessment(3, ()).summary == "미수록 3건"
    assert SplitAssessment(2, (dv("인적"),)).downgrade == 0                     # 인적만 — 강등 없음(v2.18)
    assert SplitAssessment(1, (dv("물적"),)).downgrade == 1
    assert SplitAssessment(2, (dv("인적", "1"), dv("미상", "2"))).downgrade == 1
    assert SplitAssessment(2, (dv("혼합", "1"), dv("물적", "2"))).summary == "물적 1 · 혼합 1"


def test_collect_split_decisions_classifies_and_is_idempotent(tmp_path: Path) -> None:
    store = ReturnsStore(tmp_path / "r.sqlite")
    listing = {"status": "000", "total_page": 1, "list": [
        {"rcept_no": "20251105000330", "rcept_dt": "20251106", "report_nm": "주요사항보고서(회사분할결정)"},
    ]}
    decision = {"status": "000", "list": [{
        "rcept_no": "20260710000095", "bddd": "2025년 11월 05일", "ex_sm_r": "-",
        "dv_mth": "(1) … 지분율에 비례하여 분할신설회사의 주식을 배정받는 단순·인적분할의 방법으로 분할하며 …",
        "atdv_excmp_cmpnm": "주식회사 토비스", "dvfcmp_cmpnm": "(주)네오뷰(가칭)",
    }]}
    calls: list[str] = []

    def fetch(url: str) -> object:
        calls.append(url)
        if "list.json" in url:
            return listing
        if "cmpDvDecsn" in url and "corp_code=00000001" in url:
            return decision  # 토비스만 구조화 레코드 있음(샘표 2016 접수분은 미수록 — 실관측)
        return EMPTY

    dart = DartClient("k", json_fetch=fetch)
    stocks = [("051360", "토비스")]
    collect_splits(dart, store, {"051360": ("00000001", "토비스")}, stocks, year_now=2026)
    got, skipped, errors = collect_split_decisions(dart, store, {"051360": ("00000001", "토비스")}, stocks, year_now=2026)
    assert (got, skipped, errors) == (1, 0, [])
    a = store.split_assessment("051360")
    assert a.n_events == 1 and a.decisions[0].cls == "인적" and a.downgrade == 0 and a.summary == "인적 1"
    n_before = len(calls)
    collect_split_decisions(dart, store, {"051360": ("00000001", "토비스")}, stocks, year_now=2026)
    assert len(calls) == n_before  # 같은 창·같은 최신 접수일 → 재호출 없음
    # 무이력 종목은 스킵, 이력 있는데 구조화 API 0건이면 미수록(강등 유지)
    store.add_splits("007540", [{"rcept_no": "x1", "rcept_dt": "20160223", "report_nm": "주요사항보고서(회사분할결정)"}])
    got, skipped, _ = collect_split_decisions(dart, store, {"007540": ("00000002", "샘표"), "999999": ("0", "무")},
                                              [("007540", "샘표"), ("999999", "무이력")], year_now=2026)
    assert (got, skipped) == (0, 2) and store.split_assessment("007540").downgrade == 1
    store.close()
