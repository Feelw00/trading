"""디스패치 엔트리포인트 — openclaw cron이 ``python -m trading.run <round>`` 로 호출.

``ROUNDS``: 라운드명 → 핸들러(종료코드). 핸들러는 **결정론** 수집/스크리너/fact pack을 호출
(LLM 미개입). 수집기는 lazy import(엔트리 가벼움). 스케줄은 ``ops/openclaw/cron_jobs.py``(선언적).
시장 휴장일은 스케줄러가 아니라 각 잡 내부 가드가 처리(SCHED 결정) — data.go.kr은 휴장일 빈 결과.
"""

import sys
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.alerts import AlertDispatcher


def _collect_macro() -> int:
    from trading.collectors import macro

    return macro.main()


def _collect_market() -> int:
    from trading.collectors import market

    return market.main()


def _collect_flows() -> int:
    from trading.collectors import flows

    return flows.run()


def _collect_news() -> int:
    from trading import collect_news

    return collect_news.run()


def _classify_sectors() -> int:
    from trading import sectors

    return sectors.main()


def _sector_llm() -> int:
    from trading import sector_llm

    return sector_llm.main()


def _collect_fins() -> int:
    from trading.collectors import fins

    return fins.main()


def _swing() -> int:
    from trading import swing

    return swing.main()


def _arm_watch() -> int:
    """장중 발동 감시 루프(순수 코드, 15:00 자기 종료) — cron 09:00 기동·12:00 재기동."""
    from trading.watch.arm_watch import run_loop

    return run_loop()


def _screen() -> int:
    from trading import screener

    return screener.main()


def _factpack() -> int:
    from trading import factpack

    factpack.run()
    return 0


def _score_news() -> int:
    from trading import score_news

    return score_news.run()


def _verify_catalysts() -> int:
    from trading import verify_news

    return verify_news.run()


def _reason_theses() -> int:
    from trading import reason_news

    return reason_news.run()


def _synth_playbooks() -> int:
    from trading import synth_playbooks

    rc = synth_playbooks.run()
    if rc == 0:
        _auto_approve_after_synth()
    return rc


def _auto_approve_after_synth() -> None:
    """R5 산출 직후 자동 승인 + P0 통지(EXEC-1 — 거부권 안내 동봉). 실패해도 synth는 성공."""
    from trading.approve import auto_approve_pending
    from trading.executor import exec_mode

    if exec_mode() == "off":
        print("자동 승인 스킵 — EXEC off(킬 스위치)")
        return
    try:
        ids = auto_approve_pending()
    except Exception as exc:  # noqa: BLE001 — 승인 실패는 보고만(다음날 수동 승인 가능)
        print(f"자동 승인 실패(수동 승인 가능): {exc}")
        return
    if not ids:
        print("자동 승인: 대상 없음")
        return
    print(f"자동 승인 {len(ids)}건: {', '.join(ids)}")
    from trading.alerts import Alert, AlertDispatcher, Severity
    from trading.collectors.base import now_kst

    AlertDispatcher().notify(
        Alert(
            severity=Severity.P0,
            what=f"자동 승인 {len(ids)}건 — 내일 감시 풀\n{_approved_digest(ids)}",
            rule="EXEC-1: R5 하드게이트 통과분 자동 승인(당일 생성분 한정)",
            action="거부: <code>python -m trading.approve --veto &lt;id&gt;</code> (id는 /positions·저녁 보고 참조)",
            deadline="다음 거래일 09:00(감시 기동) 전",
            created_at=now_kst(),
        )
    )


def _approved_digest(ids: list[str]) -> str:
    """승인분 다이제스트 — 종목명·손절/경고/익절·시계(운영자 피드백: 기계 ID 나열 금지)."""
    from trading.journal.playbooks import PlaybookStore
    from trading.position_check import _symbol_names_safe

    ps = PlaybookStore()
    lines: list[str] = []
    try:
        drafts = [d for d in (ps.draft(i) for i in ids) if d is not None]
        names = _symbol_names_safe([d.symbol for d in drafts])
        for d in drafts:
            nm = names.get(d.symbol) or d.symbol
            stop = f"{d.stop.level:,.0f}" if d.stop and d.stop.level else "시간손절"
            soft = f" 경고 {d.soft_stop.level:,.0f} ·" if d.soft_stop else ""
            tgt = "→".join(f"{t.level:,.0f}" for t in d.targets) if d.targets else "R:R 자동"
            lines.append(
                f"• {nm}({d.symbol}) — 손절 {stop} ·{soft} 익절 {tgt} · {d.time_stop_days or '-'}일"
            )
    except Exception:  # noqa: BLE001 — 다이제스트 실패는 통지 자체를 막지 않는다
        return "\n".join(f"• <code>{i}</code>" for i in ids)
    finally:
        ps.close()
    return "\n".join(lines)


