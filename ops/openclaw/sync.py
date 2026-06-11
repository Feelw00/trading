"""ops/openclaw cron 동기화 — JOBS 매니페스트를 openclaw cron에 idempotent 등록.

기본 dry-run(명령 출력만). ``--apply`` 시 실제 등록·갱신·제거.
멱등성: 매니페스트와 일치하는 잡은 skip, 다르면 rm+add, 선언되지 않은 기존 잡은 stale로 제거.

호출 전제:
- ``bootstrap.sh`` 통과 → ``.runtime/openclaw/openclaw.json`` 존재
- ``start-gateway.sh`` 로 트레이딩 게이트웨이 기동 중
- ``pair.sh`` 로 CLI 디바이스가 operator.admin 보유

실행: ``poetry run python ops/openclaw/sync.py [--apply]`` (repo 루트).
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cron_jobs import JOBS, TZ, CronJob  # noqa: E402

_OC = os.path.expanduser("~/.openclaw/bin/openclaw")
_ENV: dict[str, str] = {
    **os.environ,
    "OPENCLAW_STATE_DIR": str(_ROOT / ".runtime/openclaw"),
    "OPENCLAW_CONFIG_PATH": str(_ROOT / ".runtime/openclaw/openclaw.json"),
}


def add_args(job: CronJob) -> list[str]:
    """openclaw cron add 인자(list[str]) — exec 모드만."""
    if job.mode != "exec":
        raise NotImplementedError(
            f"{job.name}: mode={job.mode} 미지원 — NEWS-R2/SCHED-3에 따라 exec만 사용"
        )
    return [
        "cron", "add", job.name,
        "--cron", job.cron,
        "--tz", TZ,
        "--tools", "exec",
        "--light-context",
        # 알림·보고는 Python 두뇌가 Telegram 직접 발송(alerts/) — openclaw 딜리버리 미사용.
        # 채널 미설정 fail-closed로 잡이 error 처리되는 것 방지(2026-06-10 enable 검증).
        "--no-deliver",
        "--",
        dispatch_message(job),
    ]


def exec_command(job: CronJob) -> str:
    """결정론 exec 명령 — 절대경로 + cd 필수(2026-06-11 첫 자동 운영일 결함).

    exec cwd는 에이전트 워크스페이스고 PATH의 python은 venv가 아니다 — 상대경로 data/가
    빈 DB로 열려 조용히 스킵되고, LLM 트리거가 임기응변 복구하는 비결정 경로가 생긴다
    (SCHED-2 위반). 경로는 sync 시점에 기기별로 렌더(GitOps — clone 위치 무관).
    """
    return f"cd {shlex.quote(str(_ROOT))} && .venv/bin/python -m trading.run {job.round}"


def dispatch_message(job: CronJob) -> str:
    """결정론 디스패치 프롬프트(SCHED-2) — 트리거 LLM의 행동을 명령 실행·대기로만 제한.

    2026-06-11 pm 드릴 결함: exec가 2분(플랫폼 한도) 넘으면 백그라운드 전환되는데,
    트리거 LLM이 poll 대기 대신 **프로세스 kill·소스 열람·DB 자체 쿼리**까지 했다
    (verify-pm R4가 kill당해 검증 0건). 백그라운드 전환은 막을 수 없으므로(YIELD_MS
    클램프 120s) 프롬프트로 행동을 결정론적으로 묶는다.
    """
    return (
        f"exec 도구로 아래 명령을 정확히 1회 실행하라:\n{exec_command(job)}\n"
        "규칙:\n"
        "1. 명령이 백그라운드 세션으로 전환되면(Command still running) 종료될 때까지 "
        "process poll만 반복하라. 수십 분 걸릴 수 있으며 그것이 정상이다.\n"
        "2. 절대 금지: 프로세스 kill, 명령 수정·재실행·추가 실행, 파일·소스·DB 열람, "
        "결과 해석·요약 가공. 너는 트리거다 — 판단하지 않는다.\n"
        "3. 종료 후 명령의 마지막 출력 줄과 종료 코드만 보고하라."
    )


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_OC, *args], env=_ENV, capture_output=True, text=True, check=check
    )


def fetch_existing() -> dict[str, dict[str, Any]]:
    """name → 잡 dict. 게이트웨이 미응답이면 빈 dict 반환(첫 sync 정상)."""
    proc = _run(["cron", "list", "--all", "--json"], check=False)
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return {}
    return {j["name"]: j for j in jobs if isinstance(j, dict) and j.get("name")}


def matches_manifest(job: CronJob, existing: dict[str, Any]) -> bool:
    """매니페스트와 동일 스펙인지(이름은 호출 측에서 매칭됨)."""
    sched = existing.get("schedule") or {}
    if sched.get("expr") != job.cron or sched.get("tz") != TZ:
        return False
    payload = existing.get("payload") or {}
    if payload.get("message") != dispatch_message(job):
        return False
    if not payload.get("lightContext"):
        return False
    if set(payload.get("toolsAllow") or []) != {"exec"}:
        return False
    if (existing.get("delivery") or {}).get("mode") != "none":
        return False
    return True


def validate() -> list[str]:
    from trading.run import ROUNDS

    return [
        f"{j.name}: round '{j.round}' 미등록(trading.run.ROUNDS)"
        for j in JOBS
        if j.round not in ROUNDS
    ]


def apply_jobs() -> int:
    existing = fetch_existing()
    declared = {j.name for j in JOBS}

    # 1. 매니페스트에 없는 잡 제거(stale)
    for name, info in sorted(existing.items()):
        if name not in declared:
            print(f"[rm-stale] {name}")
            _run(["cron", "rm", info["id"]])

    # 2. 매니페스트 동기화
    for job in JOBS:
        cur = existing.get(job.name)
        if cur and matches_manifest(job, cur):
            print(f"[skip]     {job.name}")
            continue
        if cur:
            print(f"[update]   {job.name}")
            _run(["cron", "rm", cur["id"]])
        else:
            print(f"[add]      {job.name}")
        _run(add_args(job))

    print(f"# 동기화 완료 · {len(JOBS)}개 잡 정상화")
    return 0


def _dry_run() -> int:
    print(f"# openclaw cron 동기화 (dry-run) · {len(JOBS)}개 잡")
    print(f"# OPENCLAW_STATE_DIR={_ENV['OPENCLAW_STATE_DIR']}")
    for job in JOBS:
        print(f"# [{job.mode}] {job.name}: {job.comment}")
        print(shlex.join([_OC, *add_args(job)]))
    return 0


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    errors = validate()
    if errors:
        print("매니페스트 오류:")
        for e in errors:
            print(f"  - {e}")
        return 1
    return apply_jobs() if apply else _dry_run()


if __name__ == "__main__":
    raise SystemExit(main())
