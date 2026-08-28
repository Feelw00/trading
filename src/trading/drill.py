"""수동 드릴 — 자동 파이프라인(eod-v3·weekly-v3) 전 단계를 손으로 돌리고 검사한다.

페이퍼 관찰(Phase 3)의 반복 개선 루프용:
  poetry run python -m trading.drill status              # 전 스토어 현황
  poetry run python -m trading.drill run                 # 전 단계 순차 실행(전후 델타 출력)
  poetry run python -m trading.drill run valuation cycle screen   # 단계 선택 실행
  poetry run python -m trading.drill audit               # 신선도·정합 감사(PASS/WARN)

cron 자동 실행과 **완전히 같은 핸들러**를 호출한다(경로 이원화 없음) — 드릴에서 통과한
단계는 cron에서도 같은 코드가 돈다. 실행 전후 스토어 델타를 찍어 "무엇이 얼마나
변했는가"를 즉시 확인한다.
"""

import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


def _q1(db: str, sql: str) -> tuple[object, ...] | None:
    """data/<db>에 읽기 전용 단일 행 질의. 파일·테이블 없으면 None(드릴은 죽지 않는다)."""
    path = Path("data") / db
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(sql).fetchone()
            return tuple(row) if row is not None else None
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return None


# --- 스토어 메트릭(전후 델타용 한 줄 요약) ---


def m_market() -> str:
    r = _q1("market.sqlite", "SELECT COUNT(DISTINCT bas_dt), COUNT(*), MAX(bas_dt) FROM daily_quotes")
    return f"{r[0]}일·{r[1]:,}행·최신 {r[2]}" if r and r[0] else "비어 있음"


def m_sectors() -> str:
    r = _q1(
        "market.sqlite",
        "SELECT COUNT(DISTINCT srtn_cd) FROM stock_sectors "
        "WHERE source='kis-bstp-v1' AND sector != 'unclassified'",
    )
    return f"KRX 태깅 {r[0]}종목" if r else "비어 있음"


def m_fins() -> str:
    r = _q1(
        "fins.sqlite",
        "SELECT COUNT(DISTINCT srtn_cd), "
        "COUNT(DISTINCT srtn_cd||bsns_year) FILTER (WHERE reprt_code='11011') FROM fin_facts",
    )
    return f"{r[0]}종목·연간 {r[1]}건" if r and r[0] else "비어 있음"


def m_flows() -> str:
    r = _q1(
        "flows.sqlite",
        "SELECT COUNT(DISTINCT code), COUNT(DISTINCT bas_dt), MAX(bas_dt) "
        "FROM investor_flows WHERE scope='stock'",
    )
    return f"{r[0]}종목·창 {r[1]}일·최신 {r[2]}" if r and r[0] else "비어 있음"


def m_valuation() -> str:
    r = _q1("valuation.sqlite", "SELECT COUNT(DISTINCT symbol), COUNT(*), substr(MAX(as_of),1,10) FROM valuations")
    return f"{r[0]}종목·{r[1]}레코드·as_of {r[2]}" if r and r[0] else "비어 있음"


def m_cycle() -> str:
    r = _q1("cycle.sqlite", "SELECT COUNT(DISTINCT industry), COUNT(*), substr(MAX(as_of),1,10) FROM cycles")
    return f"{r[0]}산업·{r[1]}레코드·as_of {r[2]}" if r and r[0] else "비어 있음"


def m_screen() -> str:
    # append-only 누적이 아니라 **최신 실행분** 기준 — 회차 간 혼동 방지(2026-08-28 드릴 관찰)
    r = _q1(
        "candidates.sqlite",
        "SELECT COUNT(*), SUM(passed), substr(MAX(as_of),1,10) FROM candidates "
        "WHERE as_of = (SELECT MAX(as_of) FROM candidates)",
    )
    return f"최신 회차 평가 {r[0]}건·통과 {r[1]}건·as_of {r[2]}" if r and r[0] else "비어 있음"


def m_digest() -> str:
    files = sorted(Path(".runtime/reports").glob("weekly-*.md"))
    return f"보고서 {len(files)}건·최신 {files[-1].name}" if files else "없음"


# --- 단계 레지스트리: cron과 동일 핸들러 재사용(경로 이원화 금지) ---


def _r(name: str) -> Callable[[], int]:
    from trading.run import ROUNDS

    handler = ROUNDS[name]
    return handler


@dataclass(frozen=True)
class Stage:
    name: str
    chain: str                      # eod-v3 | weekly-v3
    runner: Callable[[], Callable[[], int]]
    metric: Callable[[], str]


STAGES: tuple[Stage, ...] = (
    Stage("market", "eod-v3", lambda: _r("collect-market"), m_market),
    Stage("sectors", "eod-v3", lambda: _r("classify-sectors"), m_sectors),
    Stage("fins", "eod-v3", lambda: _r("collect-fins"), m_fins),
    Stage("flows", "eod-v3", lambda: _r("flows-v3"), m_flows),
    Stage("valuation", "weekly-v3", lambda: _valuation_main, m_valuation),
    Stage("cycle", "weekly-v3", lambda: _cycle_main, m_cycle),
    Stage("screen", "weekly-v3", lambda: _screen_main, m_screen),
    Stage("digest", "weekly-v3", lambda: _digest_main, m_digest),
)


