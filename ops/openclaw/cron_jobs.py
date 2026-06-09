"""선언적 cron 스케줄 — openclaw에 등록될 잡 정의(GitOps 단일 소스, 설계서 §5).

KST 슬롯. ``round`` = ``trading.run`` 의 라운드명. ``mode``: exec(순수코드 디스패치) | llm(openclaw 직접 LLM).
``sync.py`` 가 이 매니페스트를 읽어 openclaw cron 에 idempotent 등록.
휴장일은 cron(월~금)이 아니라 각 잡 내부 가드가 거른다 — data.go.kr은 휴장일 빈 결과(SCHED 결정).
시간은 설계서 §5/§162 + PROPOSALS P-4 §4 기준 잠정값 — 운영하며 조정.

**중요(NEWS-R2/SCHED-3):** R2(score-news)·R4(verify-catalysts)도 ``mode=exec`` 다 —
openclaw는 ``python -m trading.run`` 만 exec하고 **LLM 호출은 Python 두뇌가 내부에서 claude -p로**
직접 한다(openclaw provider 라우팅 미사용). ``mode=llm``(openclaw --message --model)은 미사용 경로.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CronJob:
    name: str
    cron: str            # crontab 표현식(분 시 일 월 요일)
    round: str           # trading.run <round>
    mode: str = "exec"   # exec(순수코드, --tools exec --light-context) | llm(--message --model)
    comment: str = ""


TZ = "Asia/Seoul"

JOBS: tuple[CronJob, ...] = (
    # --- 오전(장전) ---
    CronJob("macro-am", "10 6 * * 1-5", "collect-macro", comment="장전 거시(밤사이 US·FX)"),
    CronJob("news-am", "20 6 * * 1-5", "collect-news", comment="R0 수집 — 해외·밤사이 뉴스(3계층)"),
    CronJob("score-am", "30 6 * * 1-5", "score-news", comment="R1 게이트→R2 분류·스코어(내부 claude -p, R2_MODEL)"),
    CronJob("verify-am", "45 6 * * 1-5", "verify-catalysts", comment="R4 적대검증(고강도·single_stock 선별, 내부 claude -p)"),
    CronJob("daily-eod", "0 8 * * 1-5", "daily-eod", comment="전종목→섹터분류→스크리너→fact pack(전일 EOD)"),
    # --- 오후(마감) ---
    CronJob("news-pm", "20 16 * * 1-5", "collect-news", comment="R0 수집 — 국내 마감 뉴스(네이버 중심)"),
    CronJob("score-pm", "32 16 * * 1-5", "score-news", comment="R1 게이트→R2 분류·스코어(macro-pm와 시각 분리)"),
    CronJob("verify-pm", "45 16 * * 1-5", "verify-catalysts", comment="R4 적대검증(선별)"),
    CronJob("macro-pm", "30 16 * * 1-5", "collect-macro", comment="마감 거시"),
)


__all__ = ["CronJob", "JOBS", "TZ"]
