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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cron_jobs import JOBS, TZ, CronJob  # noqa: E402

_OC = os.path.expanduser("~/.openclaw/bin/openclaw")

# setsid 절대경로 — cron 잡은 openclaw exec 의 최소 PATH 에서 돌기 때문에 이름만으론 못 찾는다.
# Linux: /usr/bin/setsid (util-linux 기본). macOS: 미제공 → Homebrew util-linux(keg-only).
_SETSID_CANDIDATES = (
    "/usr/bin/setsid",
    "/opt/homebrew/opt/util-linux/bin/setsid",  # Apple Silicon
    "/usr/local/opt/util-linux/bin/setsid",  # Intel mac
)


def _resolve_setsid() -> str:
    """setsid 실행 파일의 절대경로. 없으면 즉시 실패(조용한 cron 전멸 방지)."""
    found = shutil.which("setsid") or next(
        (p for p in _SETSID_CANDIDATES if os.access(p, os.X_OK)), None
    )
    if not found:
        raise SystemExit(
            "setsid 없음 — cron 잡이 전부 실패한다. macOS: brew install util-linux"
        )
    return found


_SETSID = _resolve_setsid()
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


def job_log_path(job: CronJob) -> Path:
    """잡별 라운드 로그(fire-and-forget의 관측 지점 — drill·운영자가 tail)."""
    return _ROOT / ".runtime" / "logs" / "cron" / f"{job.name}.log"


def exec_command(job: CronJob) -> str:
    """결정론 exec 명령 — fire-and-forget(2026-06-11 pm 드릴 교훈).

    - 절대경로 + cd: exec cwd는 에이전트 워크스페이스고 PATH python은 venv가 아니다
      (상대경로 data/가 빈 DB로 열려 조용히 스킵 — 첫 자동 운영일 결함).
    - **setsid -f 완전 분리 + 즉시 반환**: 긴 라운드를 트리거 LLM이 poll로 babysit하게
      두면 프로세스 kill·DB 자체 조회(월권), poll 턴 누적으로 모델 rate limit까지 발생.
      트리거 턴은 발사 확인 1줄로 수 초에 끝낸다. nohup &만으로는 부족 — openclaw exec가
      **에이전트 턴 종료 시 프로세스 그룹을 정리**해 장시간 라운드가 중도 사망한다
      (2026-06-11 관측: digest 2초 생존, verify 10분+ 사망). setsid로 세션 분리.
      라운드 성패 가시성은 openclaw가 아니라 Python이 담당(trading.run 실패 P1 + 잡별 로그).
    - setsid는 **절대경로**(_SETSID). exec 의 PATH 에 없고, macOS 는 기본 미제공이라
      이름만 쓰면 잡이 조용히 전멸한다 (2026-07-11 Mac mini 이관에서 발견).
    """
    log = job_log_path(job)
    return (
        f"cd {shlex.quote(str(_ROOT))} && mkdir -p {shlex.quote(str(log.parent))} && "
        f"{shlex.quote(_SETSID)} -f sh -c '.venv/bin/python -m trading.run {job.round} "
        f">> {shlex.quote(str(log))} 2>&1' && echo launched:{job.name}"
    )


def dispatch_message(job: CronJob) -> str:
    """결정론 디스패치 프롬프트(SCHED-2) — 발사 후 즉시 종료, 판단 금지."""
    return (
        f"exec 도구로 아래 명령을 정확히 1회 실행하라:\n{exec_command(job)}\n"
        "출력의 launched 줄을 그대로 보고하고 즉시 끝내라. "
        "대기·재실행·프로세스 조작·파일/DB 열람·해석 금지 — 너는 트리거다."
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
