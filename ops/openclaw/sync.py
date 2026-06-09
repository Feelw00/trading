"""ops/openclaw cron 동기화 — JOBS 매니페스트를 openclaw cron에 idempotent 등록.

기본 **dry-run**(등록할 명령만 출력). ``--apply`` 시 실제 openclaw 호출(openclaw 설치 필요).

⚠️ openclaw cron CLI 정확 구문은 **설치본에서 검증 후 확정**(절대금지 #1: 추측 구현 금지).
아래 명령 템플릿은 CLAUDE.md/ops 문서의 문서화된 플래그(`--cron --tz --tools exec --light-context`)
기준이며, `--apply`는 검증 전까지 명령을 출력만 하고 실행하지 않는다(SAFE 기본).

실행: ``python ops/openclaw/sync.py [--apply]`` (repo 루트에서).
"""

import sys
from pathlib import Path

# repo 루트를 path에 추가(ops 스크립트가 trading 패키지·동일 디렉터리 모듈 import)
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cron_jobs import JOBS, TZ, CronJob  # noqa: E402


def openclaw_command(job: CronJob) -> str:
    """이 잡을 등록하는 의도된 openclaw 명령(검증 대상 템플릿)."""
    exec_target = f"python -m trading.run {job.round}"
    if job.mode == "exec":
        tools = "--tools exec --light-context"
    else:  # llm 라운드
        tools = "--message --model \"$OPENCLAW_MODEL\""
    return f'openclaw cron add {job.name} --cron "{job.cron}" --tz {TZ} {tools} -- {exec_target}'


def validate() -> list[str]:
    """매니페스트 정합성 — round가 trading.run.ROUNDS에 존재하는지."""
    from trading.run import ROUNDS

    return [f"{j.name}: round '{j.round}' 미등록(trading.run.ROUNDS)" for j in JOBS if j.round not in ROUNDS]


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    errors = validate()
    if errors:
        print("매니페스트 오류:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"# openclaw cron 동기화 ({'APPLY 보류 — 검증 전' if apply else 'dry-run'}) · {len(JOBS)}개 잡")
    for job in JOBS:
        print(f"# [{job.mode}] {job.name}: {job.comment}")
        print(openclaw_command(job))
    if apply:
        print("\n⚠️ --apply는 openclaw CLI 구문 검증 후 활성화. 현재는 출력만(SAFE).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