def _select_playbooks() -> int:
    from trading import select_playbooks

    return select_playbooks.run()


def _alerts_digest() -> int:
    from trading.alerts import dispatch

    return dispatch.main()


def _evaluate() -> int:
    from trading import evaluate

    return evaluate.run()


def _refresh_macro_then_report(kind: str) -> int:
    """보고 직전 거시 재수집(결정론 어댑터) → 렌더.

    수집을 독립 cron 슬롯이 아니라 보고 라운드에 내장 — 트리거 에이전트 턴을 줄여
    수집 경로에서 LLM 개입 여지를 제거한다(COLLECT-3). 재수집이 실패해도 보고는
    기존 landing으로 진행한다(결측·as_of 명시 정책) — 단 실패는 P1로 띄운다.
    """
    rc = _collect_macro()
    if rc != 0:
        print(
            f"[report-{kind}] 거시 재수집 실패 rc={rc} — 기존 landing으로 보고 진행",
            file=sys.stderr,
        )
        _alert_round_failure(f"report-{kind}/collect-macro", f"rc={rc}")

    from trading import report

    return report.run(kind)


def _report_morning() -> int:
    return _refresh_macro_then_report("morning")


def _report_evening() -> int:
    return _refresh_macro_then_report("evening")


def _daily_eod() -> int:
    """EOD 디스커버리 파이프라인: 전종목 → 섹터분류(+LLM 폴백) → 스크리너 → 수급·재무 → 스윙 → fact pack.

    수급(KIS)·재무(DART)·섹터 LLM 폴백·스윙은 best-effort — 실패해도 P1만 띄우고 계속
    (결측은 각 소비자가 명시). 나머지 단계는 첫 실패에서 중단.
    섹터 LLM 폴백은 §5 휴면 대상 아님 — 분류 메타데이터 태깅이지 매매 판단 라운드가 아니다
    (신규 게이트 진입 종목만이라 통상 0콜).
    """
    for step in (_collect_market, _classify_sectors):
        rc = step()
        if rc != 0:
            return rc
    llm_rc = _sector_llm()
    if llm_rc != 0:
        _alert_round_failure("daily-eod/sector-llm", f"rc={llm_rc}")
    rc = _screen()
    if rc != 0:
        return rc
    for name, step in (("collect-flows", _collect_flows), ("collect-fins", _collect_fins), ("swing", _swing)):
        step_rc = step()
        if step_rc != 0:
            _alert_round_failure(f"daily-eod/{name}", f"rc={step_rc}")
    return _factpack()


def _whitelist_members() -> tuple[dict[str, str], str | None]:
    """화이트리스트 산업 멤버 {코드: 이름}과 시세 최신일 — 수급·토스 사실 축적의 공통 대상."""
    from trading.collectors.market import MarketStore
    from trading.cycle.policy import CURATED_GROUPS, WHITELIST
    from trading.sectors import KRX_SOURCE

    mstore = MarketStore()
    try:
        bas_dt = mstore.latest_date()
        groups = set(WHITELIST.values())
        sector_map = mstore.sector_map(KRX_SOURCE)
        names = mstore.sector_names(KRX_SOURCE)
        members: dict[str, str] = {}
        for cd, tags in sector_map.items():
            if tags and tags[0] in groups:
                members[cd] = names.get(cd, cd)
        for codes in CURATED_GROUPS.values():
            for cd in codes:
                members[cd] = names.get(cd, cd)
        return members, bas_dt
    finally:
        mstore.close()


