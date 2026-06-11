"""openclaw cron 드릴 — 슬롯을 기다리지 않고 잡을 즉시 트리거하고 **결정론까지 검증**.

배경(2026-06-11 첫 자동 운영일 교훈): cron status=ok는 신뢰 지표가 아니다 —
exec가 1차 실패해도 LLM 트리거가 임기응변으로 복구해 ok가 찍힌다(SCHED-2 위반 은폐).
이 드릴은 잡 status에 더해 **세션 로그의 1차 exec 결과·임기응변 여부**를 판정한다.

사용 (repo 루트):
  poetry run python ops/openclaw/drill.py --audit              # 트리거 없이 전 잡 최근 런 검증
  poetry run python ops/openclaw/drill.py macro-pm digest-noon # 지정 잡 순차 트리거+검증
  poetry run python ops/openclaw/drill.py --cycle pm           # 사이클 전체(시간순) 트리거+검증
  poetry run python ops/openclaw/drill.py --cycle am --timeout 2400

판정:
  PASS = status ok + 1차 exec 성공 + 임기응변 없음
  WARN = status ok 이지만 1차 exec 실패 후 임기응변 복구(비결정 — 명령·환경 점검 필요)
  FAIL = status error / 타임아웃 / 세션 미확인

주의: 모의가 아니라 **실제 경로 그대로** 실행한다 — LLM 라운드는 비용 발생,
보고·알림은 실제 Telegram으로 발송, DB에 실제 적재(append-only·멱등이라 안전).
"""

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from cron_jobs import JOBS, CronJob  # noqa: E402
from sync import _ENV, _run, fetch_existing  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_SESSIONS = _ROOT / ".runtime" / "openclaw" / "agents" / "main" / "sessions"

# 1차 exec 실패 마커 — 이게 보이면 트리거가 임기응변으로 복구했더라도 WARN
_FAIL_MARKERS = ("ModuleNotFoundError", "No module named", "Command exited with code")
_TEXT_RE = re.compile(r'"text":"((?:[^"\\]|\\.)*)"')

POLL_INTERVAL_S = 5.0
DEFAULT_TIMEOUT_S = 1800.0


@dataclass(frozen=True)
class Verdict:
    job: str
    grade: str               # PASS | WARN | FAIL
    status: str              # ok | error | timeout | ...
    duration_ms: int | None
    detail: str              # 1차 exec 출력 첫 줄 또는 에러


def _cron_minute_hour(job: CronJob) -> tuple[int, int]:
    parts = job.cron.split()
    try:
        return int(parts[1]), int(parts[0])  # (hour, minute)
    except (IndexError, ValueError):
        return (99, 99)


def cycle_jobs(which: str) -> list[CronJob]:
    """시간순 사이클 — am: <12시 잡 / pm: ≥12시 잡(eval-sat 주간 제외) / all: 전부."""
    ordered = sorted(JOBS, key=_cron_minute_hour)
    if which == "all":
        return list(ordered)
    if which == "am":
        return [j for j in ordered if _cron_minute_hour(j)[0] < 12]
    return [j for j in ordered if _cron_minute_hour(j)[0] >= 12 and j.name != "eval-sat"]


def _session_texts(session_id: str) -> list[str]:
    path = _SESSIONS / f"{session_id}.jsonl"
    if not path.exists():
        return []
    texts = []
    for m in _TEXT_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
        try:
            texts.append(json.loads(f'"{m.group(1)}"'))
        except json.JSONDecodeError:
            continue
    return texts


def _classify_session(session_id: str, status: str, run_marker: str | None) -> tuple[str, str]:
    """세션 로그 → (grade, detail). 누적 세션이면 run_marker(명령 프롬프트) 이후만 본다."""
    texts = _session_texts(session_id)
    if run_marker is not None:
        idx = max((i for i, t in enumerate(texts) if run_marker in t), default=None)
        if idx is not None:
            texts = texts[idx:]
    if not texts:
        return ("FAIL", "세션 로그 미확인")
    body = texts[1:]  # [0]=cron 프롬프트
    if not body:
        return ("FAIL", "exec 출력 없음")
    first_exec = body[0].strip().splitlines()
    first_line = first_exec[0][:100] if first_exec else "(빈 출력)"
    improvised = any(any(mark in t for mark in _FAIL_MARKERS) for t in body)
    if status != "ok":
        return ("FAIL", first_line)
    if improvised:
        return ("WARN", f"1차 exec 실패 후 임기응변 복구 — {first_line}")
    return ("PASS", first_line)


