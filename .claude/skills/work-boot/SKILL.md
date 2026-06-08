---
name: work-boot
description: 부팅 컨텍스트 — DB에서 데이터 신선도·스크리너 후보·history를 읽고, 미수집이면 알림·제안한다. 거시 라이브 조회는 collect-macro 담당. "/boot", "부팅", "세션 시작" 시.
---

# work-boot — 부팅 컨텍스트 (DB 읽기 중심)

거시 라이브 조회는 `collect-macro`가 담당. 여기선 **DB와 history만 읽는다** — 무거운 수집(전종목·공시·뉴스) 금지.

## 1. 데이터 신선도 (콘솔 날짜 기준)
- boot가 확인한 **콘솔 날짜**를 기준으로 **최근 거래일**을 판단(주말·휴장 + 국내 EOD는 +1영업일 공개 고려).
- DB 최신 수집일 확인:
  `poetry run python -c "from trading.collectors.market import MarketStore as M; s=M(); print(s.latest_date()); s.close()"`
- DB 최신일이 최근 거래일보다 뒤처지면(영업일 기준 ~2일+ 갭) → **⚠️ 알림: '〈DB최신일〉까지만 수집됨, 〈최근거래일〉 미수집' + `/collect` 실행 제안.** 정상이면 'DB 최신: 〈날짜〉'.

## 2. 오늘 후보 (스크리너 — DB 위, 수집 없음)
- `poetry run python -m trading.screener` → 상위 후보 + 섹터 태그. **기존 DB로만 계산**(라이브 수집 안 함).

## 3. 읽기 (history)
- `history/work/CURRENT.md`, `history/trading/INDEX.md`(최근 N), 포인터: `docs/PROGRESS.md`·`docs/OPEN_QUESTIONS.md`(🔴).
- CLAUDE.md·MEMORY.md는 자동 주입 — 활성화만.

## 4. 기본 동작 활성화 (체크리스트)
- 보수·반-아첨 / 마일스톤 AC 게이트 / 종료 시 CURRENT 갱신.
- 시장가 주문 금지 · 비밀값 하드코딩 금지 · KST tz-aware · 외부 엔드포인트 추측 금지.
- 수집 하네스(COLLECT-3): 승인 소스 어댑터만, 독자 웹서치 금지.

## 5. 마지막 / 다음 작업
- CURRENT "진행 중"·"최근 완료" + "다음 후보"에서 구체 행동.

## 출력
간결한 4단 보고. 자동 주입 규칙 장황한 재설명 금지.
