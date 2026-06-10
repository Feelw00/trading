# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-06-10 (KST)

## 진행 중
- _(없음 — 다음 세션 진입 시 채움)_

## 최근 완료
- 2026-06-10 — **M2 마무리 슬라이스 4종: R1 일반 게이트 · market_calendar 가드 · score-news 전수(131 이벤트) · R4 threshold 캘리브레이션(0.4/0.6)** → [archive](archive/2026-06-10-m2-wrapup-slices.md)
- 2026-06-10 — **M2 GitOps 부트스트랩 · 격리 OpenClaw · R0~R4 실거동 검증** → [archive](archive/2026-06-10-m2-bootstrap-validation.md)
- 2026-06-09 — **P-4 뉴스 촉매 파이프라인 + 보조 슬라이스(섹터 태깅·factpack·discuss·뉴스 단일 DB·디스패치·스크리너 튜닝)** → [archive](archive/2026-06-09-news-catalyst-pipeline.md)
- 2026-06-08 — **디스커버리 파이프라인 + 데이터소스 + boot/수집 스킬** → [archive](archive/2026-06-08-discovery-pipeline.md)
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이** → [archive](archive/2026-06-08-m1-skeleton.md)

## 다음 후보 (우선순위)

마일스톤 큰 줄기는 `docs/PROGRESS.md` "미해결 / 다음" 참조. 작업 단위 후보:

**M2 잔여** (빠른 슬라이스)
1. R4 실검증 실행(verify-catalysts) — 선별 18건 × 3렌즈 ≈ 54 claude 호출, 비용 승인 후
2. CAL-1: 2026년 음력·대체공휴일 KRX 공지 확인 → `krx_holidays.json` 채우기
3. R1 일반 게이트 운영 배선 — landing→FactRecord 변환 계층 후 거시·시세 적용

**M3 / Phase 1** (큰 작업, 순서대로)
5. `alerts/` P0/P1/P2 Telegram 어댑터 (cron 잡 자동 실행 전 필수)
6. R5 합성·플레이북·OrderDraft
7. R5.5 아침 선택기(흐름 변수만)
8. R6 보고 (모닝/저녁)
9. R7 평가·캘리브레이션

**외부 의존 해소** (병행 가능)
- KRX/NXT 데이터 소스 스펙 조사 → R3 supply 페르소나 grounding 동시 해소
- KIS 청산 주문(조건부) 인터페이스 스펙
