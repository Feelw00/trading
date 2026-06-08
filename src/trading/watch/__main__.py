"""연속 감시 엔트리포인트 — `python -m trading.watch`.

heartbeat(activeHours 09:00–15:30)가 호출. 순수 코드, LLM 미개입.
이벤트 감시기 본체(서킷브레이커/환율 임계/바이너리 전이 → P0 알림)는 이 패키지에 M4에서 구현.
"""


def main() -> int:
    # M4에서 구현. 현재는 no-op(정상 종료).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
