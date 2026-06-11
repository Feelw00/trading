# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-06-11 (KST)

## 진행 중
- _(없음 — 다음 세션 진입 시 채움)_

## 운영 상태 (상시 확인)
- **cron 16슬롯 자동 가동 중** (6/10 저녁~, 6/11 macro-am/pm 슬롯 제거 — 거시 수집은 report-am/pm 라운드에 내장): fire-and-forget(setsid)+로컬 트리거(qwen2.5:3b)+잡별 로그(`.runtime/logs/cron/`). 보고·알림은 Telegram HTML.
- 세션 진입 시 점검: `poetry run python ops/openclaw/drill.py --audit` (전일 사이클 PASS/WARN/FAIL) + 라운드 실패 P1 알림 수신 여부.

## 최근 완료
- 2026-06-11 — **P-5 DiscussPack: 토론 컨텍스트 사전 조립+캐싱(discuss 개편 — 수급 포지션·뉴스 R2→R4 검증·캐시 버전)** → [PROPOSALS P-5](../../docs/PROPOSALS.md)
- 2026-06-11 — **수급 해소: KIS 투자자매매동향 TR 확정+flows 파이프라인(FactPack R3 grounding) + 거시 수집 report 라운드 내장(16슬롯) + boot 자동수집화** → [archive](archive/2026-06-11-kis-investor-flows.md)
- 2026-06-11 — **R6 보고 가독성 재설계(결정 우선) + Telegram HTML 서식 통일(보고·P0·P1)** → [archive](archive/2026-06-11-telegram-format.md)
- 2026-06-11 — **첫 자동 사이클 점검 + drill.py + 트리거 아키텍처 3단 진화(절대경로→프롬프트→fire-and-forget/setsid+로컬모델). pm 풀 드릴 10잡 검증** → [archive](archive/2026-06-11-first-auto-cycle-audit.md)
- 2026-06-10 — **M3 완결(alerts·R5·R5.5·R6·R7) + cron 18개 enable** → PROGRESS M3 블록 + archive 5건
- 2026-06-10 — **M2 마무리 슬라이스 4종 + R4 실검증 → 결함 2건 수정·재검증** → [archive](archive/2026-06-10-m2-wrapup-slices.md)
- 2026-06-10 — **M2 GitOps 부트스트랩 · 격리 OpenClaw · R0~R4 실거동 검증** → [archive](archive/2026-06-10-m2-bootstrap-validation.md)
- 2026-06-09 — **P-4 뉴스 촉매 파이프라인 + 보조 슬라이스** → [archive](archive/2026-06-09-news-catalyst-pipeline.md)
- 2026-06-08 — **디스커버리 파이프라인 + 데이터소스 + boot/수집 스킬** → [archive](archive/2026-06-08-discovery-pipeline.md)
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이** → [archive](archive/2026-06-08-m1-skeleton.md)

## 다음 후보 (우선순위)

마일스톤 큰 줄기는 `docs/PROGRESS.md` "미해결 / 다음" 참조. 작업 단위 후보:

**다음 세션 첫 작업**
1. `drill.py --audit` — 새 아키텍처(fire-and-forget) 첫 무인 사이클(6/11 pm~6/12 am) 사후 점검
2. 첫 R7 유효 채점 조건 확인 — 시세 일자 누적(6/12이면 6일치) + 논제 horizon 도래분

**M4 / Phase 1 잔여** (순서대로)
3. 이벤트 감시기(`watch/`) → P0 발화 (서킷브레이커·환율 임계·바이너리 전이·보유 공시) — heartbeat 배선 포함
4. 리플레이 회귀 테스트 (6/2~6/8 주간, M4 프롬프트 §2)
5. 승인 전이(draft→approved) 운영자 도구 — R5가 실제 플레이북을 내기 시작하면 필요해짐
6. KIS 잔고·체결 어댑터 — 저녁 보고 집행·포지션 결측 해소

**빠른 슬라이스 (틈새)**
- **DiscussPack에 주요 공시 원문 요약 포함** — 공정공시·계약 공시 등은 DART `document.xml`로 원문을 받아 팩에 발췌(공시가 뉴스보다 상위 근거 — 네이버 AI 팩토리 사례). 임원·주요주주 보고는 elestock 계열 정형 API 검토.
- R6 보고 `_macro_lines`의 `GROUP BY name` 버그 — 같은 지표 다중 수집분 중 임의 행 선택(6/09 KOSPI가 6/11 보고에 나갈 수 있음) → 지표별 최신 as_of 행 선택으로 수정
- CAL-1: 2026년 음력·대체공휴일 KRX 공지 확인 → `krx_holidays.json`
- R1 일반 게이트 운영 배선 — landing→FactRecord 변환 계층

**외부 의존 해소** (병행 가능)
- NXT 프리·애프터 데이터 소스 조사 → SEL-1·R7-1 해소 (KRX 수급은 6/11 KIS TR로 해소됨)
- KIS 청산 주문(조건부) 인터페이스 스펙

**빠른 슬라이스 추가**
- R6 저녁 보고 수급 섹션을 flows.sqlite로 채우기(현재 "KRX 미해결" 결측 문구 잔존)
