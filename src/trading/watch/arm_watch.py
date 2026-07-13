"""장중 발동 감시기 — 승인(approved) 풀의 arm 조건을 실시간 평가해 충족 순간 P0 발화.

배경(운영자 2026-07-12): 발동 조건(전일고가 회복·체결강도·호가)은 장중 아무 때나 충족될 수
있는데 아침 arm-check는 1회 스냅샷이라, 조건 감시가 운영자 몫으로 남는 모순이 있었다
(P-9의 출발점 "계속 보고 있어야 하는 건 어렵다"와 충돌). 이 감시기가 그 구멍을 메운다.

**순수 코드(LLM 미개입, 절대금지 #2).** 판정은 arm-check와 동일 경로
(``flowsnap``(KIS 실시간) → ``selector.engine``)를 그대로 재사용 — 감시기는 새 판단을
만들지 않고 같은 판정을 주기 반복할 뿐이다. 주문도 없다(절대금지 #3) — 알림을 받은
운영자가 지정가/조건부 주문을 직접 입력한다(의도된 마찰).

알림 규약:
- 발동(P0, 즉시): 활성 approved 초안의 조건 전부 충족 — 행동="지정가 진입 검토+손절 동시
  입력", 기한="15:00 전"(운영자 거래 창). 초안·일자당 1회(WatchStore dedup, append-only).
- 마감 전 정리(P0, 14:40~): 활성 풀이 남아 있으면 "미체결 예약 취소·손절 주문 확인" 1회.
- 미승인 후보(candidates)는 감시하지 않는다 — 승인 전 발동 알림은 소음이다.

스케줄: openclaw cron이 09:00에 ``python -m trading.run arm-watch``(내부 루프, 15:00 종료)를
fire-and-forget으로 기동 + 12:00 재기동 슬롯(사망 대비). 중복 기동은 하트비트 파일로 차단.
자체 스케줄러 아님 — 기동은 전적으로 openclaw cron, 이 프로세스는 세션 내 폴링만 한다.
(README "예정"이던 heartbeat 배선의 실현체 — openclaw heartbeat CLI 검증 후 이관 가능.)
"""

import os
import sqlite3
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

from trading.alerts import Alert, AlertDispatcher, Severity
from trading.arm_check import AssessResult, assess
from trading.collectors.base import KST, now_kst
from trading.market_calendar.calendar import MarketCalendar

# 자동 집행(EXEC-1) — 발동 감지 시 집행기로 연결. 순수 코드·하드캡·dry-run 기본.
from trading import executor as _exec

DEFAULT_DB = Path("data") / "watch.sqlite"
HEARTBEAT_FILE = Path(".runtime") / "watch-heartbeat"

GUARD_SKIP_RC = 3  # trading.run 규약과 동일 — 세션 밖 정상 스킵


@dataclass(frozen=True)
class WatchConfig:
    session_start: time = time(9, 0)
    session_end: time = time(15, 0)    # **진입(발동) 창** 상한 — 신규 매수는 여기까지
    exit_end: time = time(20, 0)       # **청산 전용 감시** 상한(EXEC-3: 정규 잔여+NXT 애프터)
    closeout_from: time = time(14, 40)  # 마감 전 정리 리마인더 창 시작
    poll_seconds: int = 90              # KIS 폴링 간격(초안 수 × TR 콜 고려한 보수값)
    heartbeat_stale_s: int = 300        # 이 이내 하트비트가 있으면 중복 기동으로 보고 종료


_DDL = """
CREATE TABLE IF NOT EXISTS fired (
  day TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL, fired_at TEXT NOT NULL,
  UNIQUE(day, key, kind)
);
"""


