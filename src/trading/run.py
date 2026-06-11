"""디스패치 엔트리포인트 — openclaw cron이 ``python -m trading.run <round>`` 로 호출.

``ROUNDS``: 라운드명 → 핸들러(종료코드). 핸들러는 **결정론** 수집/스크리너/fact pack을 호출
(LLM 미개입). 수집기는 lazy import(엔트리 가벼움). 스케줄은 ``ops/openclaw/cron_jobs.py``(선언적).
시장 휴장일은 스케줄러가 아니라 각 잡 내부 가드가 처리(SCHED 결정) — data.go.kr은 휴장일 빈 결과.
"""

import sys
from collections.abc import Callable


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

    return synth_playbooks.run()


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
    """EOD 디스커버리 파이프라인: 전종목 → 섹터분류 → 스크리너 → 수급 → fact pack.

    수급(KIS)은 best-effort — 실패해도 P1만 띄우고 fact pack은 계속(수급 결측은
    factpack notes가 명시). 나머지 단계는 첫 실패에서 중단.
    """
    for step in (_collect_market, _classify_sectors, _screen):
        rc = step()
        if rc != 0:
            return rc
    flows_rc = _collect_flows()
    if flows_rc != 0:
        _alert_round_failure("daily-eod/collect-flows", f"rc={flows_rc}")
    return _factpack()


ROUNDS: dict[str, Callable[[], int]] = {
    "collect-macro": _collect_macro,
    "collect-market": _collect_market,
    "collect-flows": _collect_flows,
    "collect-news": _collect_news,
    "classify-sectors": _classify_sectors,
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
