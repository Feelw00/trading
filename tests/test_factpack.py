"""FactPack 결정론 조립 — 금액 파싱·재무 추출(연결 우선·YoY)·결측 note·재무 기간 폴백."""

from pathlib import Path

from trading.collectors.market import MarketStore
from trading.contracts.factpack import FactPack
from trading.factpack import (
    _extract_financials,
    _fin_periods,
    _parse_amount,
    build_fact_pack,
    build_fact_pack_for,
)
from trading.screener import Candidate, ScreenConfig, SignalSet, signals_from_series

SIG = SignalSet(tr_value_surge=3.2, mom_short=0.21, mom_long=0.55, high_proximity=0.84)
CAND = Candidate(srtn_cd="003220", name="대원제약", market="KOSPI", clpr=18000.0, score=0.92, signals=SIG)


def test_parse_amount_variants() -> None:
    assert _parse_amount("1,234,567") == 1234567.0
    assert _parse_amount("(50)") == -50.0       # 괄호 = 음수
    assert _parse_amount("-12,000") == -12000.0
    assert _parse_amount("") is None
    assert _parse_amount("-") is None
    assert _parse_amount(None) is None


def test_fin_periods_current_and_prior_year_only() -> None:
    p = _fin_periods(2026)
    assert ("2026", "11014") in p and ("2025", "11011") in p
    assert all(yr in {"2026", "2025"} for yr, _ in p)  # 과추측 방지(올해·작년만)


def test_extract_financials_cfs_pref_and_yoy() -> None:
    rows: list[dict[str, object]] = [
        {"account_nm": "매출액", "fs_div": "OFS", "thstrm_amount": "900", "frmtrm_amount": "800"},
        {"account_nm": "매출액", "fs_div": "CFS", "thstrm_amount": "1,200", "frmtrm_amount": "1,000"},
        {"account_nm": "영업이익", "fs_div": "CFS", "thstrm_amount": "(50)", "frmtrm_amount": "100"},
        {"account_nm": "자산총계", "fs_div": "CFS", "thstrm_amount": "5,000", "frmtrm_amount": "4,000"},
    ]
    out = {f.account: f for f in _extract_financials(rows)}
    assert out["매출액"].fs_div == "CFS" and out["매출액"].thstrm == 1200.0  # 연결 우선
    assert out["매출액"].yoy_pct == 20.0
    assert out["영업이익"].thstrm == -50.0 and out["영업이익"].yoy_pct == -150.0  # 적자전환
    assert out["자산총계"].yoy_pct == 25.0
    assert "부채총계" not in out  # 없는 계정은 만들지 않음


class _FakeDart:
    """financials는 2025만 데이터 — 2026 기간 폴백 후 채택되는지 검증."""

    def __init__(self, *, with_disc: bool) -> None:
        self.with_disc = with_disc

    def disclosures(self, corp_code: str, bgn_de: str, end_de: str) -> list[dict[str, object]]:
        if not self.with_disc:
            return []
        return [{"rcept_no": "20260515000123", "report_nm": "분기보고서", "rcept_dt": "20260515", "flr_nm": "대원제약"}]

    def financials(self, corp_code: str, bsns_year: str, reprt_code: str) -> list[dict[str, object]]:
        if bsns_year != "2025":
            return []
        return [{"account_nm": "매출액", "fs_div": "CFS", "thstrm_amount": "1,200", "frmtrm_amount": "1,000"}]


def _store_with_quote(tmp_path: Path) -> MarketStore:
    store = MarketStore(tmp_path / "m.sqlite")
    store.upsert(
        [{"basDt": "20260608", "srtnCd": "003220", "itmsNm": "대원제약", "mrktCtg": "KOSPI",
          "clpr": "18000", "mrktTotAmt": "606374887932"}]
    )
    return store


def test_build_fact_pack_grounded(tmp_path: Path) -> None:
    store = _store_with_quote(tmp_path)
    corp_map = {"003220": ("00111999", "대원제약")}
    pack = build_fact_pack(CAND, store, _FakeDart(with_disc=True), corp_map, ["pharma_bio"])
    assert isinstance(pack, FactPack)
    assert pack.price.as_of == "20260608" and pack.price.market_cap == 606374887932.0
    assert pack.price.mom_short_pct == 21.0  # 0.21 → %
    assert pack.fin_period == "2025/11014"   # 2026 전부 빈 → 2025 폴백
    assert pack.financials[0].account == "매출액" and pack.financials[0].yoy_pct == 20.0
    assert pack.disclosures[0].rcept_no == "20260515000123"
    assert pack.notes == []  # 결측 없음
    assert pack.as_of.tzinfo is not None  # KST aware
    store.close()


