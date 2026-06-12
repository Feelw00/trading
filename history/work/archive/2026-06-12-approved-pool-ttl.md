# 2026-06-12 — approved 활성 풀 + TTL + 승인 도구 (P-7)

arm-check 설계 질문(운영자): "전일 저녁 항목만 확인 vs approved DB+TTL 한 번에 조회 vs 별도 리뷰 스킬?"
조사 중 **날짜 어긋남 버그**를 발견 — 운영자 결정 "2번(approved 풀 + time_stop_days TTL + 승인 도구)".

## 발견 (조사 결과)

1. **날짜 라벨 어긋남(버그)**: R5(synth-pm, 밤 20:30)는 `pb.<당일>` 생성, arm-check/R5.5(다음날 아침)는
   `playbooks_for_day(<다음날>)` 조회 → 하루 어긋나 전일 밤 승인분을 못 찾음. 6/12 아침 arm-check가
   `pb.20260611` 3종을 못 보고 "비거래" 출력(실증).
2. **다일 셋업 누락**: 3트랜치 flush(투매일 매집 50%)는 갭다운 날 대기 셋업 — 며칠 걸릴 수 있는데
   R5가 매일 갈아엎어 "어제 승인·오늘 미발동→모레 진입"이 사라짐. time_stop_days(10일)가 그 유효기간.
3. **승인 수단 부재**: draft→approved 전이 도구 없음(3종 다 draft 상태로 방치).

## 산출

- **`PlaybookStore.active_playbooks(now)`**: 날짜가 아니라 **status=approved + TTL(초안 거래일 +
  time_stop_days 거래일) 미경과**로 조회. 같은 (종목, 방향) 최신만(매일 R5 재생성 중복 제거).
  → 날짜 어긋남이 라벨 비의존으로 구조적 해소. `pending_drafts`(미승인 힌트)도 추가.
- **`MarketCalendar.add_trading_days(d, n)`**: TTL 만료일(거래일 단위) 계산.
- **`approve.py`**: draft→approved CLI(`--list` + id 명시, append-only 새 version). 자동 승인 없음.
- **arm-check 전환**: `active_playbooks` 조회. 만료일 표기 + "승인 대기 N건" 힌트(미승인 시 `/approve` 안내).
- **`/approve` 스킬**(신규) + arm-check SKILL.md 개정(활성 풀·다일 셋업 반영).

## TTL 정의 (운영자 결정)

`time_stop_days` 재사용 — "셋업이 유효한 거래일 수". 그 안에 arm 조건이 안 오면 만료(추격 금지).
진입 후 시간손절과 동일 파라미터를 진입 전 대기 한도로 공용(스키마 추가 없음).

## 검증

- pytest **325 passed**, mypy strict **0 issues (77 files)**.
- 핵심 테스트: `test_approved_pool_survives_to_next_day`(6/10 밤 승인 → 6/11 아침 조회 = 날짜 버그 해소),
  TTL 만료 제외, (종목,방향) dedup 최신만, draft 제외+미승인 힌트, 승인 전이·이미승인 skip,
  add_trading_days 주말·휴일 건너뜀.
- 실 DB 실증: 6/12 arm-check가 승인 전엔 "활성 풀 비어 있음 + 승인 대기 3건" 정확 출력
  (6/11 3종 승인은 운영자 결정이라 임의 전이 안 함).

## SEL-3 동반 해소 (이어서)

`select_playbooks.py`(R5.5 cron arm)도 `active_playbooks` + `flowsnap.build_snapshot`으로 통일.
날짜 라벨·흐름 소스 일원화(`load_snapshot` 제거, `flowsnap`에 inject_dir 파라미터 추가). 검증:
`test_runner_arms_across_date_label_mismatch`(6/11 승인 → 6/12 아침 arm), 실DB 6/12 실행 정상.
이제 arm-check(읽기)·select-am(자동 arm) 둘 다 다일 approved 풀로 동작. SEL-3 🟢.

## 승인을 아침 arm-check에 통합 (이어서, 운영자 결정 "아침 통합")

저녁 CLI 강제·id 타이핑 번거로움 피드백 → 승인 단계를 저녁→아침 arm-check로 이관. "검토 후 의식적
승인"(§6 충동 차단)은 유지, 우발적 마찰(타이핑·저녁 CLI)만 제거. 텔레그램 양방향 직접 승인은
채널 발신 전용 + openclaw 수신 인프라 부재로 보류(후속).
- `PlaybookStore.candidate_playbooks`(미승인 후보 풀, `_pool`로 active와 공통화).
- `arm_check`: 승인된 셋업 + 승인 후보 두 섹션, 후보도 "승인 시 발동" 미리보기 + `approve <id>` 동봉.
- arm-check 스킬: 후보 검토 → 승인 질문(자동 금지) → approve → 갱신. 저녁 보고 톤 "검토 후보"로.
- 검증: `test_candidate_shows_would_arm_preview`·`test_candidate_pool_drafts_only_ttl_ignored`,
  실DB 6/12 arm-check가 미승인 3종을 후보 섹션에 "승인 시 발동" 미리보기로 출력.

## 미해소 (잔존)

- SEL-2(`==true` 평가 불가)는 별개로 잔존(R5 프롬프트 또는 selector 변경, 별도 결정).
- 텔레그램 양방향 직접 승인(보고 채널에서 버튼/답장) — 인프라 신설 필요, 후속.
