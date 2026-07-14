# 2026-07-14 — live 전환 사고 대응 (운영자 "dry run 다 빼")

## 사고 경위 (오전, D1)
1. **지시 인코딩 실패**: 7/13 21:48 운영자 지시("내일 … 소액으로라도 진입하도록")를 세션이
   `EXEC_TEST_ENTRY` dry-run 전용 계측으로 격하 — 충돌(기존 dry-run 5거래일 결정)을 되묻지 않음.
   트랜스크립트 대조로 확정. 09:54 뉴파워프라즈마 실발동(9,960)이 dry-run 기록으로만 남음.
2. **env 스냅샷 사고**: 게이트웨이 tmux(7/13 02:05 기동)가 이후 .env 갱신(토스 키·EXEC_*)을
   모름 → 09:00 cron 감시기가 토스 미설정·레짐 UNKNOWN으로 기동. 09:54 재기동으로 복구.
3. 운영자 강한 항의 → **"dry run 다 빼" 결정 → 10:05 EXEC_MODE=live 전환**(검증창 포기).

## 조치 (전부 당일)
- live 전환: .env(EXEC_MODE=live, EXEC_TEST_ENTRY 제거) + 감시기·게이트웨이 재기동(env 상속 확인),
  매수가능금액 500만 실조회 확인.
- **유령 포지션 정리**: dry-run이 박제한 뉴파워 50주(pos.20260714.144960) close —
  방치 시 경고 레벨(9,200)에서 미보유 주식 실매도 시도 위험.
- **dedup mode 분리**: `ExecStore.has`/`new_orders_today`에 mode 필터 — live는 live 기록만
  (dry-run order_intent가 live 재진입 차단하던 것 해소). arm_watch 풀 분모도 동일.
- **잔여 R:R 가드 신설**(운영자 지적 "9,999에 사서 10,000에 파냐"): 진입가 기준
  최종타깃 보상/손절 위험 < `EXEC_MIN_RR`(기본 1.0)이면 스킵. 아침 뉴파워 진입(R:R 0.04)도
  이 가드였으면 차단됐을 나쁜 진입.
- **R5 2단 사다리 기본 강제**(운영자 시나리오: 익절1 절반 실현→본전 상향→최종 타깃):
  활성 풀 7건 중 5건이 단일 타깃이라 사다리 엔진(manage_exits)이 밟을 계단이 없던 것 —
  프롬프트에 2단 기본·합=100·마지막 레벨=전량 명시. 21:05 산출부터 적용.
- boot 실시간 싱크(별도 archive: 2026-07-14-boot-live-sync.md).

## 검증
- pytest 전체 통과(신규: mode 필터 dedup/카운터, R:R 가드 2케이스) + mypy strict 0 (134 files).
- 실거동: 감시기 pid·하트비트·EXEC_MODE=live env 확인, 유령 정리 후 보유 0건,
  풀 실시간 대조(유효 4: 한국콜마·브이엠·피에스케이·티에스이 / 소진 테스·뉴파워 / 붕괴 데이타솔루션).

## 잔여 (다음 세션)
- ExecStore 나머지 쿼리(committed_krw·cash_skips·rotations·pending_*·latest_bracket·open_symbols)
  mode 일관성 — 돈 경로 급소는 해소, 나머지는 정합성 정리.
- 단일 타깃 pct(예: 60%)가 브래킷 전량으로 조용히 무시되는 스펙 불일치 — 계약 검증 또는 집행 반영.
- `/approve` 스킬 문구 구세계(수동 결재) — veto 중심 개정.
- `start-gateway.sh` ready 판정 grep이 ANSI 색코드에 깨짐(기동은 정상인데 실패 보고).
- INFRA: sync가 .env mtime vs 게이트웨이 기동 시각 비교 경고(재발 방지 ②의 자동화).
- **live 첫 주 = 운영 겸 검증**: 첫 실주문 전 경로(주문→체결→OCO→사다리) exec.sqlite 밀착 확인.
