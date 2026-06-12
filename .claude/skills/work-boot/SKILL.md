---
name: work-boot
description: 부팅 컨텍스트 — DB에서 데이터 신선도·스크리너 후보·history를 읽고, 미수집이면 해당 수집 스킬(collect/collect-news)을 자동 실행한다. 거시 라이브 조회는 collect-macro 담당. "/boot", "부팅", "세션 시작" 시.
---

# work-boot — 부팅 컨텍스트 (신선도 확인 + 자동 수집)

거시 라이브 조회는 `collect-macro`가 담당. 여기선 DB와 history를 읽고 신선도를 확인하되, **시세·뉴스가 미수집이면 부팅 중 해당 수집 스킬을 자동 실행해 갭을 메운다**(질문·제안으로 멈추지 않는다). 공시 등 그 외 무거운 수집은 부팅에서 하지 않는다.

## 1. 데이터 신선도 (콘솔 날짜 기준)

### 1a. 전종목 EOD (시세 DB)
- boot가 확인한 **콘솔 날짜**를 기준으로 **최근 거래일**을 판단(주말·휴장 + 국내 EOD는 +1영업일 공개 고려).
- DB 최신 수집일 확인:
  `poetry run python -c "from trading.collectors.market import MarketStore as M; s=M(); print(s.latest_date()); s.close()"`
- DB 최신일이 최근 거래일보다 뒤처지면 → **`collect` 스킬을 즉시 실행**해 갭을 메운다(제안만 하고 멈추지 말 것). 수집 후 최신일 재확인, 보고에 '〈날짜〉 수집함' 명시. 정상이면 'DB 최신: 〈날짜〉'.
- 수집 실패(소스 장애·키 문제) 시 추측·우회 금지 — ⚠️ 실패 사유 그대로 보고(COLLECT-3).

### 1b. 뉴스 (오늘자 확인 — 없으면 자동 수집)
- 오늘(콘솔 날짜 D) 뉴스 적재 여부 확인(단일 영속 `data/news.sqlite`):
  `D=$(TZ=Asia/Seoul date +%F); [ -f data/news.sqlite ] && sqlite3 data/news.sqlite "SELECT COUNT(*), MAX(fetched_at) FROM news_items WHERE substr(fetched_at,1,10)='$D'" || echo "no-db"`
- 오늘자 행>0이면 '뉴스 최신: 〈D〉 (N건)'. **0건이거나 파일 없음(`no-db`)** → 소스 키 유무로 분기:
  - 키 있으면(`.env`의 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET` 또는 `SEARXNG_URL`) → **`collect-news` 스킬을 즉시 실행**(제안만 하고 멈추지 말 것). 수집 후 건수 재확인, 보고에 '수집함 (N건)' 명시.
  - 키 없으면 → 수집 불가. **'키 필요 — `.env`에 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`(국내) 또는 `SEARXNG_URL`(해외)'** 명시(흉내내기 금지, COLLECT-3).
- **이유:** 뉴스 없이 분석하면 fact pack의 뉴스 촉매가 빈다(가격·공시·재무는 grounded). 종목 분석(`discuss`)·전망 전에 이 신선도를 먼저 본다.

## 1c. 자동 사이클 점검 (cron 16슬롯 가동 중 — 2026-06-10~, 거시는 보고 라운드 내장)
- 전일~금일 자동 사이클 사후 검증(트리거 안 함, 판정만):
  `poetry run python ops/openclaw/drill.py --audit`
- **PASS만이면 한 줄 요약. WARN/FAIL 있으면 ⚠️ 잡명+사유 보고**(잡별 로그 `.runtime/logs/cron/<잡>.log` 참조). 라운드 실패 P1 알림(Telegram) 수신분과 대조.
- 게이트웨이 생존도 함께: tmux `openclaw-trading` 세션 + 포트 18790 (죽었으면 `bash ops/openclaw/start-gateway.sh` 제안).

## 2. 오늘 후보 (스크리너 — DB 위)
- `poetry run python -m trading.screener` → 상위 후보 + 섹터 태그. **1a 자동 수집이 있었으면 수집 완료 후에 실행**(최신 시세 기준 후보).

## 2b. 보유 포지션 점검 (P-8)
- `poetry run python -m trading.positions` — open 포지션의 손익·스탑 잔여 거리·시간손절 잔여.
- **[정리 검토] 플래그가 있으면 부팅 보고 맨 앞에 올린다**(스탑 이탈·시간손절 도래 — 운영자 판단 필요).
  깊은 점검·무효화 대조는 `/positions` 스킬로. 보유 없음이면 한 줄로 생략.

## 3. 읽기 (history + 마일스톤)
- **자동 읽기**: `history/work/CURRENT.md`(진행 중·다음 후보), `docs/PROGRESS.md`(마일스톤 원장 — coarse).
- **가벼운 스캔**: `history/trading/INDEX.md`(최근 N), `docs/OPEN_QUESTIONS.md`(🔴 항목만).
- **온디맨드**: `history/work/archive/<slug>.md`(CURRENT 링크 따라 필요 시), OPEN_QUESTIONS 🟢 결정 본문.
- CLAUDE.md·MEMORY.md는 자동 주입 — 활성화만.

## 4. 기본 동작 활성화 (체크리스트)
- 보수·반-아첨 / 마일스톤 AC 게이트 / 종료 시 CURRENT 갱신.
- 시장가 주문 금지 · 비밀값 하드코딩 금지 · KST tz-aware · 외부 엔드포인트 추측 금지.
- 수집 하네스(COLLECT-3): 승인 소스 어댑터만, 독자 웹서치 금지.

## 5. 마지막 / 다음 작업
- CURRENT "진행 중"·"최근 완료" + "다음 후보"에서 구체 행동.

## 출력
간결한 4단 보고. 자동 주입 규칙 장황한 재설명 금지.
