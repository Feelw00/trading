"""R5.5 실행 러너 — ``python -m trading.select_playbooks``. (synth_playbooks 패턴, 순수 코드)

**활성 approved 풀**(PlaybookStore.active_playbooks — status·TTL, 날짜 라벨 비의존) +
흐름 관측 스냅샷 → 활성화 결정 → ``armed`` 새 version append + P1 알림(§8). cron 08:50(select-am).

날짜 어긋남 해소(SEL-3, 2026-06-12): 기존 ``playbooks_for_day(today)`` 는 R5 생성일(전일 밤)과
조회일(아침)이 어긋나 전일 승인분을 못 찾았다. arm-check와 같은 ``active_playbooks`` 로 통일 —
status=approved + TTL(time_stop_days 거래일) 미경과 풀을 조회한다.

가드(잡 내부, SCHED-1):
- **장중 실행 거부** — 08~10시 경로에 새로운 판단은 없고, 장중 arm은 즉흥 매매 경로다.
- 휴장일 스킵(``require_trading_day``).

흐름 관측치: arm-check와 동일 ``flowsnap.build_snapshot`` (KIS 실시간 + 주입 파일
``<flow_dir>/<YYYYMMDD>.json``). **둘 다 없음 = 빈 스냅샷 = 전부 비활성(비거래)** — 추측 금지.
"""

from datetime import datetime
from pathlib import Path

from trading.alerts import Alert, AlertDispatcher, Severity
from trading.collectors.base import now_kst
from trading.collectors.kis import client_from_env as kis_from_env
from trading.contracts.order import OrderStatus
from trading.flowsnap import build_snapshot
from trading.journal.playbooks import PlaybookStore
from trading.market_calendar.calendar import (
    MarketGuardError,
    in_krx_session,
    require_trading_day,
)
from trading.selector import select

DEFAULT_FLOW_DIR = Path(".runtime") / "flow"


def run(
    *,
    now: datetime | None = None,
    playbook_store: PlaybookStore | None = None,
    dispatcher: AlertDispatcher | None = None,
    flow_dir: Path = DEFAULT_FLOW_DIR,
    kis_client: object | None = None,
) -> int:
    """활성 풀 선택·arm. 장중 rc=3, 휴장일 rc=3, 그 외 0(비거래 포함 — 정상)."""
    resolved_now = now if now is not None else now_kst()
    try:
        require_trading_day(resolved_now)
    except MarketGuardError as e:
        print(f"R5.5 스킵 — {e}")
        return 3
    if in_krx_session(resolved_now):
        print("R5.5 거부 — 장중 arm 금지(아침 08:50 경로 전용, 설계서 §3 R5.5)")
        return 3

    ps = playbook_store if playbook_store is not None else PlaybookStore()
    active = ps.active_playbooks(resolved_now)   # approved + TTL(날짜 라벨 비의존)
    if not active:
        print("R5.5: 활성 approved 풀 없음 — 비거래(승인 대기/만료 점검)")
        if playbook_store is None:
            ps.close()
        return 0

    playbooks = [pb for pb, _, _ in active]
    drafts_by_pb = {pb.id: draft for pb, draft, _ in active}
    srtns = [draft.symbol for _, draft, _ in active]
    client = kis_client if kis_client is not None else kis_from_env()
    snapshot, _notes = build_snapshot(
        srtns, kis_client=client, now=resolved_now, inject_dir=flow_dir  # type: ignore[arg-type]
    )
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
        draft = drafts_by_pb[act.playbook.id]  # active_playbooks가 approved 보장
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


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(main())
