"""운영자 지시 청산 큐 CLI — 감시기가 세션 창(09:00~20:00)에서 자동 매도한다.

사용:
  python -m trading.liquidate <draft_id> [<draft_id> ...]   # 큐 등록
  python -m trading.liquidate --list                        # 현재 큐 확인

EXEC-1 잔여 "임의 청산(스탑·익절·시간손절 외) 자동화 없음" 해소(운영자 2026-07-14 밤 —
테스트 매수분 단순 매도 지시). 처리 규율은 ``executor.process_liquidation_queue`` 참조.
"""

import sys
from collections.abc import Sequence

from trading.executor import LIQUIDATE_QUEUE, queue_liquidation


def run(argv: Sequence[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if argv[0] == "--list":
        if LIQUIDATE_QUEUE.exists():
            body = LIQUIDATE_QUEUE.read_text().strip()
            print(body if body else "(큐 비어 있음)")
        else:
            print("(큐 비어 있음)")
        return 0
    added = queue_liquidation(list(argv))
    for d in added:
        print(f"청산 큐 등록: {d}")
    skipped = [a for a in argv if a not in added]
    for a in skipped:
        print(f"이미 큐에 있음: {a}")
    print("감시기가 다음 세션 창(09:00~20:00) 패스에서 매도합니다 — P0 보고 발송됨.")
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
