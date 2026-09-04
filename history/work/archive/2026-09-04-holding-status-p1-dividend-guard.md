# 2026-09-04 — 결재 3건(v2.17) · 배당수익률 액면 오기재 가드 · 추가 매수 반영 · 트리거 턴 교정

세션 시각 10:45~11:20 KST. 부팅 브리핑 → 운영자 "내가 결정해야 할 게 뭐가 있지?" → 세션이 "지금 결정할 것 3건 + 세션 작업 순서 +
지금 아님" 구조로 제시 → 운영자 **"3건 다 권고대로 가고 배당수익률부터 해. 오늘 오전에 추가 매수했으니까 확인 후 반영"**.

## 1. 부팅에서 발견 — cron list `eod-v3 error`의 실체
- openclaw `cron list`: eod-v3 status `error`(9/3 18:00), 9/1도 error, 9/2·8/31·8/28·8/27 ok. Python 쪽은 `runs.sqlite` 18:00:06 시작 →
  18:12:44 rc=0, 텔레그램 실행 보고 발송, 18:30 감시 잡 ok — **체인은 정상**.
- 운영자 가설 "토큰 부족" → 트랜스크립트(`.runtime/openclaw/agents/main/sessions/565876a4….jsonl`)로 반증: `stopReason: stop`,
  errorMessage 없음, 429·quota 흔적 없음, 최종 답변 텍스트 `""`(출력 4토큰, 비용 0). exec 결과는 "Command still running (pid 2861)".
  즉 모델이 exec 뒤 **빈 최종 답변**을 냈고 openclaw가 이를 "Agent couldn't generate a response"로 표기.
- 원인 실험(scratchpad): `setsid -f sh -c '…'`가 exec 도구의 stdio를 상속하면 파이프 소비자가 분리 프로세스 종료까지 EOF를
  못 받는다(현행 형태에서 `tail`이 8초 대기). `</dev/null >/dev/null 2>&1`을 붙인 형태는 0.01초에 `launched:` 반환·분리 프로세스
  생존(pgrep 확인).
- 교정(`ops/openclaw/sync.py`): exec 명령에 stdio 분리 + 프롬프트 "결과가 무엇이든(launched·still running·빈 출력) `launched:<job>`
  한 줄만 답하라 — 빈 답변 금지". `sync.py --apply` → 5잡 [update](rm+add, ID 갱신), payload 검증 True/True, enabled.
  **첫 실검증 = 9/4 18:00 eod-v3(status ok 기대)**. 감시망(ALERT-1 check)은 그대로.

## 2. 결재 3건(운영자 "권고대로")
| # | 결정 | 기록 |
|---|---|---|
| ① 보유 종목 상태 전이 | **P1**(P0 veto 창 아님 — 자동 청산 경로가 없어 카운트다운이 걸 대상 없음) | OPEN_QUESTIONS SCREEN-1 🟢 · POLICY_PARAMS v2.17 ① |
| ② PIVOT-10 잔여 | 원유정제·종합반도체 v1.3 유지 확정, 대기 항목에서 제거 | OPEN_QUESTIONS PIVOT-10 🟢 · POLICY_PARAMS §2 표 주석 |
| ③ 트리거 교정 | 승인 → §1 | POLICY_PARAMS v2.17 ④(ops) |

### ① 구현 — `src/trading/holding_status.py`
- 순수: `kis_transitions(held, latest, previous)` — 직전 스냅샷(as_of < 최신) 정상 ∧ 최신 플래그(관리·정지·상폐 의심,
  `classify_kis`) → Transition. 직전 없음·직전도 플래그·해제(플래그→정상)는 제외. `audit_adverse(held, verdicts, fy)` — 최신 접수분
  비적정, 키 = 접수번호(정정 공시 = 새 알림).
- 배선: `check_kis`(eod-v3 `status-v3` 끝, `run._collect_status_v3`) · `check_audit`(weekly `audit` 끝, `run._audit_v3`) →
  `AlertDispatcher` P1(rule/action/deadline 고정 — "자동 청산 없음, 정리는 운영자 앱 직접, 매도 예약 유지"). ALERT-1에 따라 다음
  실행 보고 꼬리에 동봉. 실보유 = `guide_orders.BrokerStore.latest_holdings()`(수량>0). 감시 실패는 수집 rc 불변(best-effort).
- 중복 방지: `status.sqlite holding_status_alerts`(symbol, kind, key UNIQUE) — 같은 날 재실행·같은 접수분은 침묵.
  `StatusStore.kis_previous(symbol, before_as_of)` 신설.
- 테스트 `tests/test_holding_status.py` 4건(전이 판별·비보유/역순 무시·감사 비적정만·저장소 배선+중복 방지).

