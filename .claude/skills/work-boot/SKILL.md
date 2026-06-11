---
name: work-boot
description: 부팅 컨텍스트 — DB에서 데이터 신선도·스크리너 후보·history를 읽고, 미수집이면 알림·제안한다. 거시 라이브 조회는 collect-macro 담당. "/boot", "부팅", "세션 시작" 시.
---

# work-boot — 부팅 컨텍스트 (DB 읽기 중심)

거시 라이브 조회는 `collect-macro`가 담당. 여기선 **DB와 history만 읽는다(신선도 확인 포함)** — 무거운 수집(전종목·공시·뉴스)은 안 한다. 미수집이면 **확인·유도만**(해당 `/collect…` 제안).

## 1. 데이터 신선도 (콘솔 날짜 기준)

### 1a. 전종목 EOD (시세 DB)
- boot가 확인한 **콘솔 날짜**를 기준으로 **최근 거래일**을 판단(주말·휴장 + 국내 EOD는 +1영업일 공개 고려).
- DB 최신 수집일 확인:
  `poetry run python -c "from trading.collectors.market import MarketStore as M; s=M(); print(s.latest_date()); s.close()"`
- DB 최신일이 최근 거래일보다 뒤처지면(영업일 기준 ~2일+ 갭) → **⚠️ 알림: '〈DB최신일〉까지만 수집됨, 〈최근거래일〉 미수집' + `/collect` 실행 제안.** 정상이면 'DB 최신: 〈날짜〉'.

### 1b. 뉴스 (오늘자 확인 — 없으면 수집 유도)
- 오늘(콘솔 날짜 D) 뉴스 적재 여부 확인(단일 영속 `data/news.sqlite`, 수집 아님·읽기만):
  `D=$(TZ=Asia/Seoul date +%F); [ -f data/news.sqlite ] && sqlite3 data/news.sqlite "SELECT COUNT(*), MAX(fetched_at) FROM news_items WHERE substr(fetched_at,1,10)='$D'" || echo "no-db"`
- 오늘자 행>0이면 '뉴스 최신: 〈D〉 (N건)'. **0건이거나 파일 없음(`no-db`) → ⚠️ '오늘(〈D〉) 뉴스 미수집'**, 그리고 소스 키 유무로 분기:
  - 키 있으면(`.env`의 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET` 또는 `SEARXNG_URL`) → **`/collect-news` 실행 제안.**
  - 키 없으면 → **'키 필요 — `.env`에 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`(국내) 또는 `SEARXNG_URL`(해외)'** 명시(흉내내기 금지, COLLECT-3).
- **이유:** 뉴스 없이 분석하면 fact pack의 뉴스 촉매가 빈다(가격·공시·재무는 grounded). 종목 분석(`discuss`)·전망 전에 이 신선도를 먼저 본다.

## 1c. 자동 사이클 점검 (cron 18슬롯 가동 중 — 2026-06-10~)
- 전일~금일 자동 사이클 사후 검증(트리거 안 함, 판정만):
  `poetry run python ops/openclaw/drill.py --audit`
- **PASS만이면 한 줄 요약. WARN/FAIL 있으면 ⚠️ 잡명+사유 보고**(잡별 로그 `.runtime/logs/cron/<잡>.log` 참조). 라운드 실패 P1 알림(Telegram) 수신분과 대조.
- 게이트웨이 생존도 함께: tmux `openclaw-trading` 세션 + 포트 18790 (죽었으면 `bash ops/openclaw/start-gateway.sh` 제안).

## 2. 오늘 후보 (스크리너 — DB 위, 수집 없음)
- `poetry run python -m trading.screener` → 상위 후보 + 섹터 태그. **기존 DB로만 계산**(라이브 수집 안 함).

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