class WatchStore:
    """발화 기록(append-only) — 같은 초안·같은 날 중복 알림 차단. 재기동에도 유지."""

    def __init__(self, db_path: Path | None = None) -> None:
        resolved = db_path if db_path is not None else DEFAULT_DB
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(resolved))
        self._conn.executescript(_DDL)

    def fired(self, day: str, key: str, kind: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM fired WHERE day=? AND key=? AND kind=?", (day, key, kind)
        ).fetchone()
        return row is not None

    def record(self, day: str, key: str, kind: str, at: str) -> bool:
        before = self._conn.total_changes
        self._conn.execute(
            "INSERT OR IGNORE INTO fired VALUES (?,?,?,?)", (day, key, kind, at)
        )
        self._conn.commit()
        return self._conn.total_changes > before

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class PassResult:
    rc: int
    fired: list[str] = field(default_factory=list)   # 이번 패스에 발화한 알림 키
    notes: list[str] = field(default_factory=list)


def _in_watch_window(now: datetime, cfg: WatchConfig, cal: MarketCalendar) -> bool:
    local = now.astimezone(KST)
    return cal.is_trading_day(local.date()) and cfg.session_start <= local.time() < cfg.session_end


def _in_exit_window(now: datetime, cfg: WatchConfig, cal: MarketCalendar) -> bool:
    """청산 전용 창(EXEC-3) — 진입 창 종료 후 20:00까지(정규 잔여 30분 + NXT 애프터)."""
    local = now.astimezone(KST)
    return cal.is_trading_day(local.date()) and cfg.session_start <= local.time() < cfg.exit_end


def run_pass(
    *,
    now: datetime | None = None,
    config: WatchConfig | None = None,
    store: WatchStore | None = None,
    dispatcher: AlertDispatcher | None = None,
    calendar: MarketCalendar | None = None,
    assess_fn: Callable[..., AssessResult] = assess,
    playbook_store: object | None = None,
    kis_client: object | None = None,
    executor_pass: Callable[[list[str], AlertDispatcher, datetime], None] | None = None,
) -> PassResult:
    """감시 1패스 — 세션 밖이면 rc=3. 발화는 초안·일자당 1회.

    ``executor_pass`` 가 주어지면(운영 루프) 발화분 자동 집행 + 체결 추적(EXEC-1)을
    같은 패스에서 잇는다. 기본 None — 단위 테스트·수동 1패스는 집행 미개입.
    """
    cfg = config or WatchConfig()
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    cal = calendar if calendar is not None else MarketCalendar.default()
    if not _in_watch_window(resolved, cfg, cal):
        # 청산 전용 창(EXEC-3): 진입·발동은 멈추고 체결 추적+계단 청산만 계속(운영 루프 한정)
        if executor_pass is not None and _in_exit_window(resolved, cfg, cal):
            d_exit = dispatcher if dispatcher is not None else AlertDispatcher()
            notes_exit: list[str] = ["청산 전용 감시(진입 창 밖 — 신규 매수 없음)"]
            try:
                executor_pass([], d_exit, resolved)
            except Exception as exc:  # noqa: BLE001
                notes_exit.append(f"청산 패스 오류(감시는 계속): {exc}")
            return PassResult(0, notes=notes_exit)
        return PassResult(GUARD_SKIP_RC, notes=["감시 창 밖(거래일 09:00~15:00 아님)"])

    own_store = store is None
    st = store if store is not None else WatchStore()
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    day = resolved.strftime("%Y%m%d")
    fired: list[str] = []
    notes: list[str] = []
    try:
        res = assess_fn(now=resolved, playbook_store=playbook_store, kis_client=kis_client)
        notes.extend(res.snapshot_notes)
        for it in res.items:  # 활성 approved 풀만
            if not it.active or st.fired(day, it.draft_id, "armed"):
                continue
            met = " / ".join(c.cond_ko for c in it.conditions if c.met) or "(조건 상세 없음)"
            d.notify(
                Alert(
                    severity=Severity.P0,
                    what=f"발동 — {it.headline}: 조건 전부 충족 ({met})",
                    rule="장중 감시기: approved 초안 arm 조건 전원 충족",
                    action=f"지정가 진입 검토 + 손절 동시 입력 ({it.stop} · {it.cap})",
                    deadline="장중 15:00 전",
                    created_at=resolved,
                )
            )
            st.record(day, it.draft_id, "armed", resolved.isoformat())
            fired.append(f"armed:{it.draft_id}")
        # 마감 전 정리 — 활성 풀이 있고(미발동 포함) 아직 안 알렸으면 1회
        if (
            resolved.time() >= cfg.closeout_from
            and res.items
            and not st.fired(day, "_session", "closeout")
        ):
            d.notify(
                Alert(
                    severity=Severity.P0,
                    what=f"마감 전 정리 — 활성 초안 {len(res.items)}건(발동 {res.active_count})",
                    rule="장중 감시기: 14:40 마감 전 점검 창",
                    action="미체결 예약 취소 · 체결분 손절 주문 확인",
                    deadline="15:00",
                    created_at=resolved,
                )
            )
            st.record(day, "_session", "closeout", resolved.isoformat())
            fired.append("closeout")
        # 자동 집행(EXEC-1) — 발동분 주문 + 미체결 추적. 실패해도 감시 루프는 계속.
        if executor_pass is not None:
            try:
                executor_pass([f for f in fired if f.startswith("armed:")], d, resolved)
            except Exception as exc:  # noqa: BLE001 — 집행 오류가 감시를 죽이면 안 된다
                notes.append(f"집행 패스 오류(감시는 계속): {exc}")
    finally:
        if own_store:
            st.close()
    return PassResult(0, fired=fired, notes=notes)