def _collect_flows_v3() -> int:
    """v0.3 수급 창 축적 — 화이트리스트 산업 멤버 전 종목(KIS 1콜≈30거래일, 일간 누적).

    PIVOT-7 ④: 수급은 네거티브 스크린·타이밍 보조 — 60~120거래일 창이 원료.
    """
    from trading.collectors import flows
    from trading.collectors.kis import client_from_env

    client = client_from_env()
    if client is None:
        print("KIS_APP_KEY/KIS_APP_SECRET 미설정 — 수급 축적 blocked")
        return 0
    members, bas_dt = _whitelist_members()
    if not bas_dt:
        print("시세 DB 비어 있음 — 수급 기준일 없음(수집 선행)")
        return 0

    store = flows.FlowStore()
    result = flows.collect(client, store, sorted(members.items()), bas_dt)
    store.close()
    ok = sum(1 for v in result.values() if v >= 0)
    fail = sum(1 for v in result.values() if v < 0)
    print(f"수급 축적: 대상 {len(result)} · 성공 {ok} · 실패 {fail} (기준일 {bas_dt})")
    return 0


def _collect_toss_facts_v3() -> int:
    """v0.3 토스 사실 축적 — 공매도·대차·신용 일별(화이트리스트 멤버, PIVOT-10).

    보수 페이싱(1.1s/콜)으로 3종×멤버 ≈ 8분 — eod 체인 best-effort 단계.
    당일 잠정 행은 store가 제외(관측 근거는 toss_facts 모듈 주석).
    """
    from trading.collectors.base import now_kst
    from trading.collectors.toss import client_from_env
    from trading.collectors.toss_facts import TossFactsStore, collect_stock_facts

    client = client_from_env()
    if client is None:
        print("TOSS 키 미설정 — 토스 사실 축적 blocked")
        return 0
    members, _bas_dt = _whitelist_members()
    if not members:
        print("화이트리스트 멤버 없음 — 섹터 태깅 선행")
        return 0
    store = TossFactsStore()
    try:
        added, calls, errors = collect_stock_facts(
            client, store, sorted(members), today=now_kst().strftime("%Y-%m-%d")
        )
        cov = store.coverage()
    finally:
        store.close()
    print(f"토스 사실 축적: 신규 {added}행 · 호출 {calls} · 실패 {len(errors)}")
    for kind, (syms, days, latest) in sorted(cov.items()):
        print(f"  {kind}: {syms}종목 · {days}일자 · 최신 {latest}")
    for e in errors[:5]:
        print(f"  ⚠️ {e}")
    return 0


def _eod_v3() -> int:
    """v0.3 일간 EOD 체인(§5) — 시세 갭 치유 → 섹터 태깅 → 재무 자연 갱신 → 수급·토스 사실 축적.

    전부 순수 코드(LLM 없음). 재무·수급·토스 사실은 best-effort(실패해도 P1만, 결측은
    소비자가 명시). 논제 가드(보유 무효화 검사)는 Phase 4에서 보유 연결과 함께 배선 — 현재 보유 0.
    """
    for step in (_collect_market, _classify_sectors):
        rc = step()
        if rc != 0:
            return rc
    for name, step in (
        ("collect-fins", _collect_fins),
        ("flows-v3", _collect_flows_v3),
        ("toss-facts-v3", _collect_toss_facts_v3),
        # v2.6(운영자 결재 2026-09-01, 전면 자동화): 가격 추종 산출은 일간으로 —
        # 밸류에이션(PBR·여력은 시세 함수) + 페이퍼 트리거 재생(정의가 일별 종가)
        ("valuation-daily", _valuation_v3),
        ("paper-mark", _paper_mark),
    ):
        step_rc = step()
        if step_rc != 0:
            _alert_round_failure(f"eod-v3/{name}", f"rc={step_rc}")
    print("논제 가드: 보유 0 — 검사 대상 없음(Phase 4에서 보유 연결)")
    return 0


def _valuation_v3() -> int:
    from trading.valuation.build import main as valuation_main

    return valuation_main()


def _paper_mark() -> int:
    """페이퍼 트리거 일간 재생(멱등) — 실주문 없음."""
    import sys as _sys

    argv, _sys.argv = _sys.argv, ["trading.paper"]
    try:
        from trading.paper import main as paper_main

        return paper_main()
    finally:
        _sys.argv = argv


def _owner_equity_v3() -> int:
    from trading.collect_owner_equity import main as oe_main

    return oe_main()


