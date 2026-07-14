# 2026-07-14 — boot를 어제(EXEC) 실시간 채널과 싱크 (운영자 지적)

## 문제 (D1 아침 부팅 보고)
- 부팅 백드롭이 KOSPI 7,475.94(7/10 EOD)를 표기 — **7/13 -8.9% 폭락 미반영**.
- 스크리너 순위(7/10 기준, 금호건설 등)를 "오늘 후보"로 표기 — 폭락 후 무효.
- 원인: 데이터는 정상(공공데이터 EOD가 +1영업일 공개 — 소스 직접 프로브로 20260713=0행 확인,
  스케줄러·DB 동기화 문제 아님). **결함은 boot 스킬이 어제 만든 실시간 채널
  (EXEC-7 `regime.py`·`live_backdrop_lines`, R5·R6엔 주입됨)을 배선하지 않은 것.**

## 산출
- `regime.py`에 CLI(`python -m trading.regime`) — 레짐 판정 + 실시간 KOSPI/KOSDAQ 줄 출력.
- `approve.py`에 `--pool` — 활성(approved+TTL) 풀 다이제스트(종목명·손절/경고/익절·만료).
  아침의 "오늘 후보" 실체는 스크리너가 아니라 이 풀(EXEC-1 자동 승인 체제).
- 스킬 배선: `boot.md`(보고 템플릿 — 시장 섹션에 실시간+레짐 우선, 감시 풀 섹션 신설,
  스크리너는 "EOD 참고용"+stale 라벨 필수) · `collect-macro`(절차 3에 regime 오버레이,
  코스피·코스닥 헤드라인은 실시간 우선·EOD는 as_of 병기) · `work-boot`(§2 감시 풀 우선 +
  stale 라벨 규칙).

## 검증
- pytest 전체 통과(신규 2: regime CLI UNKNOWN 출력·--pool 빈/1건 다이제스트) + mypy strict 0 (134 files).
- 실거동: `trading.regime` → NORMAL·KOSPI 6,773.64(-0.5% 실시간) / `--pool` → 7건(어젯밤 승인분) 정상.

## 메모
- 7/13 EOD는 09시 시점 소스 미공개 — 오늘 16:05 daily-eod가 적재하면 스크리너 자동 갱신.