## 3. P-20 ⑥ 배당수익률 오염 교정 — "행 선택"이 아니라 "액면 기준 기재"
- 전수 스캔(`alot_facts` 37,763행): 보통주/`-` 행 '현금배당수익률' > 20% = 11행. **전부 DPS ÷ 액면가 × 100과 정확히 일치**
  (흥국 2021~25: 200/220/240/220/280원 ÷ 500 = 40/44/48/44/56%). 예스코홀딩스 2023 25.6%는 8,750÷5,000=175%와 불일치 → 정상(특별
  배당). 즉 일부 공시가 같은 행에 **액면 배당률**을 적는다.
- 가드 `is_par_based_yield(yield, dps, par)`: |수익률 − DPS÷액면가×100| < 0.05 → `dividend_series`가 yield_pct=None(지어내지 않음),
  dps 유지. 소비처: `quality.dividend_streak`(dps>0 ∨ yield>0 — dps가 있으니 불변) · `web/picks.py` 배당 캐리 표시(None → 빈칸).
- 실데이터 효과: 발동 12종 23행. **FY말 종가 교차 검증**(market.sqlite): 22행은 시가 수익률과 크게 다름(예: 흥국 2025 시가 5.4% vs 공시
  56 · 유진투자증권 2025 시가 5.2% vs 공시 3.6=180÷5,000 — 액면가 근처 주가라 "우연"으로 보였던 것도 오기재), 1행(GS글로벌 2023
  1.0%)만 시가≈액면 우연 일치 — 수치가 같아 None이어도 손실 없음. → 시가 교차 보정 코드는 **추가하지 않음**(단순 가드 유지).
- 라벨 정규화 `_normalize_stock_kind`: 공백·'보통주식'(46행, 황금에스티 등) → 보통주. 그 외 어휘(종류주·소액주주·중간 배당 …,
  비표준만 가진 16종)는 해석하지 않음.
- /picks 스모크: 164종 중 >15% 잔존 0, None 5(흥국 포함). 웹 보고 서버(`trading-reports`) 재기동으로 반영.
- 테스트 `tests/test_returns.py` +3.

## 4. 추가 매수 반영(EXEC-12 경로만)
- 읽기 전용 대사 `ops/reconcile_broker.py`(10:54, 박제 `.runtime/reconcile/2026-09-04-broker.json`): 신규 종목 없음, 7종 전부 수량 증가 —
  아이퀘스트 38→80(평단 2,745) · 신세계I&C 8→15(13,079) · 동성케미컬 25→55(3,781) · 메타바이오메드 20→50(3,889) · 케이씨피드
  32→85(2,344) · CJ대한통운 2→5(73,673) · 케이디켐 1→25(9,261). 평가 1,625,925원(08:40 대비 60.9만 → 162.6만), 매수가능 206원.
  토스 OPEN 조건주문 7건은 옛 수량 기준(WATCHING, 만료 9/10).
- `poetry run python -m trading.run guide-orders`(live, 10:58): 보유 7·가이드 7·**등록 7·취소 7**·유지 0. 예: 아이퀘스트 80주 → 80% 선
  16주 @3,460 · 케이씨피드 85주 → 17주 @2,740 · CJ대한통운 5주 → 1주 @80,700(2주 때는 120% 선 1주였음 — 수량이 늘어 80% 선이 1주
  이상 성립) · 케이디켐 25주 → 5주 @9,810. 만료 9/11. 저널 cond_id 최신 상태 sent 7·cancel 9·canceled 3. 텔레그램 실행 보고 발송.
- **에코백(지시 좁힘 없음, 규칙 그대로)**: 시작가(가이드 기준가)는 불변이라 추가 매수로 오른 평단(≤1%)은 매도선 가격에 반영되지 않고
  **수량만** 반영됐다(9/3 규칙 · P-19 ② "실체결↔원장 정합"은 미결). 매수·정리 주문은 세션이 내지 않았다.

## 5. 검증
- `poetry run pytest -q` 전체 통과(실패 0) · `poetry run mypy src tests` 217 files clean.
- cron: `openclaw cron list --all` 5잡 enabled, eod-v3 payload에 stdio 분리·고정 답변 문구 확인.

## 6. 남은 약점·후속
- 트리거 교정의 실검증은 9/4 18:00(ok 기대). 재발 시 트랜스크립트 stopReason부터(session.md gotcha).
- 상태 전이 감시: 해제(플래그→정상) 알림은 결정 범위 밖이라 침묵 — 필요하면 P2 정보성으로 추가 검토. 첫 실행은 9/4 18:00(KIS)·
  9/5 09:30(DART).
- 배당: 시가 기준 재계산(DPS÷FY말 종가) 표기 도입 여부는 기준 변경이라 별도 판단(CURRENT B-2 잔여). `mrkt_warn_cls_code` 어휘·KIND
  실측은 SCREEN-1 잔여 그대로.
- CURRENT.md의 "관찰 대기"·"결재 6건" 중복 블록 정리 완료(이번 롤오버).
