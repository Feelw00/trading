# 2026-06-10 — M3 첫 슬라이스: alerts 어댑터 (P0/P1/P2 → Telegram)

## 맥락
- 개인 openclaw(클로, 트렌드·브리핑) 인스턴스 종료 — trading 인스턴스만 가동(운영자 지시).
- 트레이딩 결과의 Telegram 수신 경로 결정: openclaw 채널 enable(폴링, 충돌 리스크) 대신
  **Bot API `sendMessage` 직접 호출(폴링 없음)** — 설계서·PROGRESS 기존 결정 그대로. 클로 부활과도 무충돌.

## 산출 (`src/trading/alerts/` — 빈 스텁 → 구현)
- **model.py**: `Severity`(P0/P1/P2) + `Alert`(pydantic frozen). **4요소 페이로드 강제(§8)** —
  P0/P1은 `action`/`deadline` 비면 ValidationError(행동 매핑 없는 알림 생성 차단),
  P2는 역으로 action/deadline 보유 시 거부(행동 필요한 알림의 P2 강등 은닉 차단). naive datetime 거부.
- **channels.py**: `Channel` 프로토콜 + `TelegramChannel`(sendMessage POST, opener 주입, 4096자 절단,
  **토큰 스크럽** — 에러에 비노출) + `LogChannel` 폴백 + `channel_from_env`(.env 미설정 시 로그 강등).
- **store.py**: `AlertStore`(`data/alerts.sqlite`, append-only) — `alerts` + `dispatches`(발송 기록도
  행 추가로만). P1 미발송 = dispatches 부재 조건.
- **dispatch.py**: `AlertDispatcher` — P0 즉시 발송(실패 시 로그 폴백, 채널 박제) / P1 적재 →
  `flush_digest`(점심·마감 묶음, 실패 시 다음 flush 재시도, 중복 없음) / P2 적재만(R6 보고 전용).
  주문 초안 생성 코드 없음(§8).
- **배선**: `trading.run alerts-digest` 라운드 + cron `digest-noon`(12:30)·`digest-close`(15:40)
  선언 등록(sync --apply, 13개 잡) 후 **기존 정책대로 일괄 disabled**.

## 검증
- pytest **223 passed**(+16: 4요소 강제 5, 채널 4, 라우팅 6, 스토어 1), mypy **0 issues (82 files)**.
- **실발송 확인**: P0 테스트 알림 1건 → `sent:telegram` (실제 수신 확인은 운영자 단말).
- sync 정합성: 13개 잡 정상화, 전부 disabled.

## 남은 것 (M3 다음 슬라이스)
- 이벤트 감시기(`watch/`) → P0 발화 연결(서킷브레이커·환율 임계·바이너리 전이·보유 공시).
- R6 보고가 `AlertStore.recent()`로 P2 포함 렌더.
- cron 일괄 enable 시점: R5/R5.5/R6 채워진 뒤 운영자 결정.
