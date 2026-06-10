# M2 GitOps 부트스트랩 · 격리 OpenClaw · R0~R4 실거동 검증

기간: 2026-06-09(저녁) ~ 2026-06-10 (KST)
범위: 새 기기에서 trading repo 클론 → 운영 가동까지 완전 이식 체인 구축 + R0~R4 라운드 실거동 검증.

## 산출 — 코드/스크립트

**환경 핀 (`.tool-versions`)**
- Python 3.13.13 (asdf python 플러그인 신규)
- Node 22.22.3 (Node 23의 `node:sqlite.statement.columns` 부재 회피, CLAUDE.md 지시)

**격리 OpenClaw 인스턴스 (INFRA-1)**
- 바이너리: `~/.openclaw/bin/openclaw` (Node 22.22.3 번들, `install-cli.sh` 자동 설치)
- 상태: `.runtime/openclaw/` (gitignored), 포트 **18790** (개인 인스턴스 18789와 분리)
- 설정 템플릿: `ops/openclaw/openclaw.template.json` — env 치환, Ollama 모델, Telegram 명시 비활성(`channels.telegram.enabled=false` + `plugins.entries.telegram.enabled=false` 둘 다 — 동일 봇 폴링 충돌 차단)

**GitOps 부트스트랩 체인 (INFRA-2, 멱등)**
- `ops/bootstrap.sh` (9 step): asdf 플러그인·`asdf install`·Poetry·`poetry install`·`.env` 점검·`OPENCLAW_GATEWAY_TOKEN` 자동 생성·`install-cli.sh` 격리 설치·`.runtime` 디렉토리·config 렌더·pytest+mypy
- `ops/openclaw/render_config.py` — env 치환 + JSON 검증, 미치환 `${VAR}` 잔류 시 실패(SAFE)
- `ops/openclaw/start-gateway.sh` — tmux `openclaw-trading` 세션, 30초 부팅 대기
- `ops/openclaw/pair.sh` — CLI 디바이스 `operator.admin` 스코프 점진 상승 자동 처리 (pairing→read→admin, gateway 토큰으로 승인)
- `ops/openclaw/sync.py` — SAFE 락 해제, 실제 매니페스트↔cron 동기화 (add/update/skip/rm-stale 멱등)

**테스트 회귀 수정 (M1 baseline 복원)**
- `tests/test_news.py`, `test_llm.py`, `test_r3.py`, `test_contracts.py` — EventType/Scope/CatalystType enum 도입 후 일부 test 픽스처가 string 리터럴로 남아 있던 mypy strict 회귀 9건 수정. src/ 무변경.

**R2 운영 가시성**
- `src/trading/rounds/r2.py`: `BatchProgress` dataclass + `on_batch: Callable[[BatchProgress], None] | None` 키워드
- `src/trading/score_news.py`: EventStore를 LLM 호출 전에 생성 → 콜백이 직접 incremental append + 배치별 진행 출력
- 효과: 중단해도 처리된 배치 보존(append-only로 재실행 시 v2 추가), 운영 시 LLM 에러/폐기 실시간 가시화
- 신규 테스트: `test_run_r2_on_batch_callback_streams_progress`

## 페어링 메커니즘 (검증된 동작)
- 디바이스 ID는 머신·공개키 기반 결정론. 첫 접속 시 `operator.pairing` 스코프만 부여
- write 시도 → 게이트웨이가 "scope upgrade pending" pending 생성
- `openclaw devices approve <reqId> --token $OPENCLAW_GATEWAY_TOKEN` (게이트웨이 토큰이 메타 권한, websocket 우회해 로컬 state 파일 직접 접근)
- 스코프 단계적 상승: `pairing → read → admin`. 한 번에 admin 안 됨 — 시도/승인 반복. `pair.sh`가 자동 처리.

## 실거동 검증 (claude -p Haiku-4-5, 모든 라운드)

| Round | 검증 | 핵심 산출 |
|---|---|---|
| R0 collect-macro | 11 facts (FRED·ECOS·DATAGOKR), all verified | `.runtime/collect/2026-06-10/macro_indicators.sqlite` |
| R0 daily-eod (market→sectors→screen→factpack) | 30 candidates | `data/market.sqlite` |
| R0 collect-news | 395건 (네이버+SearXNG), 쿼리플랜 L1=15·L2=26·L3=6, dedup 변화 없음 | `data/news.sqlite` |
| R1 gate-news | gate_news() 통과 | |
| R2 score-news (3-batch slice) | universe 밖 종목 `affected=[]` 환각 가드, catalyst_type/scope/strength 분류 정확 (한미 조선업 협력→policy_regulation/0.8, 카카오 파업→management/0.45), 0 batch errors | `data/events.sqlite` v1 |
| R4 verify-catalysts (threshold=0.3) | 1 event 선별, 3 lens 모두 survived=False — 메타 시그널 활용("사전 공지된 부분파업→시장 사전 반영", "회사 공식 '서비스 차질 없을 것' 발표", "정부 모니터링 중") | EventStore v2 (verification 부착) |
| R3 reason-theses (323410 카카오뱅크) | 3 페르소나 격리 출력. **supply 페르소나가 투자자별 매매동향 미수집 상황에서 "방향성 판단 보류"** — 환각 가드 페르소나 레벨 동작. cycle/macro/supply 각자 입력 슬라이스만 사용, 결론 인용 없음 | `data/theses.sqlite` (3 thesis records) |

총 테스트: **162 passed** (M1 19 → 161 → 162, R2 callback 테스트 +1). mypy strict: **0 issues** (73 files).

## cron 11개 등록 후 비활성
- sync.py --apply로 매니페스트 동기화 (am: macro 06:10·news 06:20·score 06:30·verify 06:45·reason 06:55, eod 08:00, pm: news 16:20·macro 16:30·score 16:32·verify 16:45·reason 16:55, 월~금)
- 라운드 일부 미검증·R4 threshold 미튜닝 상태에서 자동 실행 회피 위해 일괄 `cron disable`. M3 진입 + 알림 인프라 준비 후 enable.

## 비밀 관리 변경점
- `.env` 키 7개 채움(FRED·ECOS·DATAGOKR·DART·NAVER×2·SEARXNG localhost:8888)
- KIS REAL 키 발급·인증 토큰 발급·잔고 조회(rt_cd=0, 빈 계좌) 검증. `KIS_CANO=68411766`, `KIS_ENABLE_TRADING=false`
- `OPENCLAW_GATEWAY_TOKEN`: bootstrap.sh가 신규 생성 — 1Password "stock / .env" 동기화 필요

## 발견된 후속 작업
- **R4 threshold 튜닝**: 기본 0.5는 실데이터(0.40·0.45 위주) 빈도 낮음 — 분포 기반 결정 필요
- **R3 grounding 보강**: supply 페르소나의 투자자별 매매동향 수집기 미구현(🔴 KRX 의존) — 항상 약한 논제만 산출
- **일반 R1 게이트**: FactRecord stale/conflict 플래그 (설계서 §3, 뉴스 외 거시·시세에도 적용) 미구현
- **market_calendar/**: 휴장일·DST 가드 빈 디렉토리
- **alerts/**: P0/P1/P2 Telegram Bot API 직접 호출 어댑터 미구현

## 부산물 — 메모리
3 신규: `project_trading_openclaw.md`, `feedback_openclaw_cli_pairing.md`, `feedback_openclaw_telegram_polling.md`. 3 제거: news 파이프라인 관련 stale 메모(개인 OpenClaw의 news 크론 잡 일괄 삭제 후 무효화).
