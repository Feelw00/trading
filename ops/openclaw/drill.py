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
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from cron_jobs import JOBS, CronJob  # noqa: E402
from sync import _ENV, _run, fetch_existing, job_log_path  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_SESSIONS = _ROOT / ".runtime" / "openclaw" / "agents" / "main" / "sessions"

# 비결정 마커 — 보이면 status ok여도 WARN. "Command exited with code 0"은 정상 종료 보고이고
# 백그라운드 전환("Command still running")+poll 대기는 플랫폼 표준 동작이라 결함 아님.
_NONZERO_EXIT = re.compile(r"Command exited with code [1-9]")
_FAIL_MARKERS = ("ModuleNotFoundError", "No module named")
# 트리거 LLM의 월권 마커(2026-06-11 pm 드릴: 프로세스 kill·DB 자체 쿼리 관측)
_OVERREACH_MARKERS = ("process kill", "\nkilled", "sqlite3 ", "SELECT COUNT")
_RUNNER_SUMMARY = re.compile(
    r"^(적재|수집|뉴스 수집|R[0-9.]+ |P1 다이제스트|섹터 보강|스크리너|해석|모닝|저녁).{0,90}"
)
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
    # detail: 러너 요약 줄 우선(백그라운드 전환 보일러플레이트 회피), 없으면 1차 출력 첫 줄
    detail = next(
        (m.group(0) for t in body for m in [_RUNNER_SUMMARY.match(t.strip())] if m),
        (body[0].strip().splitlines() or ["(빈 출력)"])[0][:100],
    )
    exec_failed = any(
        any(mark in t for mark in _FAIL_MARKERS) or _NONZERO_EXIT.search(t) for t in body
    )
    overreach = any(any(mark in t for mark in _OVERREACH_MARKERS) for t in body)
    if status != "ok":
        return ("FAIL", detail)
    if exec_failed:
        return ("WARN", f"exec 실패 흔적(임기응변 복구 의심) — {detail}")
    if overreach:
        return ("WARN", f"트리거 월권 흔적(kill/DB 조회) — {detail}")
    return ("PASS", detail)


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


def _round_process_alive(round_name: str) -> bool:
    proc = subprocess.run(
        ["pgrep", "-f", f"trading.run {round_name}"], capture_output=True, text=True
    )
    return proc.returncode == 0


_ROUND_FAIL = re.compile(r"crashed:|Traceback|\bLLMError\b")


def _follow_round(job: CronJob, pre_size: int, deadline: float) -> tuple[str, str]:
    """fire-and-forget 라운드 추적 — 로그 신장 + 프로세스 종료까지 대기 후 결과 분류."""
    log = job_log_path(job)
    grace = time.monotonic() + 30.0  # 라운드 기동 유예
    started = False
    while time.monotonic() < deadline:
        size = log.stat().st_size if log.exists() else 0
        alive = _round_process_alive(job.round)
        started = started or alive or size > pre_size
        if not started and time.monotonic() > grace:
            return ("FAIL", "라운드 미기동(로그·프로세스 없음)")
        if started and not alive and size > pre_size:
            break
        time.sleep(POLL_INTERVAL_S)
    else:
        return ("FAIL", f"라운드 미종료(드릴 타임아웃)")
    appended = log.read_text(encoding="utf-8", errors="replace")[pre_size:]
    lines = [ln.strip() for ln in appended.splitlines() if ln.strip()]
    if any(_ROUND_FAIL.search(ln) for ln in lines):
        bad = next(ln for ln in lines if _ROUND_FAIL.search(ln))
        return ("FAIL", bad[:100])
    summary = next((ln for ln in reversed(lines) if _RUNNER_SUMMARY.match(ln)), None)
    return ("PASS", (summary or (lines[-1] if lines else "(로그 출력 없음)"))[:110])


def trigger_job(job: CronJob, job_id: str, *, timeout_s: float) -> Verdict:
    name = job.name
    log = job_log_path(job)
    pre_size = log.stat().st_size if log.exists() else 0
    proc = _run(["cron", "run", job_id], check=False)
    try:
        run_id = str(json.loads(proc.stdout).get("runId", ""))
    except json.JSONDecodeError:
        return Verdict(name, "FAIL", "trigger-error", None, proc.stderr.strip()[:100])
    deadline = time.monotonic() + timeout_s
    entry = None
    while time.monotonic() < deadline:
        entry = _latest_entry(job_id, run_id=run_id)
        if entry is not None:
            break
        time.sleep(POLL_INTERVAL_S)
    if entry is None:
        return Verdict(name, "FAIL", "timeout", None, f"트리거 {timeout_s:.0f}s 내 미종료")
    status = str(entry.get("status", "?"))
    grade, detail = _classify_session(
        str(entry.get("sessionId", "")), status, run_marker=f" {name}] "
    )
    if grade == "FAIL":
        return Verdict(name, grade, status, entry.get("durationMs"), detail)
    # 트리거는 발사만 — 라운드 완주는 로그·프로세스로 추적
    round_grade, round_detail = _follow_round(job, pre_size, deadline)
    final = "WARN" if (grade == "WARN" and round_grade == "PASS") else round_grade
    return Verdict(name, final, status, entry.get("durationMs"), round_detail)


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

    by_name = {j.name: j for j in JOBS}
    verdicts: list[Verdict] = []
    for n in names:
        job_id = str(existing[n]["id"])
        if audit_only:
            verdicts.append(audit_job(n, job_id))
        else:
            print(f"… {n} 트리거", flush=True)
            v = trigger_job(by_name[n], job_id, timeout_s=timeout_s)
            print(f"  [{v.grade}] {v.detail[:90]}", flush=True)
            verdicts.append(v)
    return report(verdicts)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
