"""R5.5 실행 러너 — ``python -m trading.select_playbooks``. (synth_playbooks 패턴, 순수 코드)

당일 PlaybookSet(PlaybookStore) + 흐름 관측 스냅샷 → 활성화 결정 → **승인된(approved)**
주문 초안만 ``armed`` 새 version append + P1 알림(§8: 플레이북 arm). cron 08:50(select-am).

가드(잡 내부, SCHED-1):
- **장중 실행 거부** — 08~10시 경로에 새로운 판단은 없고, 장중 arm은 즉흥 매매 경로다.
- 휴장일 스킵(``require_trading_day``).

흐름 관측치 소스: NXT 프리마켓 어댑터 미구현(🔴/SEL-1) — ``.runtime/flow/<YYYYMMDD>.json``
(``{"<srtn>": {"gap_pct": -3.5, …}}``) 주입 파일만 읽는다. **파일 없음 = 빈 스냅샷 =
전부 비활성(비거래)** — 관측치를 추측하지 않는다.
"""

import json
from datetime import datetime
from pathlib import Path

from trading.alerts import Alert, AlertDispatcher, Severity
from trading.collectors.base import KST, now_kst
from trading.contracts.order import OrderStatus
from trading.journal.playbooks import PlaybookStore
from trading.market_calendar.calendar import (
    MarketGuardError,
    in_krx_session,
    require_trading_day,
)
from trading.selector import FlowSnapshot, select

DEFAULT_FLOW_DIR = Path(".runtime") / "flow"


def load_snapshot(day: str, *, flow_dir: Path = DEFAULT_FLOW_DIR) -> FlowSnapshot:
    """주입 스냅샷 로드 — 없으면 빈 dict(전부 비활성). 숫자 외 값은 버린다(평가 불가)."""
    path = flow_dir / f"{day}.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for srtn, obs in raw.items():
        if isinstance(obs, dict):
            out[str(srtn)] = {
                str(k): float(v) for k, v in obs.items() if isinstance(v, (int, float))
            }
    return out


def run(
    *,
    now: datetime | None = None,
    playbook_store: PlaybookStore | None = None,
    dispatcher: AlertDispatcher | None = None,
    flow_dir: Path = DEFAULT_FLOW_DIR,
) -> int:
    """당일 플레이북 선택·arm. 장중 rc=3, 휴장일 rc=3, 그 외 0(비거래 포함 — 정상)."""
    resolved_now = now if now is not None else now_kst()
    try:
        require_trading_day(resolved_now)
    except MarketGuardError as e:
        print(f"R5.5 스킵 — {e}")
        return 3
    if in_krx_session(resolved_now):
        print("R5.5 거부 — 장중 arm 금지(아침 08:50 경로 전용, 설계서 §3 R5.5)")
        return 3

    day = resolved_now.astimezone(KST).strftime("%Y%m%d")
    ps = playbook_store if playbook_store is not None else PlaybookStore()
    playbooks = ps.playbooks_for_day(day)
    if not playbooks:
        print(f"R5.5: 당일({day}) 플레이북 없음 — 비거래")
        if playbook_store is None:
            ps.close()
        return 0

    snapshot = load_snapshot(day, flow_dir=flow_dir)
    if not snapshot:
        print("R5.5: 흐름 관측치 없음(SEL-1, NXT 어댑터 미구현) — 전 플레이북 비활성")
    result = select(playbooks, snapshot)

    d = dispatcher if dispatcher is not None else AlertDispatcher()
    armed = 0
    for act in result.activations:
        marks = ", ".join(
            f"{e.var}{e.expr}→{'O' if e.met else 'X'}"
            + (f"({e.note})" if e.note else f"(obs={e.observed})")
            for e in act.evals
        )
        print(f"  {act.playbook.id}: {act.state.value} [{marks}]")
        if not act.active:
            continue
        draft = ps.draft(act.playbook.order_draft_ref)
        if draft is None:
            print(f"    ⚠ 참조 초안 없음: {act.playbook.order_draft_ref}")
            continue
        if draft.status is not OrderStatus.APPROVED:
            # §6 워크플로: 21:00 저녁 결재 승인 없인 arm 불가(의도된 마찰)
            print(f"    arm 보류 — 초안 status={draft.status.value} (approved 아님)")
            continue
        ps.append_draft(draft.model_copy(update={"status": OrderStatus.ARMED}))
        armed += 1
        d.notify(
            Alert(
                severity=Severity.P1,
                what=f"플레이북 arm: {act.playbook.id} ({draft.symbol} {draft.side.value})",
                rule=f"R5.5 조건 일치: {dict(act.playbook.arm_conditions)}",
                action="저녁 보고에서 집행 편차 검토",
                deadline="오늘 21:00 저녁 결재 보고",
            )
        )

    if dispatcher is None:
        d.store.close()
    if playbook_store is None:
        ps.close()

    if result.no_trade:
        print(f"R5.5: 활성 0/{len(playbooks)} — 오늘 해당 없음, 비거래(정상)")
    else:
        print(f"R5.5: 활성 {len(result.active)}/{len(playbooks)} / arm {armed}")
    return 0


def main() -> int:
    return run()


__all__ = ["load_snapshot", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
