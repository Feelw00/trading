---
name: work-boot
description: 부팅 요약 — 스케줄러(cron 16슬롯)가 축적한 DB를 읽기 전용으로 요약한다: 감시 풀(진입/손절/익절)·보유 포지션·스크리너 후보·history. 수집·사이클 점검은 하지 않는다(거시 라이브는 collect-macro 담당). "/boot", "부팅", "세션 시작" 시.
---

# work-boot — 부팅 요약 (읽기 전용)

거시 라이브 조회는 `collect-macro`가 담당. 여기선 **스케줄러(cron 16슬롯)가 축적한 DB를 읽어 요약만** 한다.
수집·신선도 자동 보정·사이클 점검(drill --audit)은 부팅에서 하지 않는다 — 필요하면 `/collect`·`/collect-news`·`drill.py --audit`를 수동 실행.

## 1. 오늘 감시 풀 — 진입/손절/익절 (스크리너보다 먼저)
- **오늘의 실제 대상은 approved 풀이다(EXEC-1 자동 승인 체제)**: `poetry run python -m trading.approve --pool`
  — 종목명·진입 발동 조건·손절/경고/익절 사다리·만료 다이제스트.
- 진입 발동 여부의 실시간 판단이 필요하면 `/arm-check`(별도 스킬)로.

## 2. 보유 포지션 (P-8)
- `poetry run python -m trading.positions` — open 포지션의 손익·스탑 잔여 거리·시간손절 잔여.
- **[정리 검토] 플래그가 있으면 부팅 보고 맨 앞에 올린다**(스탑 이탈·시간손절 도래 — 운영자 판단 필요).
  깊은 점검·무효화 대조는 `/positions` 스킬로. 보유 없음이면 한 줄로 생략.

## 3. 스크리너 후보 (EOD 참고용)
- `poetry run python -m trading.screener` — DB에 축적된 최신 EOD 기준 상위 후보 + 섹터 태그(재수집 없음).
- **stale 라벨(필수)**: `as_of`가 최근 거래일보다 이전(공개대기 — 아침 부팅에선 항상 그렇다)이면
  "**전일 미반영(as_of=…) — 참고용**" 라벨. 레짐이 CAUTION/RISK_OFF이거나 전일 지수 급변이면
  **이 순위는 사실상 무효**라고 명시(7/14 오독: 폭락 다음날 아침, 폭락 전 기준 순위가 "오늘 후보"로 나갔다).
- `as_of`가 비정상적으로 뒤처져 보이면(갭 의심) 수집·우회하지 말고 ⚠️ 한 줄 보고 + `/collect` 수동 실행 안내.

## 4. 읽기 (history + 마일스톤)
- **자동 읽기**: `history/work/CURRENT.md`(진행 중·다음 후보), `docs/PROGRESS.md`(마일스톤 원장 — coarse).
- **가벼운 스캔**: `history/trading/INDEX.md`(최근 N), `docs/OPEN_QUESTIONS.md`(🔴 항목만).
- **온디맨드**: `history/work/archive/<slug>.md`(CURRENT 링크 따라 필요 시), OPEN_QUESTIONS 🟢 결정 본문.
- CLAUDE.md·MEMORY.md는 자동 주입 — 활성화만.

## 5. 기본 동작 활성화 (체크리스트)
- 보수·반-아첨 / 마일스톤 AC 게이트 / 종료 시 CURRENT 갱신.
- 시장가 주문 금지 · 비밀값 하드코딩 금지 · KST tz-aware · 외부 엔드포인트 추측 금지.
- 수집 하네스(COLLECT-3): 승인 소스 어댑터만, 독자 웹서치 금지.

## 6. 마지막 / 다음 작업
- CURRENT "진행 중"·"최근 완료" + "다음 후보"에서 구체 행동.

## 출력
간결한 요약 보고: 감시 풀(진입/손절/익절) → 보유 포지션 → 스크리너(stale 라벨) → 다음 작업.
자동 주입 규칙 장황한 재설명 금지.