def _live_price(symbol: str, toss: object) -> float | None:
    """집행용 현재가 — 토스(주문 나갈 브로커와 동일 소스) 우선, 결측이면 KIS 체결가.

    조회 실패·비수치는 None(집행 스킵 — 값을 지어내지 않는다, 절대금지 #1)."""
    if toss is not None:
        try:
            rows = toss.prices([symbol])  # type: ignore[attr-defined]
            if rows:
                v = rows[0].get("lastPrice")
                if v is not None:
                    return float(str(v).replace(",", ""))
        except Exception:  # noqa: BLE001
            pass
    from trading.collectors.kis import client_from_env as kis_from_env

    kis = kis_from_env()
    if kis is None:
        return None
    try:
        v = kis.quote_ccnl(symbol).get("stck_prpr")
        return float(str(v).replace(",", "")) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def execution_pass(fired_keys: list[str], dispatcher: AlertDispatcher, now: datetime) -> None:
    """발동분 자동 집행 + 미체결 추적(EXEC-1). 운영 루프 전용 — 모드·캡은 executor가 강제."""
    mode = _exec.exec_mode()
    if mode == "off":
        return
    from trading.collectors.toss import client_from_env as toss_from_env
    from trading.journal.playbooks import PlaybookStore
    from trading.journal.positions import PositionStore

    ps = PlaybookStore()
    store = _exec.ExecStore()
    pos = PositionStore()
    try:
        active = ps.active_playbooks(now)
        drafts = {dr.id: dr for _, dr, _ in active}
        # 보유 포지션의 원 초안은 풀 이탈(TTL 등) 후에도 청산 관리에 필요 — 직접 로드
        for p_open in pos.open_positions():
            if p_open.source_ref and p_open.source_ref not in drafts:
                d_ref = ps.draft(p_open.source_ref)
                if d_ref is not None:
                    drafts[p_open.source_ref] = d_ref
        toss = toss_from_env()
        policy = _exec.ExecPolicy.from_env()
        day = now.astimezone(KST).strftime("%Y%m%d")
        # 레짐 스냅샷(EXEC-7) — 패스당 1회, 비정상 레짐은 하루 1회 P0
        from trading.regime import Regime, snapshot as regime_snapshot

        reg = regime_snapshot(toss, now=now)
        if reg.regime is not Regime.NORMAL and not store.has(f"_regime_{reg.regime.value}", ("regime",)):
            store.log(day=day, draft_id=f"_regime_{reg.regime.value}", symbol="-",
                      kind="regime", mode=mode, detail="; ".join(reg.lines), at=now.isoformat())
            dispatcher.notify(Alert(
                severity=Severity.P0,
                what=f"레짐 {reg.regime.value.upper()} — " + " · ".join(reg.lines),
                rule="레짐 감시(EXEC-7): 코스피 -3%↓ 배분 절반 / -5%↓ 신규 중단",
                action="개입 불필요 — 청산 관리는 계속, 신규 진입만 보수화",
                deadline="당일", created_at=now,
            ))
        # 발동분 + 오늘 잔고 부족으로 밀린 초안 재시도(EXEC-4 — 교체·청산으로 잔고가 생겼을 수 있음)
        attempt_ids = [k.split(":", 1)[1] for k in fired_keys]
        attempt_ids += [i for i in store.cash_skips_today(day) if i not in attempt_ids]
        active_ids = {dr.id for _, dr, _ in active}

        def _pool_weight() -> float:
            """**미집행 잔여** 활성 풀의 계수 합 — 동적 분모(EXEC-5 개정): 잔여가 줄면 몫 증가."""
            return sum(
                _exec.cap_fraction(drafts[i].total_size_cap)
                for i in active_ids
                if i in drafts and not store.has(i, ("order_intent", "order_sent"))
            )

        for did in attempt_ids:
            draft = drafts.get(did)
            if draft is None:
                continue
            price = _live_price(draft.symbol, toss)
            if price is None:
                print(f"  집행 스킵: {draft.symbol} 현재가 조회 불가")
                continue
            r = _exec.execute_armed(
                draft, price=price, store=store, policy=policy, mode=mode, toss=toss,
                dispatcher=dispatcher, now=now, pool_weight_total=_pool_weight(),
                regime=reg.regime,
            )
            print(f"  집행[{mode}]: {draft.symbol} {r.action} — {r.detail}")
            # 잔고 부족 → 회수 사다리(EXEC-4/6): ①갈아타기(전량 교체) ②부분 트림 → 재시도 1회
            if r.action == "skipped" and r.detail.startswith("잔고 부족"):
                funded = _exec.consider_rotation(
                    draft, price, store=store, mode=mode, toss=toss,
                    drafts_by_id=drafts, price_fn=lambda s: _live_price(s, toss),
                    position_store=pos, dispatcher=dispatcher, now=now,
                )
                if not funded:
                    w = _exec.cap_fraction(draft.total_size_cap)
                    w_all = max(sum(
                        _exec.cap_fraction(drafts[i].total_size_cap)
                        for i in active_ids if i in drafts
                    ), w)
                    fair_alloc = policy.account_krw * w / w_all
                    funded = _exec.trim_for_shortfall(
                        fair_alloc, store=store, mode=mode, toss=toss,
                        drafts_by_id=drafts, price_fn=lambda s: _live_price(s, toss),
                        position_store=pos, dispatcher=dispatcher, now=now,
                    ) > 0
                if funded:
                    r2 = _exec.execute_armed(
                        draft, price=price, store=store, policy=policy, mode=mode,
                        toss=toss, dispatcher=dispatcher, now=now,
                        pool_weight_total=_pool_weight(), regime=reg.regime,
                    )
                    print(f"  회수 후 재집행[{mode}]: {draft.symbol} {r2.action} — {r2.detail}")
        # 테스트 진입(D1 계측, env 게이트·dry-run 전용): 11:00까지 자연 발동 0이면
        # 가드 통과 초안 중 계획 R:R 최고 1건을 최소 수량으로 관통(체결→브래킷→사다리 계측)
        if (
            mode == "dry-run"
            and os.environ.get("EXEC_TEST_ENTRY") == "1"
            and now.astimezone(KST).time() >= time(11, 0)
            and store.new_orders_today(day) == 0
        ):
            best: tuple[float, str] | None = None
            for did in active_ids:
                draft = drafts.get(did)
                if draft is None or not draft.stop or not draft.stop.level:
                    continue
                p_ = _live_price(draft.symbol, toss)
                if p_ is None or p_ <= draft.stop.level:
                    continue  # 붕괴 가드 선반영
                if draft.targets and p_ >= draft.targets[0].level:
                    continue  # 소진 가드 선반영
                risk = (p_ - draft.stop.level) / p_
                up = _exec.planned_upside_pct(draft, p_)
                if risk <= 0 or up <= 0:
                    continue
                rr = up / risk
                if best is None or rr > best[0]:
                    best = (rr, did)
            if best is not None:
                draft = drafts[best[1]]
                p_ = _live_price(draft.symbol, toss)
                if p_ is not None:
                    rt = _exec.execute_armed(
                        draft, price=p_, store=store, policy=policy, mode=mode, toss=toss,
                        dispatcher=dispatcher, now=now, pool_weight_total=_pool_weight(),
                        regime=reg.regime, test_entry=True,
                    )
                    print(f"  테스트 진입[D1]: {draft.symbol} {rt.action} — {rt.detail}")
        done = _exec.reconcile(
            store=store, mode=mode, toss=toss, drafts_by_id=drafts,
            dispatcher=dispatcher, position_store=pos, now=now,
        )
        for did in done:
            print(f"  체결 처리[{mode}]: {did}")
        # 계단식 청산(EXEC-2) — 부분 익절·경고 축소·본전 상향
        legs = _exec.manage_exits(
            store=store, mode=mode, toss=toss, drafts_by_id=drafts,
            price_fn=lambda s: _live_price(s, toss),
            position_store=pos, dispatcher=dispatcher, now=now,
        )
        for did in legs:
            print(f"  청산 레그[{mode}]: {did}")
    finally:
        ps.close()
        store.close()
        pos.close()


