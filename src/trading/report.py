"""R6 실행 러너 — ``python -m trading.report <morning|evening>``.

렌더(정적, LLM 없음) → ``.runtime/reports/<일자>-<종류>.md`` 저장(원본) → Telegram 발송
(채널 미설정 시 로그 폴백 — channels 규약). 분량 가드 위반은 **생성 실패 + P1 알림**(§8/M4).
cron: report-am(06:50) / report-pm(21:00).
"""

import sys
from datetime import datetime
from pathlib import Path

from trading.alerts import Alert, AlertDispatcher, ChannelError, Severity, channel_from_env
from trading.reports.render import Rendered, ReportLengthError, render_evening, render_morning

DEFAULT_OUT_DIR = Path(".runtime") / "reports"


def _alert_failure(kind: str, detail: str, dispatcher: AlertDispatcher | None) -> None:
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    d.notify(
        Alert(
            severity=Severity.P1,
            what=f"R6 {kind} 보고 생성 실패 — {detail[:140]}",
            rule="보고 분량 가드(§8 5분 독해) / 렌더 무결성",
            action="상류 산출(플레이북 수·체크리스트) 점검 후 재생성",
            deadline="다음 보고 슬롯 전",
        )
    )
    if dispatcher is None:
        d.store.close()


def run(
    kind: str,
    *,
    now: datetime | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
    dispatcher: AlertDispatcher | None = None,
    send: bool = True,
) -> int:
    """보고 1종 생성·발송. 분량 초과 rc=1(+P1), 성공 0."""
    try:
        rendered: Rendered = (
            render_morning(now=now) if kind == "morning" else render_evening(now=now)
        )
    except ReportLengthError as e:
        print(f"R6 실패 — {e}")
        _alert_failure(kind, str(e), dispatcher)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{rendered.day}-{rendered.kind}.md"
    path.write_text(rendered.text, encoding="utf-8")

    sent = "미발송(send=False)"
    if send:
        channel = channel_from_env()
        try:
            channel.send(rendered.text)
            sent = f"발송: {channel.name}"
        except ChannelError as e:
            print(f"  발송 실패(파일은 저장됨): {e}")
            sent = "발송 실패"
    print(f"R6 {rendered.kind}: {len(rendered.text)}자 → {path} / {sent}")
    return 0


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    if kind not in ("morning", "evening"):
        print("usage: python -m trading.report <morning|evening>", file=sys.stderr)
        return 2
    return run(kind)


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(main())
