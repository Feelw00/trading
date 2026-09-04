# 2026-09-04 — COLLECT-5 결재 2건 구현(policy v2.18): 리츠 면제 해제·접수분별 배당 저장 · 분할 인적/물적 구분

세션 시각 11:55~12:45 KST. 실측 노트 `docs/research/2026-09-04-collect5-reit-dividends-split-method.md` → 운영자 **"둘 다 권고대로
가고 구현해"** → 같은 세션에 구현·재수집·검증.

## 1. 결정(운영자, 세션 권고 (a)(a) 채택)
| # | 결정 | 기록 |
|---|---|---|
| ① | 리츠 환원 면제 해제 + `alot_facts` 접수분별 저장 | POLICY_PARAMS v2.18 ① · OPEN_QUESTIONS COLLECT-5 🟢 |
| ② | 인적분할 강등 해제 · 물적·혼합·미상·미수록 강등 유지 · **물적 배제 승격 기각** | POLICY_PARAMS v2.18 ② |

## 2. 구현
### ① `collectors/returns.py` — 접수분별 배당
- 새 표 `alot_reports`(키: 종목·연도·접수번호·행 번호, `stlm_dt`·frmtrm·lwfr 보존). `upsert_alot`이 옛 `alot_facts`와 **병행 기록**(옛 표 보존 —
  append-only). `has_alot_report`.
- 읽기 `dividend_series`: 연도 안에서 **결산기준일별 최신 접수번호**만(정정 = 같은 결산일의 새 접수번호 → 최신 대체) →
  `parse_dividend_rows`(보통주 우선·'-' 폴백·라벨 정규화·총액 오기재·액면 배당률 가드, 순수) → `aggregate_reports`(dps·수익률 **합**,
  성향은 1건일 때만, `n_reports`). 접수분 표 없는 연도는 옛 표 폴백(`n_reports` None).
- `collect_returns`: 'ok' 시도라도 접수분 표가 비면 **1회 자동 재수집**(자가 치유) — 실행 결과 통과 518종 전부 재수집(접수분 2,573건,
  오류 0). 검증: 이지스레지던스 2025 dps 150→**300**·수익률 3.6→**7.4**(n=2) · 삼성전자 1,668/1.5 불변 · 흥국 수익률 None 유지.
- `quality.RETURNS_EXEMPT_INDUSTRIES = ∅`(상수는 호환용) · `review.auto_review` 리츠 hold 조건 제거.

### ② 분할 결정
- `dart.split_decisions`(`cmpDvDecsn.json`)·`split_merger_decisions`(`cmpDvmgDecsn.json`) — 공식 가이드 apiId 2020051/2020052, 실호출
  관측 필드 주석.
- `returns.split_decisions` 표(원문 payload 박제 + `method_text`·`method_hint`·설립/존속 회사명) · `classify_split_method`(순수: 공백 제거 후
  '인적분할'/'물적분할' 포함, 둘 다=혼합, 없으면 보조 필드 `ex_sm_r`/`mg_stn`, 그래도 없으면 미상) · `SplitDecision`·`SplitAssessment`
  (`downgrade` = 이벤트 있고 결정 없음 → 1(미수록, 보수) / 결정 중 ≠인적 수 · `summary`).
- `collect_split_decisions`(멱등 키 = 10년 창 + 이력의 최신 접수일 — 새 분할 공시가 잡히면 재수집). 실행: 이력 54종 중 결정 확보 51 ·
  미수록 3(2016~17 접수분: 롯데칠성·크라운해태홀딩스·샘표). 저장 분할 54·분할합병 2.
- 스크리너 `screen/__main__`·`web/picks`: 강등 조건 `splits[sym] == 0` → `split_assessment(sym).downgrade == 0`. 표기 "⚠분할 물적 1" /
  "(인적분할 이력 — 강등 없음)". CLI 꼬리 문구 v2.18로 갱신.

## 3. 검증
- pytest **709 passed**(신규 9: 접수분 합산·정정 대체·레거시 폴백·재수집·분류 어휘·평가 규칙·결정 수집 멱등·면제 해제·리츠 hold 제거) ·
  mypy 217 clean.
- 스크리너 수동 실행(12:35): 평가 2,672 · 통과 519 · **코어 자격 168**. `_build_picks` 계량: 인적분할 이력으로 코어 복귀 **5종**
  (매일홀딩스·케이씨·F&F홀딩스 여력 +49%·유니퀘스트·오리온홀딩스 — 전부 미심사 → 9/5 weekly `review-auto` 대상, F&F홀딩스만 여력 하한
  충족) — 실측 노트의 "인적만 17종" 중 다른 코어 축(안정·고PER·환원 등)까지 통과한 것이 5종. 코어 리츠 = 이지스레지던스(배당 5y·7.4%).
- 웹 보고 서버 재기동(HTTP 200, /picks 200).

## 4. 에코백(지시 대비)
- 지시 그대로 구현. 좁힌 것: 없음. 추가로 한 것: 옛 'ok' 시도 자동 재수집(운영자 개입 없이 데이터 정상화), 스크리너 CLI 문구 갱신.
- 도입하지 않은 것(범위 밖·별도 결재): 물적분할 **후 자회사 상장** 사실 소스 → 중복상장 감점·반복 탈락(PIVOT-7 ④) · 배당수익률 시가 재계산
  표기 · 2016~17 미수록 3건의 원본(document.xml) 파싱 폴백.

## 5. 후속
- 9/5 weekly: `audit` 첫 cron · 새 코어 5종 자동 심사 · `collect_returns`가 정기 경로에서 접수분 표를 채움(신규 통과 종목).
- 리츠 22종 중 미통과 20종은 수집 대상 아님(설계) — 통과 시 자동 수집.
- 분할설립회사 상장 여부: `split_decisions.new_company` ↔ 상장 종목명 매칭 실측이 다음 단계 후보(PROPOSALS 미등록 — 결재 시 등록).
