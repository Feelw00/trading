"""R7 실행 러너 — ``python -m trading.evaluate``. (synth_playbooks 패턴)

채점·레짐(순수 코드) → ScoreStore 적재 → claude -p 해석·개정안(**자동 적용 금지** —
`.runtime/reports/<일자>-r7-proposal.md` 박제, 운영자 승인 대기) → 요약 Telegram 발송.
cron: eval-sat(토 10:00). LLM 실패해도 채점 결과는 적재된다(해석만 누락 표기).
"""

from datetime import datetime
from pathlib import Path

from trading.alerts import ChannelError, channel_from_env
from trading.collectors.market import MarketStore
from trading.journal.events import EventStore
from trading.journal.scores import ScoreStore
from trading.journal.theses import ThesisStore
from trading.llm import LLMClient, LLMError, client_from_env
from trading.collectors.base import now_kst
from trading.rounds.r7 import R7Config, build_interpretation_prompt, evaluate

DEFAULT_OUT_DIR = Path(".runtime") / "reports"


def _summary_text(day: str, record_notes: list[str], persona_lines: list[str],
                  r4_line: str, regime_line: str, proposal_path: str | None) -> str:
    parts = [f"[R7 주간 평가] {day}", *persona_lines, r4_line, regime_line]
    if proposal_path:
        parts.append(f"해석·개정안: {proposal_path} (자동 적용 안 됨 — 승인 대기)")
    parts.append("한계: " + " / ".join(record_notes[:2]))
    return "\n".join(parts)


def run(
    *,
    now: datetime | None = None,
    config: R7Config | None = None,
    client: LLMClient | None = None,
    market_store: MarketStore | None = None,
    thesis_store: ThesisStore | None = None,
    event_store: EventStore | None = None,
    score_store: ScoreStore | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
    send: bool = True,
) -> int:
    resolved_now = now if now is not None else now_kst()

    ts = thesis_store if thesis_store is not None else ThesisStore()
    theses = ts.recent(limit=500)
    if thesis_store is None:
        ts.close()
    es = event_store if event_store is not None else EventStore()
    events = es.recent(limit=500)
    if event_store is None:
        es.close()

    ms = market_store if market_store is not None else MarketStore()
    record, outcomes = evaluate(
        theses, events, ms, now=resolved_now, config=config or R7Config()
    )

    ss = score_store if score_store is not None else ScoreStore()
    version = ss.append(record)
    if score_store is None:
        ss.close()

    persona_lines = []
    for p in record.personas:
        hr = f"{p.hit_rate:.0%}" if p.hit_rate is not None else "N/A"
        persona_lines.append(
            f"- {p.persona.value}: 적중률 {hr} (채점 {p.n_scored}, 미성숙 {p.n_immature})"
        )
        print(persona_lines[-1])
    r4_line = (
        f"- R4: 기각 {record.r4_refuted_correct}/{record.r4_refuted_checked} 정확, "
        f"생존 {record.r4_confirmed_correct}/{record.r4_confirmed_checked} 정확"
    )
    regime_line = f"- 레짐 변동성 비율: {record.regime_volatility_ratio or '미산출'}"
    print(r4_line)
    print(regime_line)

    # 해석·개정안 (LLM — 실패해도 채점은 이미 적재됨)
    proposal_rel: str | None = None
    scored_any = any(p.n_scored for p in record.personas) or record.r4_refuted_checked
    if scored_any:
        llm = client if client is not None else client_from_env()
        try:
            text = llm.complete(build_interpretation_prompt(record, outcomes))
            out_dir.mkdir(parents=True, exist_ok=True)
            day = record.as_of.date().isoformat()
            path = out_dir / f"{day}-r7-proposal.md"
            path.write_text(text, encoding="utf-8")
            proposal_rel = str(path)
            print(f"해석·개정안 박제: {path} (자동 적용 안 됨)")
        except LLMError as e:
            print(f"해석 생략 — LLM 실패: {e} (채점 결과는 적재됨)")
    else:
        print("해석 생략 — 채점 표본 없음(전부 미성숙/결측)")

    if ms is not market_store:
        ms.close()

    if send:
        day = record.as_of.date().isoformat()
        channel = channel_from_env()
        try:
            channel.send(_summary_text(day, record.notes, persona_lines, r4_line,
                                       regime_line, proposal_rel))
        except ChannelError as e:
            print(f"발송 실패(채점은 적재됨): {e}")
    print(f"R7 평가 적재: {record.id} v{version} ({record.period_start}~{record.period_end})")
    return 0


def main() -> int:
    return run()


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(main())
