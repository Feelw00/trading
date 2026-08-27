"""R4 스크리너 — 규칙·발동 존·탈락 전수 박제·큐레이션 멤버십 테스트."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.contracts.longterm import CyclePhase, CycleRecord, PrimaryAxes, ValuationRecord
from trading.cycle.store import CycleStore
from trading.screen import PROPOSED_R4, run_screen
from trading.screen.rules import evaluate
from trading.screen.store import CandidateStore
from trading.valuation.store import ValuationStore

KST = ZoneInfo("Asia/Seoul")
TS = datetime(2026, 8, 27, 18, 0, tzinfo=KST)


def _val(symbol: str, *, sector: str | None, pbr: float | None = 0.8,
         losses: int | None = 0, observed: int | None = 5,
         debt: float | None = 0.5) -> ValuationRecord:
    return ValuationRecord(
        id=f"val.20260826.{symbol}", as_of=TS, fetched_at=TS, source="derived:test",
        symbol=symbol, sector_krx=sector, pbr=pbr,
        loss_years_5y=losses, loss_years_observed=observed, debt_ratio=debt,
    )


def _cyc(industry: str, phase: CyclePhase, *, secular: bool | None = False) -> CycleRecord:
    axes = (
        PrimaryAxes(sector_pbr_band_pct=0.1, sector_margin_band_pct=0.2, sector_rev_cycle_z=0.5)
        if phase is not CyclePhase.UNKNOWN
        else PrimaryAxes()
    )
    return CycleRecord(
        id=f"cyc.20260827.{industry}", as_of=TS, fetched_at=TS, source="derived:test",
        industry=industry, phase=phase,
        temperature=15 if phase is not CyclePhase.UNKNOWN else None,
        axes_primary=axes, secular_decline=secular,
    )


def test_evaluate_pass_and_rejects() -> None:
    ok = _val("000001", sector="금속")
    passed, reasons = evaluate(
        ok, industry="철강", phase=CyclePhase.BOTTOMING, secular_decline=False,
        industry_pbr_pct=0.2, params=PROPOSED_R4,
    )
    assert (passed, reasons) == (True, [])

    # 존 밖 + 가치 미달 + 적자 초과 — 사유가 전부 쌓인다(전수 박제)
    bad = _val("000002", sector="금속", losses=3)
    passed2, reasons2 = evaluate(
        bad, industry="철강", phase=CyclePhase.OVERHEATED, secular_decline=True,
        industry_pbr_pct=0.9, params=PROPOSED_R4,
    )
    assert not passed2 and len(reasons2) == 4  # 존·사양·가치·적자
    assert any(r.startswith("적자 상한 초과") for r in reasons2)  # 분포 집계용 안정 라벨


def test_evaluate_honest_gaps_and_debt_exemption() -> None:
    thin = _val("000003", sector="금속", observed=2, debt=None)
    _, reasons = evaluate(
        thin, industry="철강", phase=CyclePhase.BOTTOMING, secular_decline=False,
        industry_pbr_pct=0.1, params=PROPOSED_R4,
    )
    assert any("관측 부족" in r for r in reasons) and any("부채비율 미산출" in r for r in reasons)

    bank = _val("000004", sector="금융", debt=None)  # 금융업 — 부채 필터 면제
    passed, reasons2 = evaluate(
        bank, industry="은행", phase=CyclePhase.BOTTOMING, secular_decline=False,
        industry_pbr_pct=0.1, params=PROPOSED_R4,
    )
    assert passed and reasons2 == []


def test_run_screen_zone_membership_and_persistence(tmp_path: Path) -> None:
    vs = ValuationStore(tmp_path / "v.sqlite")
    cs = CycleStore(tmp_path / "c.sqlite")
    # 철강(금속 버킷): bottoming 존 — 3종목(percentile 산출 가능), 1종목만 저PBR
    for sym, pbr in (("000001", 0.4), ("000002", 1.0), ("000003", 2.0)):
        vs.append(_val(sym, sector="금속", pbr=pbr))
    cs.append(_cyc("금속", CyclePhase.BOTTOMING))
    # 조선(큐레이션): overheated 존 밖 — 멤버십은 명시 리스트로
    vs.append(_val("329180", sector="운송장비·부품", pbr=3.0))
    cs.append(_cyc("조선(큐레이션)", CyclePhase.OVERHEATED))
    # 나머지 화이트리스트 산업은 국면 레코드 없음 → skipped로 정직 보고

    records, s = run_screen(vs, cs, params=PROPOSED_R4, now=TS)
    by_sym = {r.symbol: r for r in records}
    assert by_sym["000001"].passed and by_sym["000001"].industry == "철강"
    assert not by_sym["000002"].passed  # 하위 50% > 40%
    assert any("가치 미달" in r for r in by_sym["000003"].reject_reasons)
    ship = by_sym["329180"]
    assert ship.industry == "조선" and not ship.passed
    assert any("발동 존 아님" in r for r in ship.reject_reasons)
    assert len(s.skipped_industries) >= 4  # 국면 미산출 산업 정직 보고
    assert by_sym["000001"].unapplied  # 미적용 필터 명시(침묵 생략 금지)

    store = CandidateStore(tmp_path / "cand.sqlite")
    for r in records:
        store.append(r)
    assert [c.symbol for c in store.latest_passed()] == ["000001"]
    store.close()
    vs.close()
    cs.close()
