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


def _alerts_digest() -> int:
    from trading.alerts import dispatch

    return dispatch.main()


def _daily_eod() -> int:
    """EOD 디스커버리 파이프라인: 전종목 수집 → 섹터분류 → 스크리너 → fact pack. 첫 실패에서 중단."""
    for step in (_collect_market, _classify_sectors, _screen, _factpack):
        rc = step()
        if rc != 0:
            return rc
    return 0


ROUNDS: dict[str, Callable[[], int]] = {
    "collect-macro": _collect_macro,
    "collect-market": _collect_market,
    "collect-news": _collect_news,
    "classify-sectors": _classify_sectors,
    "screen": _screen,
    "factpack": _factpack,
    "score-news": _score_news,
    "verify-catalysts": _verify_catalysts,
    "reason-theses": _reason_theses,
    "alerts-digest": _alerts_digest,
    "daily-eod": _daily_eod,
}


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
    return handler()


if __name__ == "__main__":
    raise SystemExit(main())