def _latest_entry(job_id: str, *, run_id: str | None = None) -> dict[str, Any] | None:
    proc = _run(["cron", "runs", "--id", job_id], check=False)
    if proc.returncode != 0:
        return None
    try:
        entries = json.loads(proc.stdout).get("entries", [])
    except json.JSONDecodeError:
        return None
    if run_id is not None:
        entries = [e for e in entries if e.get("runId") == run_id]
    if not entries:
        return None
    latest: dict[str, Any] = max(entries, key=lambda e: int(e.get("ts", 0)))
    return latest


def audit_job(name: str, job_id: str) -> Verdict:
    entry = _latest_entry(job_id)
    if entry is None:
        return Verdict(name, "FAIL", "no-run", None, "실행 이력 없음")
    grade, detail = _classify_session(
        str(entry.get("sessionId", "")), str(entry.get("status", "?")), run_marker=f" {name}] "
    )
    return Verdict(name, grade, str(entry.get("status")), entry.get("durationMs"), detail)


def trigger_job(name: str, job_id: str, *, timeout_s: float) -> Verdict:
    proc = _run(["cron", "run", job_id], check=False)
    try:
        run_id = str(json.loads(proc.stdout).get("runId", ""))
    except json.JSONDecodeError:
        return Verdict(name, "FAIL", "trigger-error", None, proc.stderr.strip()[:100])
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        entry = _latest_entry(job_id, run_id=run_id)
        if entry is not None:
            grade, detail = _classify_session(
                str(entry.get("sessionId", "")), str(entry.get("status", "?")),
                run_marker=f" {name}] ",
            )
            return Verdict(name, grade, str(entry.get("status")), entry.get("durationMs"), detail)
        time.sleep(POLL_INTERVAL_S)
    return Verdict(name, "FAIL", "timeout", None, f"{timeout_s:.0f}s 내 종료 안 됨")


def report(verdicts: list[Verdict]) -> int:
    width = max((len(v.job) for v in verdicts), default=10)
    for v in verdicts:
        dur = f"{(v.duration_ms or 0) / 1000:.0f}s" if v.duration_ms else "-"
        print(f"[{v.grade}] {v.job:<{width}} status={v.status:<8} {dur:>6}  {v.detail}")
    fails = [v for v in verdicts if v.grade == "FAIL"]
    warns = [v for v in verdicts if v.grade == "WARN"]
    print(f"# {len(verdicts)}개: PASS {len(verdicts) - len(fails) - len(warns)} / "
          f"WARN {len(warns)} / FAIL {len(fails)}")
    return 1 if fails or warns else 0


def main(argv: list[str]) -> int:
    audit_only = "--audit" in argv
    timeout_s = DEFAULT_TIMEOUT_S
    if "--timeout" in argv:
        timeout_s = float(argv[argv.index("--timeout") + 1])
    names: list[str]
    if "--cycle" in argv:
        names = [j.name for j in cycle_jobs(argv[argv.index("--cycle") + 1])]
    else:
        names = [a for a in argv if not a.startswith("--") and a not in (f"{timeout_s}", f"{timeout_s:g}")]
        names = [n for n in names if not n.replace(".", "").isdigit()]
    declared = {j.name for j in JOBS}
    existing = fetch_existing()
    if audit_only and not names:
        names = [j.name for j in sorted(JOBS, key=_cron_minute_hour)]
    if not names:
        print(__doc__)
        return 2
    unknown = [n for n in names if n not in declared]
    if unknown:
        print(f"미선언 잡: {unknown} (cron_jobs.py JOBS 기준)", file=sys.stderr)
        return 2
    missing = [n for n in names if n not in existing]
    if missing:
        print(f"미등록 잡: {missing} — 먼저 sync.py --apply", file=sys.stderr)
        return 2

    verdicts: list[Verdict] = []
    for n in names:
        job_id = str(existing[n]["id"])
        if audit_only:
            verdicts.append(audit_job(n, job_id))
        else:
            print(f"… {n} 트리거", flush=True)
            verdicts.append(trigger_job(n, job_id, timeout_s=timeout_s))
    return report(verdicts)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
