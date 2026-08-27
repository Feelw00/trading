"""선언적 cron 스케줄 — openclaw에 등록될 잡 정의(GitOps 단일 소스, 설계서 v0.3 §5).

**v0.3 (2026-08-27 재작성, PIVOT-1):** 장기 사이클·가치 체제 — 장중 상주 프로세스 없음,
슬롯 16→2. v0.2 스윙 슬롯(감시기·LLM 체인·아침 선택 등)은 폐기 — 부활 금지(FROZEN.md).

KST 슬롯. ``round`` = ``trading.run`` 의 라운드명. 전 라운드 순수 코드(LLM 없음) —
mode=exec(``--tools exec --light-context``, 트리거 에이전트는 데이터·판단 미개입).
휴장일은 cron(월~금)이 아니라 각 잡 내부 가드·빈 응답 관측이 거른다(SCHED 결정).
``sync.py`` 가 이 매니페스트를 읽어 openclaw cron에 idempotent 등록.

미등록 슬롯(의도적 — 전제 미충족):
- 월간 R5(DCA 초안·결재 보고): POLICY_PARAMS §6(비중·DCA·veto 창) 결재 후.
- 월간 집행 슬롯: Phase 4 전제(§10) — 페이퍼 관찰 1~2개월 + 운영자 전환 결정.
- 분기 R7(이행 점검·규칙 감사): 첫 분기 도래(페이퍼 데이터 축적) 시점에 등록.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CronJob:
    name: str
    cron: str            # crontab 표현식(분 시 일 월 요일)
    round: str           # trading.run <round>
    mode: str = "exec"   # exec(순수코드, --tools exec --light-context) | llm(미사용 경로)
    comment: str = ""


TZ = "Asia/Seoul"

JOBS: tuple[CronJob, ...] = (
    # --- 일간 EOD (설계서 v0.3 §5: 거래일 18:00) ---
    # data.go.kr T-1 공개 시차·애프터마켓(9/14~ 16:00-20:00)과 무관 — 전부 순수 수집이라
    # 휴면 창 제약 없음. 시세 갭 치유→섹터 태깅→재무 자연 갱신→수급 창 축적(KIS 1콜≈30거래일).
    CronJob("eod-v3", "0 18 * * 1-5", "eod-v3",
            comment="일간 EOD 체인: 시세→섹터→재무(멱등)→수급 창 축적. 논제 가드는 Phase 4 배선"),
    # --- 주간 계측·보고 (설계서 v0.3 §5: 토 09:00~11:00 → 단일 체인 09:30 통합) ---
    # R2 밸류에이션→R3 온도계→R4 페이퍼 스크리닝→주간 다이제스트(.runtime/reports/).
    # §5의 분할 슬롯(09:00/09:30/10:00/11:00)은 체인 순차 실행으로 통합 — 중간 산출물이
    # 전부 DB 경유라 슬롯 분리 이득 없음(트리거 턴 3회 절약).
    CronJob("weekly-v3", "30 9 * * 6", "weekly-v3",
            comment="주간 계측 체인: 밸류에이션→온도계→R4 페이퍼→다이제스트(집행 없음)"),
)


__all__ = ["CronJob", "JOBS", "TZ"]
