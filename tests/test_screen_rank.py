"""screen.rank — P-18 정렬 도구(게이트 아님) 단위 테스트."""

from datetime import datetime, timezone, timedelta

from trading.contracts.longterm import CandidateRecord, CyclePhase
from trading.screen.rank import rank_key, shortlist, value_depth

TS = datetime(2026, 9, 1, 9, 0, tzinfo=timezone(timedelta(hours=9)))


def _cand(
    symbol: str,
    industry: str = "화학",
    phase: CyclePhase = CyclePhase.BOTTOMING,
    industry_pct: float | None = 0.10,
    market_pct: float | None = 0.50,
    passed: bool = True,
) -> CandidateRecord:
    return CandidateRecord(
        id=f"cand.20260901.{symbol}.{industry}", as_of=TS, fetched_at=TS,
        source="derived:test", symbol=symbol, industry=industry, phase=phase,
        passed=passed, reject_reasons=[] if passed else ["가치 미달(테스트)"],
        industry_pbr_pct=industry_pct, market_pbr_pct=market_pct,
        cycle_caution=phase is CyclePhase.OVERHEATED, valuation_ref="val.x",
    )


def test_value_depth_min_of_parallel_axes() -> None:
    # 병행 기준 미러: 두 축 중 깊은 쪽. 결측 축은 무시, 전결측=1.0(최하위)
    assert value_depth(_cand("A", industry_pct=0.10, market_pct=0.70)) == 0.10
    assert value_depth(_cand("B", industry_pct=None, market_pct=0.30)) == 0.30
    assert value_depth(_cand("C", industry_pct=None, market_pct=None)) == 1.0


def test_phase_priority_beats_value_depth() -> None:
    # 발동 존이 먼저 — 과열은 아무리 깊어도 회복 존 뒤
    deep_overheated = _cand("111111", phase=CyclePhase.OVERHEATED, industry_pct=0.01)
    shallow_recovering = _cand("222222", phase=CyclePhase.RECOVERING, industry_pct=0.35)
    out = shortlist([deep_overheated, shallow_recovering])
    assert [r.symbol for r in out] == ["222222", "111111"]


def test_roe_quality_tiebreak_and_determinism() -> None:
    a = _cand("333333", industry_pct=0.10)
    b = _cand("444444", industry_pct=0.10)
    roe = {"444444": 0.12, "333333": 0.04}
    out = shortlist([a, b], roe_by_symbol=roe)
    assert [r.symbol for r in out] == ["444444", "333333"]
    # ROE 결측/동률이면 심볼 오름차순 — 실행마다 같은 순서
    out2 = shortlist([b, a])
    assert [r.symbol for r in out2] == ["333333", "444444"]
    assert rank_key(a, {}) < rank_key(b, {})


def test_per_industry_cap_forces_diversification() -> None:
    chems = [_cand(f"10000{i}", industry="화학", industry_pct=0.01 * (i + 1)) for i in range(8)]
    it = _cand("500001", industry="IT 서비스", industry_pct=0.30)
    out = shortlist([*chems, it], per_industry_cap=5)
    assert sum(1 for r in out if r.industry == "화학") == 5
    assert any(r.industry == "IT 서비스" for r in out)  # 캡 덕에 얕아도 진입


def test_symbol_dedup_keeps_best_record_and_skips_failed() -> None:
    # 다중 소속(큐레이션 산업 2곳) 종목은 최상위 레코드 1건만
    dual_a = _cand("666666", industry="메모리반도체", industry_pct=0.05)
    dual_b = _cand("666666", industry="파운드리", industry_pct=0.20)
    failed = _cand("777777", passed=False)
    out = shortlist([dual_b, dual_a, failed])
    assert [r.symbol for r in out] == ["666666"]
    assert out[0].industry == "메모리반도체"


def test_top_n_bound() -> None:
    many = [
        _cand(f"60{i:04d}", industry=f"산업{i % 20}", industry_pct=0.001 * i)
        for i in range(60)
    ]
    assert len(shortlist(many, top_n=40)) == 40


def test_high_per_demoted_within_phase_not_excluded() -> None:
    # v1.7 이익 축: 같은 국면에서 고PER은 깊이가 깊어도 클린 종목 뒤로 — 단 탈락은 아님
    deep_expensive = _cand("888888", industry="외국증권", industry_pct=0.01)
    shallow_clean = _cand("999990", industry="유통", industry_pct=0.30)
    per = {"888888": 338.0, "999990": 8.0}
    out = shortlist([deep_expensive, shallow_clean], per_by_symbol=per)
    assert [r.symbol for r in out] == ["999990", "888888"]


def test_per_missing_is_not_demoted_and_threshold_param() -> None:
    known_cheap = _cand("121212", industry_pct=0.20)
    missing = _cand("131313", industry_pct=0.10)
    out = shortlist([known_cheap, missing], per_by_symbol={"121212": 8.0})
    assert [r.symbol for r in out] == ["131313", "121212"]  # 결측은 강등 없음 — 깊이 순
    # 임계 파라미터: 12로 낮추면 PER 13도 강등
    out2 = shortlist(
        [_cand("141414", industry_pct=0.05), _cand("151515", industry_pct=0.30)],
        per_by_symbol={"141414": 13.0, "151515": 9.0},
        high_per_threshold=12.0,
    )
    assert [r.symbol for r in out2] == ["151515", "141414"]
