# 2026-07-13 — 토스 Open API 조사 + 섹터 기준 KRX 공식 업종 전환 (SECT-1)

## 맥락
- 운영자가 토스증권 Open API 신청(사전 롤아웃) → 조사 요청 → KIS 대비 검토 →
  "자체 태깅보다 금융사 분류 신뢰" 판단으로 **섹터 기준 전면 교체 결정**(AskUserQuestion로 확정).
- 부수: 14:40 마감 리마인더 미수신 문의 → 원인 진단(버그 아님) → P-10 제안 등록.

## 산출
1. **토스증권 Open API 전수 조사** (스펙 JSON 27 엔드포인트 직접 검증, 추측 없음):
   - 조건주문 `SINGLE/OCO/OTO`(triggerPrice→LIMIT/MARKET, expireDate, 멱등키) — §6 청산 요구 정합.
     🔴 "증권사 조건부 주문 API" 첫 실물 스펙. OPEN_QUESTIONS 외부 의존 섹션에 조사 노트.
   - KRX+NXT 통합 장 캘린더(프리마켓 세션) — SEL-1 부분 해소 후보(1분봉 프리마켓 포함 여부 키 수령 후 실측).
   - 없음: 웹소켓(REST only)·종목별 수급·**업종/섹터 필드(0회)**·체결강도(체결에 방향 구분 없음).
   - 결론: KIS 대체가 아니라 보완(주문·계좌·NXT·200종목 배치 시세·장 캘린더). 메모리 `toss-securities-open-api` 저장.
2. **SECT-1 — 섹터 KRX 공식 업종 전환** (OPEN_QUESTIONS 🟢 등록):
   - `kis.quote_price()` 신설(TR FHKST01010100, `bstp_kor_isnm` 관측 확정: 005930→'전기·전자').
   - `sectors.classify_krx()` → `kis-bstp-v1` 소스(원문 그대로·정규화 없음, 실패는 행 미기록=재시도).
   - `SECTOR_SOURCES` 최우선 배치(first-wins) — 기존 큐레이션·KSIC·LLM 폴백은 갭 필로 강등.
   - `sectors.main()`에 KRX 단계 삽입 → daily-eod 체인이 매일 신규분 자동 태깅.
   - 첫 실행: 게이트 232종목 중 **231 태깅**(21개 업종, 1종목 스킵→재시도). 스크리너·스윙(도메인 축) KRX 업종으로 동작 확인.
   - 잔여 🟡: FactRecord.sector enum·R2 `sector:` 라벨은 taxonomy 유지(계약 §4 — SECT-1 잔여 참조).
3. **P-10 제안 등록**(PROPOSALS): 감시기 기동 알림(무소식 vs 무대상 구분) + 승인 누락 리마인더.
   진단: 7/13 arm-watch 231패스 정상 가동, 리마인더 침묵은 approved 풀 0건(7/12 draft 4건 미승인) — 설계된 동작.

## 검증
- pytest 전체 통과 + mypy strict 0 issues (128 files). 신규 테스트: classify_krx 태깅/스킵/재시도, KRX first-wins 우선순위.
- 드라이브바이: test_dispatch.py mypy 기존 에러 1건 수정(lambda append→명명 함수).
- 실거동: 라이브 태깅 231종목, 스크리너·스윙 출력 KRX 업종 확인.

## 후속
- 토스 키 수령 시: IP 등록 → NXT 프리마켓 1분봉 실측(SEL-1) → 조건주문 어댑터 인터페이스(지정가만).
- SECT-1 잔여: 계약 층(Sector enum·R2 라벨) 전환 여부 운영하며 결정.
- P-10 채택 여부 운영자 결정 대기.
- 미분류 잔여 1종목은 다음 daily-eod에서 자동 재시도.