def test_build_fact_pack_missing_corp_and_disclosures(tmp_path: Path) -> None:
    store = _store_with_quote(tmp_path)
    # corp_map 비어 있음 → 공시·재무 미수집 note, 추측 안 함
    pack = build_fact_pack(CAND, store, _FakeDart(with_disc=False), {}, [])
    assert pack.financials == [] and pack.disclosures == []
    assert any("corp_code 없음" in n for n in pack.notes)
    assert pack.price.close == 18000.0  # 가격맥락은 여전히 grounded
    store.close()


def test_signals_from_series_surge_and_high() -> None:
    # (srtn_cd, name, market, bas_dt, clpr, hipr, tr_prc, mcap) 오름차순
    series = [(100, 1e9), (110, 1e9), (120, 5e9)]
    recs: list[tuple[object, ...]] = [
        ("X", "엑스", "KOSPI", f"2026060{i}", str(c), str(c), str(tr), "1e12")
        for i, (c, tr) in enumerate(series, 1)
    ]
    s = signals_from_series(recs, ScreenConfig(lookback_surge=2, mom_short=1, mom_long=2))
    assert round(s.tr_value_surge, 2) == 1.67   # 5e9 / ((1e9+5e9)/2)
    assert s.high_proximity == 1.0              # clpr 120 = 최고가
    assert round(s.mom_long, 2) == 0.2          # 120/100 - 1


def _seed_one(store: MarketStore, code: str, name: str) -> None:
    rows = [
        {"basDt": f"2026060{i}", "srtnCd": code, "itmsNm": name, "mrktCtg": "KOSPI",
         "clpr": str(c), "hipr": str(c), "trPrc": "1000000000", "mrktTotAmt": "1000000000000"}
        for i, c in enumerate([100, 110, 120], 1)
    ]
    store.upsert(rows)


def test_build_fact_pack_for_resolves_code_and_name(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    _seed_one(store, "003220", "대원제약")
    corp_map = {"003220": ("00111999", "대원제약")}
    pack = build_fact_pack_for("003220", store=store, dart=_FakeDart(with_disc=True), corp_map=corp_map)
    assert pack is not None and pack.srtn_cd == "003220" and pack.name == "대원제약"
    assert pack.price.close == 120.0 and pack.price.high_252_proximity == 1.0
    assert pack.fin_period == "2025/11014"        # DART grounding 연동
    assert any("단일종목" in n for n in pack.notes)
    # 이름으로도 해석(부분일치)
    by_name = build_fact_pack_for("대원", store=store, dart=_FakeDart(with_disc=False), corp_map=corp_map)
    assert by_name is not None and by_name.srtn_cd == "003220"
    store.close()


def test_build_fact_pack_for_unknown_returns_none(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    _seed_one(store, "003220", "대원제약")
    assert build_fact_pack_for("없는종목xyz", store=store, dart=_FakeDart(with_disc=False), corp_map={}) is None
    store.close()


def test_build_fact_pack_includes_news(tmp_path: Path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from trading.collectors.news import NewsStore, RawNews, normalize

    kst = ZoneInfo("Asia/Seoul")
    news_store = NewsStore(tmp_path / "news.sqlite")
    item = normalize(
        RawNews(title="대원제약 호재", url="https://yna.co.kr/x", publisher="연합뉴스",
                published_at=datetime(2026, 6, 8, 9, 0, tzinfo=kst)),
        source="naver", query="대원제약", entities=["003220"],
    )
    assert item is not None
    news_store.upsert([item])

    store = _store_with_quote(tmp_path)
    corp_map = {"003220": ("00111999", "대원제약")}
    pack = build_fact_pack(CAND, store, _FakeDart(with_disc=True), corp_map, ["pharma_bio"], news_store)
    assert len(pack.news) == 1 and pack.news[0].title == "대원제약 호재"
    assert pack.sources.get("news") is not None
    # news_store 없으면 빈 뉴스(하위호환)
    pack2 = build_fact_pack(CAND, store, _FakeDart(with_disc=True), corp_map, ["pharma_bio"])
    assert pack2.news == []
    news_store.close()
    store.close()


def test_build_fact_pack_no_disclosures_notes(tmp_path: Path) -> None:
    store = _store_with_quote(tmp_path)
    corp_map = {"003220": ("00111999", "대원제약")}
    pack = build_fact_pack(CAND, store, _FakeDart(with_disc=False), corp_map, ["pharma_bio"])
    assert any("공시 없음" in n for n in pack.notes)
    assert pack.fin_period == "2025/11014"  # 재무는 정상
    store.close()
