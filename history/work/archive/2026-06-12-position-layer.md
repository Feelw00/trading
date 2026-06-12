# 2026-06-12 — 포지션 관리 레이어 (P-8): 보유 테이블 + 계획 스냅샷 + 정리 점검

운영자 요청: "보유 종목 테이블 + 트리거·계획을 분석 문서 그대로 저장 + boot/arm-check/스킬에서
보유 조회·정리 필요 확인". 설계서 §8 "보유 포지션의 무효화 조건 잔여 거리"(저녁 보고 항목)의
구현이기도 함 — 기존엔 "KIS 잔고 어댑터 미구현" 결측으로 방치.

## 산출

- **`contracts/position.py`**: PositionRecord — 종목·수량·평단 + **계획 스냅샷**(hypothesis·
  trigger_text·invalidation_text·stop_level·time_stop_days·confidence·**plan_doc 전문**·source_ref)
  + status(open/closed)·close_reason. discuss 조건문을 그대로 박제.
- **`journal/positions.py`**: PositionStore(`data/positions.sqlite`, append-only — 정리=새 version).
  기본 경로는 호출 시점 해석(테스트 격리 가능).
- **`position_check.py`(순수 코드)**: 현재가(KIS 실시간→EOD 폴백, as_of 표기) 대비 손익%·
  스탑 잔여 거리%·시간손절 잔여 거래일(market_calendar) → **[정리 검토] 플래그**(스탑 이탈/도래).
  자유문 무효화는 코드가 평가하지 않고 표시만(해석=스킬, 판단=운영자 — 절대금지 #2).
- **`positions.py` CLI**: `add`(계획 스냅샷·--plan-file 박제, 출구 없는 등록은 경고)·
  list(점검)·`close --reason`(사유 박제 — R7 준수율 입력).
- **노출 3곳**: arm-check "보유 포지션 점검" 섹션 / 저녁 보고 "보유 포지션 — 무효화 잔여 거리"
  섹션(§8 결측 해소, 결측 노트는 "집행 편차(잔고 대사)"로 축소) / `/positions` 스킬(등록·점검·정리,
  무효화 자유문을 최신 검증 이벤트와 대조 해설). work-boot에 2b 보유 점검 추가.

## 검증

- pytest **342 passed**, mypy strict **0 (81 files)**.
- 테스트: 계약 검증(qty/price/confidence 거부), append-only 전이, 실시간 손익·스탑 거리,
  스탑 이탈/시간손절 도래 → [정리 검토], KIS 장애 시 EOD 폴백, CLI add/list/close·출구 경고.
- 실데이터 스모크: 테스 5주 @196,300 가상 등록 → 실시간 186,700(−4.9%)·스탑 여유 +16.7%·
  시간손절 잔여 10거래일 정확 산출, arm-check·저녁 보고 섹션 표시 확인 후 테스트 DB 제거.
- **테스트 격리**: conftest autouse로 DEFAULT_POSITIONS_DB를 tmp로 패치 — 테스트가 운영
  data/positions.sqlite를 만들지 않음(2026-06-11 AlertStore 사고와 동일 원칙).

## 미해소 (유지)

- KIS 잔고·체결 어댑터 — 수동 등록과 실계좌 대사(체결 자동 반영)는 후속.