def _heartbeat_fresh(now: datetime, cfg: WatchConfig, path: Path = HEARTBEAT_FILE) -> bool:
    try:
        age = now.timestamp() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return 0 <= age < cfg.heartbeat_stale_s


def run_loop(
    *,
    config: WatchConfig | None = None,
    heartbeat_path: Path = HEARTBEAT_FILE,
    sleep_fn: Callable[[float], None] = _time.sleep,
    max_passes: int | None = None,
) -> int:
    """세션 내 폴링 루프 — cron(09:00·12:00 재기동)이 fire-and-forget으로 실행.

    거래일 아님/세션 밖이면 rc=3 즉시 종료. 이미 가동 중(하트비트 신선)이면 rc=0 종료.
    """
    cfg = config or WatchConfig()
    now = now_kst()
    if _heartbeat_fresh(now, cfg, heartbeat_path):
        print("감시기 이미 가동 중(하트비트 신선) — 중복 기동 종료")
        return 0
    mode = _exec.exec_mode()
    print(f"자동 집행 모드: {mode} (EXEC-1 — 캡: {_exec.ExecPolicy.from_env()})")
    first = run_pass(now=now, config=cfg, executor_pass=execution_pass)
    if first.rc == GUARD_SKIP_RC:
        print(f"감시 창 밖 — 스킵(rc={GUARD_SKIP_RC})")
        return GUARD_SKIP_RC
    passes = 1
    _report_pass(first)
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.touch()
    while max_passes is None or passes < max_passes:
        sleep_fn(cfg.poll_seconds)
        heartbeat_path.touch()
        r = run_pass(config=cfg, executor_pass=execution_pass)
        passes += 1
        if r.rc == GUARD_SKIP_RC:  # 20:00(청산 창 상한) 도달 — 정상 종료
            print(f"세션 종료 — 감시 마감(패스 {passes})")
            return 0
        _report_pass(r)
    return 0


def _report_pass(r: PassResult) -> None:
    if r.fired:
        print(f"발화: {', '.join(r.fired)}")
    for n in r.notes:
        print(f"  note: {n}")


__all__ = [
    "GUARD_SKIP_RC",
    "PassResult",
    "WatchConfig",
    "WatchStore",
    "execution_pass",
    "run_loop",
    "run_pass",
]