def _returns_v3() -> int:
    """환원·분할 증분 수집 — argv 가드(체인 인자 'weekly-v3'가 연수로 오파싱되지 않게)."""
    import sys as _sys

    argv, _sys.argv = _sys.argv, ["trading.collect_returns"]
    try:
        from trading.collect_returns import main as returns_main

        return returns_main()
    finally:
        _sys.argv = argv


def _review_auto_v3() -> int:
    """자동 심사(rule-v1) + 원장 요약 로그 — 순수 코드, 수동 판정 우선."""
    from trading.review import ReviewStore, auto_review, latest_annual_year

    year = latest_annual_year()
    store = ReviewStore()
    try:
        n_appr, n_hold = auto_review(store, year)
        print(f"자동 심사: 승인 {n_appr} · 조건부 {n_hold} (기준연도 {year})")
        return 0
    finally:
        store.close()


def _paper_register_v3() -> int:
    """승인 ∧ 여력 ≥30% 신규 종목 자동 등록(멱등) — 페이퍼 원장."""
    import sys as _sys

    argv, _sys.argv = _sys.argv, ["trading.paper", "register"]
    try:
        from trading.paper import main as paper_main

        return paper_main()
    finally:
        _sys.argv = argv


def _weekly_v3() -> int:
    """v0.3 주간 계측 체인(§5 토요일) — R2 밸류에이션 → R3 온도계 → R4 페이퍼 → 다이제스트."""
    from trading.cycle.__main__ import main as cycle_main
    from trading.screen.__main__ import main as screen_main
    from trading.valuation.build import main as valuation_main
    from trading.weekly_digest import main as digest_main

    for name, step in (
        # v2.6: 판정·유니버스 추종은 주간 — 지배주주지분/환원 증분(멱등) →
        # 계측 → 자동 심사 → 페이퍼 자동 등록 → 다이제스트
        ("owner-equity", _owner_equity_v3),
        ("valuation", valuation_main),
        ("cycle", cycle_main),
        ("screen", screen_main),
        ("returns", _returns_v3),
        ("review-auto", _review_auto_v3),
        ("paper-register", _paper_register_v3),
        ("digest", digest_main),
    ):
        rc = step()
        if rc != 0:
            _alert_round_failure(f"weekly-v3/{name}", f"rc={rc}")
            return rc
    return 0


# --- ALERT-1(운영자 결정 2026-09-02): 실행 보고 + 미발화 감시 ---------------------------
# v0.3 체인은 끝나면 성공/실패 1통(텔레그램)을 보내고 적체 P1을 같은 메시지 꼬리에 싣는다.
# openclaw 트리거 턴 자체의 실패(Python 미기동)는 check-* 슬롯이 RunStore 부재로 잡는다.
REPORTED_ROUNDS: frozenset[str] = frozenset({"eod-v3", "weekly-v3"})
_SUMMARY_MAX_LINES = 14


def _summarize(lines: list[str], *, limit: int = _SUMMARY_MAX_LINES) -> list[str]:
    """체인 stdout에서 최상위(비들여쓰기) 줄만 — 종목·산업별 상세는 들여쓰기라 자연 제외."""
    top = [ln.rstrip() for ln in lines if ln.strip() and not ln.startswith((" ", "\t"))]
    if len(top) > limit:
        return [*top[:limit], f"…(+{len(top) - limit}줄)"]
    return top


