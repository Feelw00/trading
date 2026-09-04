<!--
이 프로젝트의 boot/end 정의. 유저 레벨 /boot·/end가 이 파일을 그대로 수행한다.
기존 이력 컨벤션 매핑: NEXT.md 역할=history/work/CURRENT.md · histories/ 역할=history/work/archive/
(신규 NEXT.md·histories/·aliases/ 생성 금지 — CLAUDE.md 작업 방식이 상위 기준)
[2026-08-26 v0.3 비준으로 전면 개정 — 스윙 운영 절차(감시기·게이트웨이·EXEC_MODE 점검) 폐기.
 시스템은 장기 사이클·가치 투자로 피벗, 현재 리팩터링(Phase 0~) 단계이며 자동 매매 없음.]
-->

## boot

### sync
- `TZ=Asia/Seoul date '+%Y-%m-%d %H:%M (%a)'` — **콘솔 날짜 기준**(모델 추정 날짜 신뢰 금지)
- `git status --short` + 최근 커밋 1줄 — 미커밋 잔여 확인

### state
- 헌법 = `docs/trading-system-design.md` **v0.3**(2026-08-26 비준) · 최종 기준 = `docs/OPEN_QUESTIONS.md`(PIVOT-1~8)
- 환경 프로비저닝 여부 한 줄: poetry / `.env` / `~/.openclaw` 유무 (Phase 0 완료 전엔 없음이 정상. DB는 SQLite 파일 — docker/PostgreSQL 불필요, PIVOT-8)
- **cron 정상 상태 = v0.3 5잡**(eod-v3 18:00 · weekly-v3 토 09:30 · check 2 · guide-orders 08:40)
  — `cron list`로 확인(.env 소싱 + `OPENCLAW_STATE_DIR=$PWD/.runtime/openclaw OPENCLAW_CONFIG_PATH=$PWD/.runtime/openclaw/openclaw.json ~/.openclaw/bin/openclaw cron list --all` — `OPENCLAW_HOME`으로는 1006 끊김).
  스윙 잡(v0.2 16잡)이 살아 있으면 그게 사고다(PIVOT-1). 장중 상주 없음
- **⚠️ 실주문 경로 가동 중(EXEC-12 live, 2026-09-02)**: guide-orders가 토스 조건주문(SELL·지정가)을
  실등록. `.runtime/exec/KILL` 없음 = 가동. 토스 OPEN 조건주문 = 저널(data/broker.sqlite) 대조

### gotchas
- **v0.2 스윙 계열(watch/·selector/·executor 브래킷·arm-check·approve 스킬) = ❄️ 동결** — 수정·재가동·cron 등록 금지
- **운영자 답변·결정은 바로 인코딩하지 말 것** — 판정(합당/반대)+근거+대안 먼저, 합의 후 반영 (2026-08-26 지시 ×2)
- 판단 라운드(R1~R5·논제 가드)에 LLM 금지 — LLM은 R0 수집(하네스)·서술(R4.5/R7)만
- 알림은 ALERT-1: 체인 종료 시 텔레그램 1통(P1 동봉) — 별도 다이제스트 슬롯 없음. 조건부 주문은
  EXEC-12(가이드 매도 SELL·상방 감시)만 허용, 손절·OCO·브래킷은 동결
- 시작가(가이드 기준가)는 불변 — `paper rebase`는 `--correction <사유>` 정정 전용
- **페이퍼 편입은 실보유(guide-orders 자동)·명시 `paper register <심볼>`만**(9/2 자동 등록 폐지). 승인 종목
  노출 = 심사 승인 ∧ 회귀 여력 ≥ +30%(미달 "승인 보류" 파생). 목표가 반영은 `paper retarget --reason`만.
  **회귀 여력 = min(자기 역사 5년 밴드 중앙, 정당 PBR (ROE₅−1%)/(10%−1%)) ÷ 현재 PBR − 1**(v2.13·v2.14, 9/3 —
  섹터 중앙 PBR 폐기, `valuation/band.py`). COE·g 변경은 결재 사항
- **9/3 결재(policy v2.15·GUIDE-1·EXEC-14)**: 과열 산업(⚠)은 `paper register` 불가(승인 노출은 유지) · 심사 승인
  없는 실보유는 편입 보류(매도 예약 없음·P1) · 목표가 자동 반영 없음(retarget만) · **매수는 운영자 재량**(§6 자동
  DCA 보류) · 매도 예약은 다음 선 1개만(의도) · 전량 정리는 앱 직접 — 세션이 매수·정리 주문을 내지 않는다
- **SCREEN-1(v2.16, 9/3)**: 관리종목·거래정지·감사의견 비적정은 R4 하드 탈락 — 소스는 KIS `inquire-price`
  (`mang_issu_cls_code` Y·상태 51/58, `temp_stop_yn`은 지표 아님)·DART 감사의견(당기 행 전부, ≠적정의견). 데이터 없으면
  탈락이 아니라 종목별 미적용 고지. `data/status.sqlite`(eod `status-v3`·weekly `audit`)
- **브로커 대사 미완(PIVOT-6)**: 7/15 이후 방치된 보유(피에스케이 5주·S-Oil 4주)+조건주문 — Phase 0에서 실측 대사 전까지 브로커 상태 추측 금지
- 국내 EOD는 +1영업일 공개. 정책 파라미터(부록 B)는 운영자 결재 전 임의 기본값 금지
- 지시 인코딩 시 **결과를 지시자 언어로 에코백**(7/14 규칙)

### next
- `history/work/CURRENT.md`의 "진행 중"·"다음 후보" 인용 (이 프로젝트의 NEXT.md 역할)
- 큰 줄기: v0.3 §10 로드맵 — Phase 0(환경 복구·브로커 대사) → 1(밸류에이션·백필) → 2(온도계·화이트리스트 결재) → 3(스크리너·페이퍼) → 4(DCA 집행)

## end

### summary
- `git diff --stat` + 이번 세션 산출·검증 요약
- **검증 게이트**: 코드 변경 시 `poetry run pytest -q` + `poetry run mypy src tests` 통과 확인 — 실패 상태로 세션을 닫지 않는다(환경 미프로비저닝 등 불가피하면 운영자 합의 후 미실행 명시 박제)

### handoff
- 세션 상세 → `history/work/archive/YYYY-MM-DD-<slug>.md` / `CURRENT.md`는 한 줄+링크 롤오버·최종 갱신일 (**NEXT.md·histories/ 신규 생성 금지 — 기존 컨벤션 매핑**)
- 마일스톤 완결 → `docs/PROGRESS.md`(coarse), 결정·모호함 → `docs/OPEN_QUESTIONS.md`, 아이디어 → `docs/PROPOSALS.md`
- **지시 에코백(7/14 규칙)**: 운영자 지시를 좁혀/보수화해 인코딩했으면 종료 보고에 **결과를 지시자 언어로** 명시. 조용한 격하 금지
- 상태 스냅샷 한 줄: 동결 유지 여부 + 현재 Phase + 미커밋 잔여

### commit
- 논리 단위 커밋(feat/fix/docs/ops), 비밀값(.env) 절대 미포함 확인
- 푸시는 운영자 요청·세션 합의 시. main 직접 커밋 금지 — 작업 브랜치에서
