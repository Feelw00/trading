"""디스패치 엔트리포인트 — openclaw cron이 ``python -m trading.run <round>`` 로 호출.

순수-코드 경로(R0/R1/R5.5)와 LLM 라운드(R2~R5,R7)의 라우팅 지점.
라운드 핸들러는 M2~M3에서 ``ROUNDS`` 에 등록한다. LLM은 트리거 전용(데이터 미개입).
"""

import sys
from collections.abc import Callable

# 라운드 이름 → 종료코드 반환 콜러블. M2~M3에서 채운다.
ROUNDS: dict[str, Callable[[], int]] = {}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: python -m trading.run <round>", file=sys.stderr)
        return 2
    name = args[0]
    handler = ROUNDS.get(name)
    if handler is None:
        print(f"unknown or not-yet-implemented round: {name}", file=sys.stderr)
        return 2
    return handler()


if __name__ == "__main__":
    raise SystemExit(main())