def _run_reported(name: str, handler: Callable[[], int]) -> int:
    """실행 사실(RunStore) 박제 + stdout 요약 + 실행 보고 1통. 보고 실패는 rc에 영향 없음."""
    import contextlib
    import io
    from typing import TextIO

    from trading.collectors.base import now_kst
    from trading.runs import RunStore

    class _Tee(io.StringIO):
        """stdout 통과 + 캡처 — 잡 로그(.runtime/logs/cron)는 그대로, 요약만 추가로 얻는다."""

        def __init__(self, real: TextIO) -> None:
            super().__init__()
            self._real = real

        def write(self, s: str) -> int:
            self._real.write(s)
            return super().write(s)

    started = now_kst()
    store = RunStore()
    try:
        run_id = store.start(name, at=started)
    finally:
        store.close()
    tee = _Tee(sys.stdout)
    failure: str | None = None
    with contextlib.redirect_stdout(tee):
        try:
            rc = handler()
        except Exception as exc:  # noqa: BLE001 — 라운드 전체의 마지막 방어선(§9)
            print(f"round {name} crashed: {exc!r}", file=sys.stderr)
            _alert_round_failure(name, repr(exc))
            failure, rc = repr(exc), 1
    if rc not in (0, GUARD_SKIP_RC) and failure is None:
        _alert_round_failure(name, f"rc={rc}")
        failure = f"rc={rc}"
    finished = now_kst()
    summary = _summarize(tee.getvalue().splitlines())
    store = RunStore()
    try:
        store.finish(name, run_id, rc=rc, summary="\n".join(summary), at=finished)
    finally:
        store.close()
    _send_run_report(
        name, ok=failure is None, started_at=started, finished_at=finished,
        summary_lines=summary, failure=failure,
    )
    return rc


def _send_run_report(
    name: str, *, ok: bool, started_at: datetime, finished_at: datetime,
    summary_lines: list[str], failure: str | None,
) -> None:
    """실행 보고 발송(ALERT-1) — 테스트는 conftest가 무효화."""
    try:
        from trading.alerts import AlertDispatcher

        d = AlertDispatcher()
        try:
            res = d.send_run_report(
                round_name=name, ok=ok, started_at=started_at, finished_at=finished_at,
                summary_lines=summary_lines, failure=failure,
            )
        finally:
            d.store.close()
        print(f"실행 보고: {res}")
    except Exception as exc:  # noqa: BLE001 — 보고 실패가 라운드 rc를 바꾸면 안 된다
        print(f"실행 보고 발송 실패(무시): {exc!r}", file=sys.stderr)


def _check_run(name: str, *, dispatcher: "AlertDispatcher | None" = None) -> int:
    """미발화 감시 — 오늘(KST) 해당 라운드의 완료 기록이 없으면 ❌/⏳ 1통 즉시 발송.

    완료 기록이 있으면(실패 rc여도) 침묵 — 실패 보고는 실행 보고가 이미 보냈다.
    """
    from trading.alerts import Alert, AlertDispatcher, Severity
    from trading.collectors.base import now_kst
    from trading.runs import RunStore

    now = now_kst()
    store = RunStore()
    try:
        st = store.latest_on(name, now.date())
    finally:
        store.close()
    if st is not None and st.finished:
        print(f"감시: {name} 오늘 완료 확인(rc={st.rc}) — 무발송")
        return 0
    if st is None:
        header = f"❌ {name} 미발화"
        what = f"{name} 미발화 — 오늘({now:%Y-%m-%d}) 실행 기록 없음(openclaw 트리거 턴 실패 가능)"
    else:
        elapsed = (now - st.started_at).total_seconds() / 60
        header = f"⏳ {name} 미완료"
        what = f"{name} 미완료 — {st.started_at:%H:%M} 시작 후 {elapsed:.0f}분, 종료 기록 없음"
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    try:
        d.notify(Alert(
            severity=Severity.P1,
            what=what,
            rule="ALERT-1 미발화 감시(운영자 결정 2026-09-02)",
            action=(f"openclaw cron list 상태·.runtime/logs/cron/{name}.log 확인 → "
                    f"수동 실행: python -m trading.run {name}"),
            deadline="다음 동일 슬롯 전",
            created_at=now,
        ))
        n = d.flush_digest(header=header)
    finally:
        if dispatcher is None:
            d.store.close()
    print(f"감시: {header} — 발송 {n}건")
    return 0


def _check_eod_v3() -> int:
    return _check_run("eod-v3")


def _check_weekly_v3() -> int:
    return _check_run("weekly-v3")


