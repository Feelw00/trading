# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-06-10 (KST)

## 진행 중
- _(없음 — 다음 세션 진입 시 채움)_

## 최근 완료
- 2026-06-10 — **M3 R7 평가: 결정론 채점기(적중률·캘리브레이션·R4 정확도·레짐 프록시) + 해석 claude -p(자동 적용 금지) — M3 완결, R0~R7 전 라운드 배선** → [archive](archive/2026-06-10-m3-r7-evaluation.md)
- 2026-06-10 — **M3 R6 보고: Jinja2 모닝/저녁 렌더 + 분량 가드(실패+P1, 축약 없음) + Telegram 실발송 검증(1,420자)** → [archive](archive/2026-06-10-m3-r6-reports.md)
- 2026-06-10 — **M3 R5.5 선택기: 순수 함수 평가 엔진 + approved-only arm(P1 알림) + 장중·휴장 가드, SEL-1 등록** → [archive](archive/2026-06-10-m3-r55-selector.md)
- 2026-06-10 — **M3 R5 합성: 흐름변수 화이트리스트 + 규율 코드 강제(3트랜치·상한·손절2종) + PlaybookStore + 장중 가드 배선, 실거동 "비거래" 검증** → [archive](archive/2026-06-10-m3-r5-synthesis.md)
- 2026-06-10 — **M3 alerts 어댑터: 4요소 강제 + Telegram sendMessage 직접(폴링 없음) + P0즉시/P1다이제스트/P2보고, 실발송 검증** → [archive](archive/2026-06-10-m3-alerts-adapter.md)
- 2026-06-10 — **M2 마무리 슬라이스 4종 + R4 실검증 → 결함 2건 수정·재검증(인프라 실패 8→0, confirmed 0은 백필 데이터의 정답)** → [archive](archive/2026-06-10-m2-wrapup-slices.md)
- 2026-06-10 — **M2 GitOps 부트스트랩 · 격리 OpenClaw · R0~R4 실거동 검증** → [archive](archive/2026-06-10-m2-bootstrap-validation.md)
- 2026-06-09 — **P-4 뉴스 촉매 파이프라인 + 보조 슬라이스(섹터 태깅·factpack·discuss·뉴스 단일 DB·디스패치·스크리너 튜닝)** → [archive](archive/2026-06-09-news-catalyst-pipeline.md)
- 2026-06-08 — **디스커버리 파이프라인 + 데이터소스 + boot/수집 스킬** → [archive](archive/2026-06-08-discovery-pipeline.md)
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이** → [archive](archive/2026-06-08-m1-skeleton.md)

## 다음 후보 (우선순위)

마일스톤 큰 줄기는 `docs/PROGRESS.md` "미해결 / 다음" 참조. 작업 단위 후보:

**M2 잔여** (빠른 슬라이스)
1. CAL-1: 2026년 음력·대체공휴일 KRX 공지 확인 → `krx_holidays.json` 채우기
2. R1 일반 게이트 운영 배선 — landing→FactRecord 변환 계층 후 거시·시세 적용
3. R4 실측 생존률 측정 — 운영 슬롯(당일 뉴스) 가동 후(백필로는 측정 불가, 6/10 결론)

**M3 완료** — alerts·R5·R5.5·R6·R7 전부 (2026-06-10). cron 18개 등록, 전부 disabled.

**운영 가동 중 (2026-06-10 저녁부터)**
- cron 18개 **전부 enable** — 트리거 모델 m2.5 교체 + --no-deliver 수정 후 체인 검증(digest-noon ok).
- 첫 자동 슬롯: 당일 20:30 synth-pm → 21:00 report-pm, 익일 06:10부터 풀 파이프라인.

**M4 (다음 후보)**
1. 이벤트 감시기(`watch/`) → P0 발화 (서킷브레이커·환율 임계·바이너리 전이·보유 공시)
2. 리플레이 회귀 테스트 (6/2~6/8 주간, M4 프롬프트)
3. 승인 전이(draft→approved) 운영자 도구
4. 첫 자동 운영일 모니터링 — 실패 잡 점검(cron runs), claude -p 비용 관측

**외부 의존 해소** (병행 가능)
- KRX/NXT 데이터 소스 스펙 조사 → R3 supply 페르소나 grounding 동시 해소
- KIS 청산 주문(조건부) 인터페이스 스펙
