"""`python -m trading.manual_input` — 운영자 수동 입력 CLI (PIVOT-8, §4 규약).

예:
  poetry run python -m trading.manual_input add \\
      --metric shipping.bdi --value 1420 --unit index \\
      --source "manual:발틱해운거래소(공표치)" --as-of 2026-08-25
  poetry run python -m trading.manual_input list
  poetry run python -m trading.manual_input history --metric shipping.bdi

--as-of 는 YYYY-MM-DD(KST 15:30 해석) 또는 timezone 포함 ISO8601.
급변 가드에 걸리면 값이 맞는지 확인 후 --confirm 으로 재시도.
"""

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from trading.collectors.manual import (
    ManualInputError,
    ManualStore,
    SurgeConfirmRequired,
    add_entry,
)

KST = ZoneInfo("Asia/Seoul")


def _parse_as_of(raw: str) -> datetime:
    try:
        # date-only → 그날 15:30 KST(장 마감 관측으로 해석 — 결정론 컨벤션)
        d = datetime.strptime(raw, "%Y-%m-%d")
        return d.replace(hour=15, minute=30, tzinfo=KST)
    except ValueError:
        return datetime.fromisoformat(raw)  # tz 미포함이면 add_entry 가드가 거부


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading.manual_input", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="지표값 입력(사실만 — 점수·판단 불가)")
    p_add.add_argument("--metric", required=True, help="지표 키 예: shipping.bdi")
    p_add.add_argument("--value", required=True, type=float)
    p_add.add_argument("--source", required=True, help='"manual:<출처명>" 형식')
    p_add.add_argument("--as-of", required=True, dest="as_of")
    p_add.add_argument("--unit", default=None)
    p_add.add_argument("--note", default=None)
    p_add.add_argument("--confirm", action="store_true", help="급변 가드 확인 후 재시도용")

    sub.add_parser("list", help="입력된 지표 키와 최신값")

    p_hist = sub.add_parser("history", help="지표의 전체 버전 이력")
    p_hist.add_argument("--metric", required=True)

    args = parser.parse_args(argv)
    store = ManualStore()
    try:
        if args.cmd == "add":
            try:
                entry = add_entry(
                    store,
                    metric=args.metric,
                    value=args.value,
                    source=args.source,
                    as_of=_parse_as_of(args.as_of),
                    unit=args.unit,
                    note=args.note,
                    confirm=args.confirm,
                )
            except SurgeConfirmRequired as e:
                print(f"⚠️ 급변 가드: {e}", file=sys.stderr)
                return 3
            except ManualInputError as e:
                print(f"거부: {e}", file=sys.stderr)
                return 2
            print(
                f"기록됨: {entry.metric} v{entry.version} = {entry.value}"
                f"{' ' + entry.unit if entry.unit else ''} (as_of {entry.as_of}, {entry.source})"
            )
        elif args.cmd == "list":
            keys = store.metrics()
            if not keys:
                print("입력된 지표 없음")
            for k in keys:
                latest = store.latest(k)
                assert latest is not None
                print(f"{k:32s} v{latest.version} = {latest.value} (as_of {latest.as_of})")
        elif args.cmd == "history":
            entries = store.history(args.metric)
            if not entries:
                print(f"{args.metric}: 이력 없음")
            for h in entries:
                print(f"v{h.version}: {h.value} (as_of {h.as_of}, {h.source}, 입력 {h.entered_at})")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