ROUNDS: dict[str, Callable[[], int]] = {
    # --- v0.3 장기 사이클 라운드(전부 순수 코드) ---
    "eod-v3": _eod_v3,
    "weekly-v3": _weekly_v3,
    "flows-v3": _collect_flows_v3,
    "toss-facts-v3": _collect_toss_facts_v3,
    "check-eod-v3": _check_eod_v3,        # ALERT-1 미발화 감시(18:30)
    "check-weekly-v3": _check_weekly_v3,  # ALERT-1 미발화 감시(토 10:00)
    "collect-macro": _collect_macro,
    "collect-market": _collect_market,
    "collect-flows": _collect_flows,
    "collect-news": _collect_news,
    "classify-sectors": _classify_sectors,
    "sector-llm": _sector_llm,
    "collect-fins": _collect_fins,
    "swing": _swing,
    "arm-watch": _arm_watch,
    "screen": _screen,
    "factpack": _factpack,
    "score-news": _score_news,
    "verify-catalysts": _verify_catalysts,
    "reason-theses": _reason_theses,
    "synth-playbooks": _synth_playbooks,
    "select-playbooks": _select_playbooks,
    "alerts-digest": _alerts_digest,
    "report-morning": _report_morning,
    "report-evening": _report_evening,
    "evaluate": _evaluate,
    "daily-eod": _daily_eod,
}


GUARD_SKIP_RC = 3  # 시장 가드 정상 스킵(장중·휴장) — 실패 아님, 알림 없음

# 장중 휴면(§5) 대상 = LLM이 판단에 개입하는 라운드. 수집·스크리너·선택기(R5.5)·다이제스트는
# 순수 코드라 장중에도 돈다(선택기는 자체 in_krx_session 처리). R5(synth)는 핸들러 안에서
# require_market_closed를 부르므로 여기선 중복 호출하지 않는다(--force 우회 경로 보존, CAL-2).
_LLM_ROUNDS: frozenset[str] = frozenset(
    {"score-news", "verify-catalysts", "reason-theses", "report-morning", "report-evening", "evaluate"}
)


def _guard_llm_round(name: str) -> int | None:
    """cron 경로의 §5 휴면 강제 — 장중(정규장+애프터마켓, CAL-3)이면 스킵 코드.

    지금껏 §5는 **슬롯 배치로만** 지켜졌다(가드는 정의만 되고 미배선). 애프터마켓(9/14~)이
    저녁 슬롯과 겹치면서 배치만으로는 못 막으므로 디스패치에서 강제한다.
    수동 CLI(`python -m trading.score_news`)는 이 경로를 타지 않는다 — CAL-2대로 우회 허용.
    """
    if name not in _LLM_ROUNDS:
        return None
    from trading.market_calendar.calendar import MarketGuardError, require_llm_rounds_allowed

    try:
        require_llm_rounds_allowed()
    except MarketGuardError as exc:
        print(f"[guard] {name} 스킵 — {exc}")
        return GUARD_SKIP_RC
    return None


def _alert_round_failure(name: str, detail: str) -> None:
    """라운드 실패 P1 — cron이 fire-and-forget이라 실패 가시성은 여기(Python)가 전담."""
    try:
        from trading.alerts import Alert, AlertDispatcher, Severity

        d = AlertDispatcher()
        d.notify(
            Alert(
                severity=Severity.P1,
                what=f"라운드 실패: {name} — {detail[:140]}",
                rule="trading.run 디스패치 무결성(§9 장애 대응)",
                action="잡 로그(.runtime/logs/cron) 확인 후 수동 재실행 또는 보류",
                deadline="다음 동일 슬롯 전",
            )
        )
        d.store.close()
    except Exception as alert_exc:  # noqa: BLE001 — 알림 실패가 원 실패를 가리면 안 됨
        print(f"[alert-fail] {alert_exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("--list", "-l"):
        for name in ROUNDS:
            print(name)
        return 0
    if not args:
        print("usage: python -m trading.run <round> | --list", file=sys.stderr)
        return 2
    name = args[0]
    handler = ROUNDS.get(name)
    if handler is None:
        print(f"unknown or not-yet-implemented round: {name} (try --list)", file=sys.stderr)
        return 2
    skipped = _guard_llm_round(name)
    if skipped is not None:
        return skipped
    if name in REPORTED_ROUNDS:
        return _run_reported(name, handler)  # ALERT-1: 실행 기록 + 성공/실패 보고 1통
    try:
        rc = handler()
    except Exception as exc:  # noqa: BLE001 — 라운드 전체의 마지막 방어선(§9)
        print(f"round {name} crashed: {exc!r}", file=sys.stderr)
        _alert_round_failure(name, repr(exc))
        return 1
    if rc not in (0, GUARD_SKIP_RC):
        _alert_round_failure(name, f"rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
