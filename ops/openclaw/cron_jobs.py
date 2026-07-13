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
    # 거시 수집은 독립 슬롯이 아니라 report-am/report-pm 라운드에 내장(trading.run) —
    # 트리거 에이전트 턴 최소화(자체 판단 수집 여지 제거). R3/R5는 최신 landing(기존 데이터)만 읽는다.
    # 뉴스 수집은 **매일**(주말 포함, 운영자 결정 2026-07-12) — 순수 어댑터라 비용 미미하고,
    # 주말 미수집 시 월요일 아침 쿼리 상한에 토요일분이 잘릴 수 있다(신선도 게이트 3일이라
    # 주말 수집분은 월요일 R2가 정상 처리). 어제(7/11 토) 캐치업 실행 403건이 주말 동작 실증.
    CronJob("news-am", "20 6 * * *", "collect-news", comment="R0 수집 — 해외·밤사이 뉴스(3계층, 매일)"),
    CronJob("score-am", "30 6 * * 1-5", "score-news", comment="R1 게이트→R2 분류·스코어(내부 claude -p, R2_MODEL)"),
    CronJob("verify-am", "45 6 * * 1-5", "verify-catalysts", comment="R4 적대검증(고강도·single_stock 선별, 내부 claude -p)"),
    CronJob("reason-am", "55 6 * * 1-5", "reason-theses", comment="R3 페르소나 분석(촉매 보유 종목, 내부 claude -p ×3)"),
    # --- 장중 감시(순수 코드, fire-and-forget 루프 — 15:00 자기 종료) ---
    # arm 조건(전일고가·체결강도·호가)은 장중 아무 때나 충족 가능 → 감시기가 충족 순간 P0.
    # 12:00 슬롯은 재기동 안전망 — 이미 가동 중이면 하트비트 파일로 자기 종료(중복 알림은 WatchStore dedup).
    CronJob("arm-watch", "0 9 * * 1-5", "arm-watch", comment="장중 발동 감시 루프 기동(approved 풀, P0 발화 — 15:00까지 진입, 20:00까지 청산 전용)"),
    CronJob("arm-watch-relaunch", "0 12 * * 1-5", "arm-watch", comment="감시 루프 재기동 안전망(사망 대비, 중복은 자기 종료)"),
    # EXEC-3(2026-07-13): 15:00 이후~20:00은 청산 전용 감시(진입 금지 — 정규 잔여 30분+NXT 애프터).
    # 이 슬롯은 오후 사망 대비 재기동 안전망 — already-alive면 하트비트로 자기 종료.
    CronJob("arm-watch-exit", "35 15 * * 1-5", "arm-watch", comment="청산 전용 감시 재기동 안전망(부분 레그·브래킷 관리, 애프터 커버)"),
    # 16:05 — data.go.kr T-1 EOD가 08:00엔 미공개(2026-06-11 관측: 09시에도 없음, 전일 14:49엔 있음).
    # 마감 직후로 옮겨 pm 라운드(16:20~)가 최신 스크리너 후보를 쓰게 한다.
    CronJob("daily-eod", "5 16 * * 1-5", "daily-eod", comment="전종목→섹터분류→스크리너→fact pack(T-1 EOD)"),
    # --- 오후(마감) ---
    # 수집(R0)은 순수 어댑터라 애프터마켓 중에도 허용 — 마감 뉴스를 제때 받는다.
    CronJob("news-pm", "20 16 * * *", "collect-news", comment="R0 수집 — 국내 마감·오후 뉴스(네이버 중심, 매일)"),
    # ⚠️ pm LLM 체인은 **애프터마켓(16:00–20:00, 2026-09-14~) 마감 후**로 배치(CAL-3 결정: 장중 = 정규장+애프터).
    # 이전 16:32/16:45/16:55 슬롯은 9/14부터 §5 휴면 창이라 `trading.run` 가드가 스킵시킨다.
    # 저녁 결재 보고를 21:30에 유지하려면 체인을 20:00 벽에 최대한 붙여야 한다(운영자 요청).
    # 실측 소요: R2 ~64분(장대) · R4 ~23분 · R3 ~25분 · R5 ~4분.
    # R4/R3는 R2 완료를 기다리지 않고 그때까지 적재된 이벤트를 처리한다(기존 16:32/16:45/16:55와 동일한 겹침).
    # R5만 R3 산출(논제)을 DB로 받으므로 R3 종료(~20:57) 뒤에 둔다.
    CronJob("score-pm", "2 20 * * 1-5", "score-news", comment="R1 게이트→R2 분류·스코어(애프터마켓 후)"),
    CronJob("verify-pm", "15 20 * * 1-5", "verify-catalysts", comment="R4 적대검증(선별)"),
    CronJob("reason-pm", "32 20 * * 1-5", "reason-theses", comment="R3 페르소나 분석"),
    # --- 보고 (설계서 §5·§8: 06:50 모닝 / 21:30 저녁 결재) — 거시 재수집 내장(렌더 직전) ---
    # 저녁 결재 21:00 → 21:30(CAL-3: 애프터마켓 마감 20:00 뒤로 체인이 밀린 만큼만 순연).
    CronJob("report-am", "50 6 * * 1-5", "report-morning", comment="거시 전부 수집(결정론)→R6 모닝 브리핑"),
    CronJob("report-pm", "30 21 * * 1-5", "report-evening", comment="거시 재수집(결정론)→R6 저녁 결재 보고(승인 요청)"),
    # --- 아침 선택 (설계서 §5: 08:50 R5.5 — 순수 코드, 휴장·장중은 러너 가드가 거부) ---
    CronJob("select-am", "50 8 * * 1-5", "select-playbooks", comment="R5.5 플레이북 선택·arm(순수 코드)"),
    # --- 야간 합성 (R5 — R3 종료 후·보고 전. 장중 실행은 러너 내부 가드가 거부) ---
    CronJob("synth-pm", "5 21 * * 1-5", "synth-playbooks", comment="R5 합성·플레이북·주문 초안(내부 claude -p)"),
    # --- 주간 평가 (설계서 §5: 토 10:00 R7) ---
    CronJob("eval-sat", "0 10 * * 6", "evaluate", comment="R7 평가·캘리브레이션+레짐(채점=코드, 해석=claude -p)"),
    # --- 알림 (설계서 §8: P1 묶음 = 점심·마감) ---
    CronJob("digest-noon", "30 12 * * 1-5", "alerts-digest", comment="P1 다이제스트(점심)"),
    CronJob("digest-close", "40 15 * * 1-5", "alerts-digest", comment="P1 다이제스트(마감 직후)"),
)


__all__ = ["CronJob", "JOBS", "TZ"]