def _valuation_main() -> int:
    from trading.valuation.build import main

    return main()


def _cycle_main() -> int:
    from trading.cycle.__main__ import main

    return main()


def _screen_main() -> int:
    from trading.screen.__main__ import main

    return main()


def _digest_main() -> int:
    from trading.weekly_digest import main

    return main()


# --- 명령 ---


def cmd_status() -> int:
    print("=== 스토어 현황 ===")
    for s in STAGES:
        print(f"{s.name:<10} [{s.chain:<9}] {s.metric()}")
    return 0


def cmd_run(names: list[str]) -> int:
    known = {s.name: s for s in STAGES}
    unknown = [n for n in names if n not in known]
    if unknown:
        print(f"알 수 없는 단계: {', '.join(unknown)} (가능: {', '.join(known)})")
        return 2
    targets = [known[n] for n in names] if names else list(STAGES)
    failed = 0
    for s in targets:
        before = s.metric()
        t0 = time.monotonic()
        print(f"\n▶ {s.name} ({s.chain}) — 이전: {before}")
        rc = s.runner()()
        dur = time.monotonic() - t0
        after = s.metric()
        mark = "OK" if rc == 0 else f"FAIL rc={rc}"
        print(f"◀ {s.name} {mark} · {dur:.1f}s — 이후: {after}")
        if rc != 0:
            failed += 1
    print(f"\n드릴 완료: {len(targets)}단계 · 실패 {failed}")
    return 1 if failed else 0


def cmd_audit() -> int:
    """신선도·정합 감사 — PASS/WARN 한 줄씩(판단은 사람 몫, 여기선 사실만)."""
    from trading.collectors.base import now_kst
    from trading.market_calendar.calendar import MarketCalendar

    issues = 0

    def check(ok: bool, label: str, detail: str) -> None:
        nonlocal issues
        print(f"{'PASS' if ok else 'WARN'}  {label}: {detail}")
        if not ok:
            issues += 1

    cal = MarketCalendar.default()

    def _prev_trading(d: date) -> date:
        d -= timedelta(days=1)
        while not cal.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    # 국내 EOD는 T+1 공개(통상 당일 저녁) — 직전 거래일분은 아직 없을 수 있으므로
    # 기대 하한 = 직전 거래일의 이전 거래일. 그보다 뒤처지면 진짜 갭이다.
    today = now_kst().date()
    prev1 = _prev_trading(today)
    prev2 = _prev_trading(prev1)

    r = _q1("market.sqlite", "SELECT MAX(bas_dt) FROM daily_quotes")
    latest = str(r[0]) if r and r[0] else None
    check(
        latest is not None and latest >= prev2.strftime("%Y%m%d"),
        "시세 신선도",
        f"최신 {latest}, 기대 하한 {prev2} ({prev1}분은 T+1 공개라 오늘 저녁 eod-v3가 수집)",
    )

    v = _q1("valuation.sqlite", "SELECT substr(MAX(as_of),1,10) FROM valuations")
    check(
        v is not None and v[0] is not None,
        "밸류에이션",
        f"최신 as_of {v[0] if v else '없음'} (시세 최신일과 같아야 정상 — weekly 실행 시 갱신)",
    )

    fl = _q1(
        "flows.sqlite",
        "SELECT COUNT(DISTINCT bas_dt) FROM investor_flows WHERE scope='stock'",
    )
    window = int(str(fl[0])) if fl and fl[0] else 0
    check(
        window > 0,
        "수급 창",
        f"{window}거래일 / 목표 60~120 (일간 자동 축적 — 네거티브 스크린은 60일부터 유의미)",
    )

    cyc = _q1("cycle.sqlite", "SELECT COUNT(DISTINCT industry) FROM cycles")
    check(bool(cyc and cyc[0]), "온도계", f"{cyc[0] if cyc else 0}개 산업 박제")

    unknown_row = _q1(
        "cycle.sqlite",
        "SELECT COUNT(*) FROM cycles c WHERE phase='unknown' AND rowid = "
        "(SELECT rowid FROM cycles WHERE industry=c.industry ORDER BY as_of DESC, version DESC LIMIT 1)",
    )
    if unknown_row:
        print(f"INFO  판정 불가(unknown) 산업 {unknown_row[0]}개 (표본 미달 — 결측 정직, 결함 아님)")

    logs = sorted(Path(".runtime/logs/cron").glob("*.log"))
    if logs:
        newest = max(logs, key=lambda p: p.stat().st_mtime)
        age_h = (time.time() - newest.stat().st_mtime) / 3600
        check(age_h < 30, "cron 로그", f"{newest.name} 마지막 갱신 {age_h:.1f}시간 전 (일간 잡 기준 30h 내 정상)")
    else:
        check(False, "cron 로그", "로그 없음 — cron 미발화 또는 게이트웨이 확인")

    print(f"\n감사 결과: {'전부 PASS' if issues == 0 else f'WARN {issues}건'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    if cmd == "status":
        return cmd_status()
    if cmd == "run":
        return cmd_run(args[1:])
    if cmd == "audit":
        return cmd_audit()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
