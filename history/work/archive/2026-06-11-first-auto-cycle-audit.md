# 2026-06-11 — 첫 자동 운영 사이클 점검 (6/10 20:30 ~ 6/11 08:50)

## 표면 결과: 18슬롯 전부 status=ok
- 6/10: synth-pm(20:33) → report-pm(21:00, 저녁 결재 2,959자 발송).
- 6/11: macro-am(06:10, 11건 verified) → news-am(06:20) → score-am(06:30) → verify-am(06:45) → **report-am(06:50, 모닝 브리핑 1,511자 발송 — 거시 11종 as_of 병기·체크리스트 7항목·비거래 결론)** → reason-am(06:55) → daily-eod(08:00) → select-am(08:50).

## 세션 검증에서 발견된 결함 (status ok ≠ 정상 동작)
1. **exec 비결정(SCHED-2 위반)**: `python -m trading.run …`이 에이전트 워크스페이스 cwd + asdf 전역 python으로 1차 실패(ModuleNotFoundError) → **LLM 트리거가 ls·cd로 repo를 찾아 임기응변 재실행**해 ok 처리. 6/10 enable 검증(digest 30초 ok)도 같은 패턴이었음.
2. **상대경로 data/의 조용한 실패**: 임기응변 실행도 cwd가 어긋나면 `data/market.sqlite`가 **빈 DB로 생성**돼 news-am이 "스크리너 후보 없음" 스킵(오늘 뉴스 0건) — 에러가 아니라 스킵이라 더 위험. 워크스페이스에 stray 빈 DB 2개 생성됨(전 테이블 0행 확인 후 삭제).
3. **daily-eod 08:00 공백**: data.go.kr T-1 EOD가 08:00 미공개(6/11 09시에도 없음, 전일 14:49엔 있음) — 잡은 ok지만 신규 0행.

## 수정 (커밋 6179770)
- `sync.py exec_command()`: `cd <repo> && .venv/bin/python -m trading.run <round>` — sync 시점 기기별 절대경로 렌더(GitOps 이식성 유지). `matches_manifest`가 message·delivery 비교(구 포맷 하드코딩 멱등성 결함 동시 수정). 18잡 재등록.
- daily-eod 08:00 → **16:05** (pm 라운드 16:20~가 최신 후보 사용).
- 검증: digest-noon 재트리거 — **1차 exec 즉시 성공, 임기응변 0, 30초→4초.**
- 오늘 뉴스 수동 백필(360건 적재).

## 교훈
- **cron status=ok는 신뢰 지표가 아니다** — 세션 로그의 1차 exec 결과까지 봐야 한다. LLM 트리거의 "복구 능력"이 결정론 결함을 가린다.

---

# 후속: drill.py + pm 풀 드릴 → 트리거 아키텍처 3단 진화 (같은 날 오전)

## drill.py (ops/openclaw/) — 슬롯 대기 없는 즉시 테스트 도구
- `--audit`(최근 런 검증) / `<잡…>` / `--cycle am|pm|all`. 판정 = status ok + **1차 exec 성공 + 임기응변·월권 없음** + (트리거 모드) **잡 로그로 라운드 완주 추적**.

## pm 풀 드릴(10잡)이 연쇄로 들춘 결함과 진화
1. **v1 절대경로 명령**(아침 수정): 짧은 잡은 해결. 그러나 2분+ 잡은 openclaw exec가 백그라운드 전환(YIELD_MS 클램프 120s — 회피 불가) → 트리거 LLM이 poll 대기 대신 **프로세스 kill(verify-pm R4 사망)·소스 열람·DB 자체 쿼리**(월권).
2. **v2 프롬프트 강화**("poll만, kill 금지"): poll 턴 누적으로 **무료 티어 모델 rate limit** → 트리거 자체가 429 전멸. LLM babysitting은 프롬프트로 못 고친다.
3. **v3 fire-and-forget(최종)**: `setsid -f sh -c '… >> .runtime/logs/cron/<잡>.log' && echo launched` — 트리거 턴 수 초 종료. nohup만으론 부족(openclaw가 **턴 종료 시 프로세스 그룹 정리** — digest 2초 생존 vs verify 10분+ 사망으로 격리 확인) → setsid 세션 분리.
   - 실패 가시성 이관: `trading.run`이 예외·비정상 rc 시 **P1 알림**(가드 스킵 rc=3 제외) + 잡별 로그.
   - 트리거 모델 **로컬 핀**(qwen2.5:3b, bootstrap이 pull) — 클라우드 쿼터를 크리티컬 패스에서 제거.

## 최종 검증
- digest-noon: PASS(트리거 58s, 라운드 완주 추적 "P1 다이제스트: 1건 발송").
- verify-pm: PASS — R4가 트리거 턴 종료 후 분리 생존·완주. 선별 0은 정당(미검증 이벤트 최대 강도 0.35/0.5로 임계 미달).
- pm 드릴 데이터 실적: 뉴스 564 · 이벤트 71 · 논제 36 · 저녁 보고 1,450자 실발송 · synth-pm 장중 가드 정상 거부.

## 남은 관전
- 오늘 16:05~21:00 실슬롯 = v3 아키텍처의 첫 무인 사이클. 익일 `drill.py --audit`로 점검.
