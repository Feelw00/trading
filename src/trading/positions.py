"""positions CLI — 보유 포지션 등록·점검·정리 (P-8, 수동 — KIS 잔고 대사는 후속).

  python -m trading.positions                              # open 포지션 점검(현재가·스탑·시간손절)
  python -m trading.positions add --symbol 095610 --qty 10 --price 196300 \\
      [--stop 160000] [--time-stop 10] [--confidence 0.35] \\
      [--hypothesis "..."] [--trigger "..."] [--invalidation "..."] \\
      [--plan-file 분석문서.md] [--source "discuss:테스 v1"]
  python -m trading.positions close pos.20260612.095610.buy --reason "시간손절 도래"

등록·정리는 운영자 명시 행위(자동 없음). 계획 스냅샷(가설·무효화·스탑·시간손절·문서 전문)을
포지션에 박제해 arm-check·저녁 보고가 매일 계획 대비 거리를 들이밀게 한다.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from trading.collectors.base import now_kst
from trading.contracts.order import Side
from trading.contracts.position import PositionRecord, PositionStatus
from trading.journal.positions import PositionStore
from trading.position_check import check_positions, render_lines


def _cmd_list(ps: PositionStore) -> int:
    views = check_positions(position_store=ps)
    if not views:
        print("보유 포지션 없음.")
        return 0
    review = sum(1 for v in views if v.review_needed)
    print(f"보유 {len(views)}건 / 정리 검토 {review}건")
    for line in render_lines(views):
        print(line.replace("**", ""))
    return 0


def _cmd_add(ps: PositionStore, args: argparse.Namespace) -> int:
    now = now_kst()
    pos_id = f"pos.{now:%Y%m%d}.{args.symbol}.{args.side}"
    if ps.get(pos_id) is not None:
        print(f"이미 존재: {pos_id} — 수량 변경은 close 후 재등록")
        return 1
    plan_doc = ""
    if args.plan_file:
        plan_doc = Path(args.plan_file).read_text(encoding="utf-8")
    pos = PositionRecord(
        id=pos_id, as_of=now, fetched_at=now, source="operator:manual",
        symbol=args.symbol, side=Side(args.side), qty=args.qty, avg_price=args.price,
        hypothesis=args.hypothesis or "", trigger_text=args.trigger or "",
        invalidation_text=args.invalidation or "",
        stop_level=args.stop, time_stop_days=args.time_stop, confidence=args.confidence,
        plan_doc=plan_doc, source_ref=args.source or "",
    )
    version = ps.append(pos)
    print(f"등록: {pos_id} v{version} — {args.qty}주 @{args.price:,.0f}")
    if pos.stop_level is None and pos.time_stop_days is None:
        print("  경고: 가격 스탑·시간손절 둘 다 없음 — 출구 없는 포지션(계획 보강 권장)")
    return 0


def _cmd_close(ps: PositionStore, position_id: str, reason: str) -> int:
    pos = ps.get(position_id)
    if pos is None:
        print(f"포지션 없음: {position_id}")
        return 1
    if pos.status is PositionStatus.CLOSED:
        print(f"이미 closed: {position_id}")
        return 1
    now = now_kst()
    closed = pos.model_copy(
        update={
            "status": PositionStatus.CLOSED, "close_reason": reason,
            "as_of": now, "fetched_at": now,
        }
    )
    version = ps.append(closed)
    print(f"정리: {position_id} v{version} — 사유: {reason}")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m trading.positions")
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("add", help="보유 등록(계획 스냅샷 포함)")
    a.add_argument("--symbol", required=True)
    a.add_argument("--qty", type=int, required=True)
    a.add_argument("--price", type=float, required=True)
    a.add_argument("--side", default="buy", choices=["buy", "sell"])
    a.add_argument("--stop", type=float, default=None)
    a.add_argument("--time-stop", dest="time_stop", type=int, default=None)
    a.add_argument("--confidence", type=float, default=None)
    a.add_argument("--hypothesis", default="")
    a.add_argument("--trigger", default="")
    a.add_argument("--invalidation", default="")
    a.add_argument("--plan-file", dest="plan_file", default="")
    a.add_argument("--source", default="")
    c = sub.add_parser("close", help="포지션 정리(사유 박제)")
    c.add_argument("position_id")
    c.add_argument("--reason", required=True)
    return p


def run(argv: Sequence[str]) -> int:
    args = _parser().parse_args(list(argv))
    ps = PositionStore()
    try:
        if args.cmd == "add":
            return _cmd_add(ps, args)
        if args.cmd == "close":
            return _cmd_close(ps, args.position_id, args.reason)
        return _cmd_list(ps)
    finally:
        ps.close()


def main() -> int:
    return run(sys.argv[1:])


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(main())
