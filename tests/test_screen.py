"""R4 스크리너 — P-18 가치 코어 규칙·사이클 도구 플래그·탈락 전수 박제 테스트."""

from datetime import datetime, timedelta
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
         debt: float | None = 0.5, roe: float | None = 0.08,
         roe_median: float | None = 0.06) -> ValuationRecord:
    return ValuationRecord(
        id=f"val.20260826.{symbol}", as_of=TS, fetched_at=TS, source="derived:test",
        symbol=symbol, sector_krx=sector, pbr=pbr,
        roe=roe, roe_median_5y=roe_median, roe_years_observed=observed,
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


def test_evaluate_value_parallel_or_rule() -> None:
    """P-18 결재 ③: 산업 내 OR 시장 전체 — 하나만 충족해도 가치 성립."""
    ok = _val("000001", sector="금속")
    passed, reasons = evaluate(
        ok, industry="철강", secular_decline=False,
        industry_pbr_pct=0.2, market_pbr_pct=0.9, params=PROPOSED_R4,
    )
    assert (passed, reasons) == (True, [])

    # 산업 내 미산출(소표본)이어도 시장 전체가 싸면 통과 — 혼성·소표본 보정
    passed2, reasons2 = evaluate(
        ok, industry="유통", secular_decline=None,
        industry_pbr_pct=None, market_pbr_pct=0.1, params=PROPOSED_R4,
    )
    assert (passed2, reasons2) == (True, [])

    # 둘 다 초과 → 가치 미달(두 수치 병기)
    _, reasons3 = evaluate(
        ok, industry="철강", secular_decline=False,
        industry_pbr_pct=0.7, market_pbr_pct=0.6, params=PROPOSED_R4,
    )
    assert any(r.startswith("가치 미달") and "산업 내 70%" in r and "시장 전체 60%" in r for r in reasons3)

    # 둘 다 미산출 → 상대 위치 미산출 탈락
    _, reasons4 = evaluate(
        ok, industry="철강", secular_decline=False,
        industry_pbr_pct=None, market_pbr_pct=None, params=PROPOSED_R4,
    )
    assert any("상대 위치 미산출" in r for r in reasons4)


def test_evaluate_no_zone_gate_but_secular_and_survival_kept() -> None:
    """P-18: 발동 존은 게이트가 아님 — 사양 가드·생존력 필터는 유지."""
    bad = _val("000002", sector="금속", losses=3)
    passed, reasons = evaluate(
        bad, industry="철강", secular_decline=True,
        industry_pbr_pct=0.9, market_pbr_pct=0.9, params=PROPOSED_R4,
    )
    assert not passed and len(reasons) == 3  # 사양·가치·적자 (존 사유는 더 이상 없음)
    assert not any("발동 존" in r for r in reasons)
    assert any(r.startswith("구조적 사양 산업") for r in reasons)
    assert any(r.startswith("적자 상한 초과") for r in reasons)


def test_evaluate_honest_gaps_and_debt_exemption() -> None:
    thin = _val("000003", sector="금속", observed=2, debt=None)
    _, reasons = evaluate(
        thin, industry="철강", secular_decline=False,
        industry_pbr_pct=0.1, market_pbr_pct=0.1, params=PROPOSED_R4,
    )
    assert any("관측 부족" in r for r in reasons) and any("부채비율 미산출" in r for r in reasons)

    # 금융업 면제 — P-18 전 상장 확장으로 KRX 버킷 산업명(금융·보험)도 면제
    for fin_industry in ("은행", "증권", "금융", "보험"):
        bank = _val("000004", sector="금융", debt=None)
        passed, reasons2 = evaluate(
            bank, industry=fin_industry, secular_decline=False,
            industry_pbr_pct=0.1, market_pbr_pct=0.1, params=PROPOSED_R4,
        )
        assert passed and reasons2 == [], fin_industry


def test_value_trap_filters_v12() -> None:
    """v1.2(운영자 2026-08-28): 최신 연간 적자 또는 만성 저수익은 저PBR이어도 탈락."""
    latest_loss = _val("051910", sector="화학", roe=-0.02)
    _, reasons = evaluate(
        latest_loss, industry="화학", secular_decline=False,
        industry_pbr_pct=0.07, market_pbr_pct=0.2, params=PROPOSED_R4,
    )
    assert any(r.startswith("가치 함정 방어") for r in reasons)

    chronic = _val("000005", sector="금속", roe=0.01, roe_median=0.01)
    _, reasons2 = evaluate(
        chronic, industry="철강", secular_decline=False,
        industry_pbr_pct=0.1, market_pbr_pct=0.1, params=PROPOSED_R4,
    )
    assert any(r.startswith("만성 저수익") for r in reasons2)


def test_run_screen_two_pass_flags_and_persistence(tmp_path: Path) -> None:
    vs = ValuationStore(tmp_path / "v.sqlite")
    cs = CycleStore(tmp_path / "c.sqlite")
    # 1패스 — 철강(금속(큐레이션)) 실멤버 3종: 과열 국면이어도 가치 통과분은 플래그만(결재 ①)
    for sym, pbr in (("005490", 0.4), ("004020", 1.0), ("010130", 2.0)):
        vs.append(_val(sym, sector="금속", pbr=pbr))
    cs.append(_cyc("금속(큐레이션)", CyclePhase.OVERHEATED))
    # 조선(큐레이션): 고PBR — 가치 미달로 탈락
    vs.append(_val("329180", sector="운송장비·부품", pbr=3.0))
    cs.append(_cyc("조선(큐레이션)", CyclePhase.OVERHEATED))
    # 2패스 — 비큐레이션 종목(유통 버킷, 국면 레코드 없음): 시장 전체 기준으로 통과
    vs.append(_val("000001", sector="유통", pbr=0.5))

    records, s = run_screen(vs, cs, params=PROPOSED_R4, now=TS)
    by_sym = {r.symbol: r for r in records}

    steel = by_sym["005490"]
    assert steel.passed and steel.industry == "철강"
    assert steel.phase is CyclePhase.OVERHEATED and steel.cycle_caution  # 플래그만, 탈락 아님
    assert steel.market_pbr_pct is not None

    assert not by_sym["004020"].passed  # 산업·시장 병행 기준 모두 초과
    assert any("가치 미달" in r for r in by_sym["010130"].reject_reasons)

    retail = by_sym["000001"]
    assert retail.passed and retail.industry == "유통"
    assert retail.phase is CyclePhase.UNKNOWN and retail.cycle_ref is None
    assert not retail.cycle_caution
    assert retail.industry_pbr_pct is None  # 버킷 표본 1 < 3 — 시장 기준으로 통과

    ship = by_sym["329180"]
    assert ship.industry == "조선" and not ship.passed
    assert not any("발동 존" in r for r in ship.reject_reasons)  # 존은 더 이상 탈락 사유가 아님

    assert len(s.skipped_industries) >= 7  # 국면 레코드 없는 산업들 — 심사는 수행됨(정보 고지)
    assert s.evaluated == 5 and s.passed == 2
    assert steel.unapplied  # 미적용 필터 명시(침묵 생략 금지)

    store = CandidateStore(tmp_path / "cand.sqlite")
    for r in records:
        store.append(r)
    assert {c.symbol for c in store.latest_passed()} == {"005490", "000001"}
    store.close()
    vs.close()
    cs.close()


def test_min_roe_latest_v17_nominal_profit_rejected() -> None:
    """v1.7(운영자 결재 2026-09-01): 명목 흑자(0<ROE<1%)는 가치 함정 방어로 탈락."""
    nominal = _val("900340", sector="금융", roe=0.005, roe_median=0.05)
    _, reasons = evaluate(
        nominal, industry="금융", secular_decline=False,
        industry_pbr_pct=0.05, market_pbr_pct=0.1, params=PROPOSED_R4,
    )
    assert any(r.startswith("가치 함정 방어") for r in reasons)

    meaningful = _val("051910", sector="화학", roe=0.015, roe_median=0.05)
    passed, reasons2 = evaluate(
        meaningful, industry="화학", secular_decline=False,
        industry_pbr_pct=0.05, market_pbr_pct=0.1, params=PROPOSED_R4,
    )
    assert not any(r.startswith("가치 함정 방어") for r in reasons2)


# --- v2.16 SCREEN-1 하드 필터(운영자 결재 (a) 2026-09-03) — 관리종목·거래정지·상폐 의심·감사의견 비적정 ---

def test_status_filter_pure_rules() -> None:
    from trading.collectors.audit import AuditVerdict
    from trading.collectors.status import KisFlags
    from trading.screen.rules import (
        UNAPPLIED_AUDIT,
        UNAPPLIED_AUDIT_MISSING,
        UNAPPLIED_STATUS,
        status_filter,
    )

    ok = KisFlags(managed=False, halted=False, delisted_suspect=False, warn_code="00")
    clean = AuditVerdict(state="clean", opinion="적정의견", rcept_no="20260310002820", adtor="삼정회계법인")
    assert status_filter(ok, clean) == ([], [])
    # 데이터 없음 → 탈락 아님, 미적용 고지(침묵 생략 금지)
    assert status_filter(None, None) == ([], [UNAPPLIED_STATUS, UNAPPLIED_AUDIT])
    assert status_filter(ok, AuditVerdict("missing", None, None, None)) == ([], [UNAPPLIED_AUDIT])
    # 당기 결측(adtor '-') → 추측 탈락 금지, ⚠ 고지
    assert status_filter(ok, AuditVerdict("unaudited", None, "20260101000001", None)) == ([], [UNAPPLIED_AUDIT_MISSING])
    # 관리·정지·상폐 의심 → 탈락 사유(관측 근거 명시)
    r, u = status_filter(KisFlags(managed=True, halted=False, delisted_suspect=False, warn_code="00"), clean)
    assert r == ["관리종목(KIS mang_issu_cls_code=Y)"] and u == []
    r, _ = status_filter(KisFlags(managed=True, halted=True, delisted_suspect=False, warn_code="00"), clean)
    assert len(r) == 2 and any("매매거래정지" in x for x in r)
    r, u = status_filter(KisFlags(managed=None, halted=None, delisted_suspect=True, warn_code=None), clean)
    assert r == ["상장폐지/무자료 의심(KIS 상태 00·현재가 0)"] and u == []
    # 상태 필드 결측(None·None) → 판독 불가 고지
    _, u = status_filter(KisFlags(managed=None, halted=None, delisted_suspect=False, warn_code=None), clean)
    assert u == ["관리종목·거래정지 판독 불가(KIS 상태 필드 결측)"]
    # 감사의견 비적정(관측 어휘 의견거절·미관측 어휘도 ≠적정의견이면 동일)
    r, _ = status_filter(ok, AuditVerdict("adverse", "의견거절", "20260323000029", "대주회계법인"))
    assert r == ["감사의견 비적정(의견거절 — 대주회계법인, 접수 20260323000029)"]


def test_run_screen_applies_status_and_audit_inputs(tmp_path: Path) -> None:
    from trading.collectors.audit import AuditVerdict
    from trading.collectors.status import KisFlags

    vs = ValuationStore(tmp_path / "v.sqlite")
    cs = CycleStore(tmp_path / "c.sqlite")
    for sym in ("000001", "000002", "000003", "000004", "000005"):
        vs.append(_val(sym, sector="유통", pbr=0.5))        # 전부 가치·건전성 통과 조건
    for i in range(10):                                      # 고PBR 대조군 — 시장 percentile 기준 성립용(가치 미달 탈락)
        vs.append(_val(f"9{i:05d}", sector="화학", pbr=5.0))
    ok = KisFlags(managed=False, halted=False, delisted_suspect=False, warn_code="00")
    flags = {
        "000001": ok,
        "000002": KisFlags(managed=True, halted=False, delisted_suspect=False, warn_code="00"),  # 관리종목
        "000003": ok,
        "000004": KisFlags(managed=None, halted=None, delisted_suspect=True, warn_code=None),   # 상폐 의심
        # 000005: 스냅샷 없음 → 미적용 고지
    }
    audits = {
        "000001": AuditVerdict("clean", "적정의견", "20260310000001", "A회계법인"),
        "000002": AuditVerdict("clean", "적정의견", "20260310000002", "A회계법인"),
        "000003": AuditVerdict("adverse", "의견거절", "20260323000003", "B회계법인"),   # 비적정
        "000005": AuditVerdict("unaudited", None, "20260101000005", None),           # 당기 결측
    }
    records, s = run_screen(vs, cs, params=PROPOSED_R4, now=TS, kis_flags=flags, audits=audits)
    by = {r.symbol: r for r in records}
    assert by["000001"].passed and not any("관리종목" in u or "감사의견" in u for u in by["000001"].unapplied)
    assert not by["000002"].passed and by["000002"].reject_reasons == ["관리종목(KIS mang_issu_cls_code=Y)"]
    assert not by["000003"].passed and by["000003"].reject_reasons[0].startswith("감사의견 비적정(의견거절")
    assert not by["000004"].passed and "상장폐지/무자료 의심" in by["000004"].reject_reasons[0]
    five = by["000005"]
    assert five.passed  # 데이터 부재·당기 결측은 탈락 아님 — 고지만
    assert any(u.startswith("관리종목·거래정지 필터") for u in five.unapplied)
    assert any(u.startswith("감사의견 당기 결측") for u in five.unapplied)
    assert s.passed == 2 and s.reject_counts["관리종목"] == 1 and s.reject_counts["감사의견 비적정"] == 1
    # 입력 없이 호출(기존 경로) → 전 종목 미적용 고지, 판정 불변(대조군 10종은 가치 미달)
    records0, _ = run_screen(vs, cs, params=PROPOSED_R4, now=TS)
    assert all(any(u.startswith("관리종목·거래정지 필터") for u in r.unapplied) for r in records0)
    assert sum(1 for r in records0 if r.passed) == 5
    vs.close()
    cs.close()


def test_load_status_inputs_freshness_and_audit(tmp_path: Path) -> None:
    from trading.collectors.status import StatusStore
    from trading.screen.run import STATUS_MAX_AGE_DAYS, load_status_inputs

    db = tmp_path / "status.sqlite"
    st = StatusStore(db)
    env = {"iscd_stat_cls_code": "51", "mang_issu_cls_code": "Y", "stck_prpr": "1000"}
    st.append_kis("000001", "2026-09-03", env, fetched_at="t")
    stale = (TS - timedelta(days=STATUS_MAX_AGE_DAYS + 1)).strftime("%Y-%m-%d")
    st.append_kis("000002", stale, env, fetched_at="t")
    rows = [{"rcept_no": "20260310000001", "bsns_year": "제10기(당기)", "adtor": "A", "adt_opinion": "적정의견"}]
    st.append_audit("000001", "C1", "2025", "11011", rows, fetched_at="t")
    st.close()
    flags, audits = load_status_inputs(fy="2025", db_path=db, now=TS + timedelta(days=1))
    assert set(flags) == {"000001"} and flags["000001"].managed is True   # 오래된 스냅샷은 미적용
    assert audits["000001"].state == "clean" and set(audits) == {"000001"}
