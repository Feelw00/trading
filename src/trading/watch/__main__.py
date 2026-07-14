"""연속 감시 엔트리포인트 — ``python -m trading.watch``.

openclaw cron(09:00 기동 + 12:00 재기동, fire-and-forget)이 호출. 순수 코드, LLM 미개입.
기본 = 세션 내 폴링 루프(15:00 종료, ``arm_watch.run_loop``). ``--once`` = 1패스만
(드릴 검증·heartbeat 이관 대비). 서킷브레이커/환율 임계/바이너리 전이 감시(P0)는 후속 —
같은 골격에 패스 추가.
"""

import sys

from trading.watch.arm_watch import run_loop, run_pass


def main() -> int:
    if "--once" in sys.argv[1:]:
        r = run_pass()
        if r.fired:
            print(f"발화: {', '.join(r.fired)}")
        for n in r.notes:
            print(f"  note: {n}")
        return 0 if r.rc == 3 else r.rc  # 1패스 모드에선 세션 밖도 정상 종료(드릴용)
    return run_loop()


if __name__ == "__main__":
    raise SystemExit(main())
