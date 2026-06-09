"""선언적 cron 스케줄 — openclaw에 등록될 잡 정의(GitOps 단일 소스, 설계서 §5).

KST 슬롯. ``round`` = ``trading.run`` 의 라운드명. ``mode``: exec(순수코드 디스패치) | llm(LLM 라운드).
``sync.py`` 가 이 매니페스트를 읽어 openclaw cron 에 idempotent 등록.
휴장일은 cron(월~금)이 아니라 각 잡 내부 가드가 거른다 — data.go.kr은 휴장일 빈 결과(SCHED 결정).
시간은 설계서 §5/§162 기준 잠정값 — 운영하며 조정.
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
    CronJob("macro-am", "10 6 * * 1-5", "collect-macro", comment="장전 거시(밤사이 US·FX)"),
    CronJob("news-am", "20 6 * * 1-5", "collect-news", comment="해외·밤사이 뉴스(SearXNG 중심)"),
    CronJob("daily-eod", "0 8 * * 1-5", "daily-eod", comment="전종목→섹터분류→스크리너→fact pack(전일 EOD)"),
    CronJob("news-pm", "20 16 * * 1-5", "collect-news", comment="국내 마감 뉴스(네이버 중심)"),
    CronJob("macro-pm", "30 16 * * 1-5", "collect-macro", comment="마감 거시"),
)


__all__ = ["CronJob", "JOBS", "TZ"]
