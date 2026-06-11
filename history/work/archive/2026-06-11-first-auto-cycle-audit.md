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
- 다음 검증 포인트: 오늘 pm 사이클(16:05 eod → 16:20 news → 16:32 score → 16:45 verify → 16:55 reason → 20:30 synth → 21:00 report)이 **첫 완전 결정론 자동 사이클**.
