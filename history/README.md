# history — 작업·트레이딩 히스토리

`/boot`(작업 세션 부팅)가 읽는 이력 저장소. **git 추적**(이력=지식, 다른 기기 이식 시 함께 복원).
secrets·`.runtime/`만 git 제외 — 여기는 제외 대상 아님.

## 두 갈래

### 1. `work/` — 작업 히스토리 (개발/AI 작업 루프)
- **`CURRENT.md`** — 슬림·항상 1개. 진행 중 + 최근 완료(1줄) + 다음 후보. **`/boot`가 매번 읽는다.**
- **`archive/YYYY-MM-DD-<slug>.md`** — 완료된 작업 1건 = 1파일(작업당 네이밍).

**롤오버 규칙(슬림 유지):** 작업이 끝나 다음으로 넘어가면 → 전체 기록을 `archive/`로 떨구고,
`CURRENT.md`에는 **한 줄 요약 + 링크만** 남긴다. CURRENT는 항상 가볍게.

**`docs/PROGRESS.md`와의 관계:** PROGRESS = 마일스톤 원장(coarse, CLAUDE.md가 의무화).
work 히스토리 = 그보다 잘은 작업/세션 단위. CURRENT는 마일스톤을 **링크만** 하고 복제하지 않는다.

### 2. `trading/` — 트레이딩 경험 히스토리 (경험의 부산물)
- **`INDEX.md`** — 목차(스캔 레이어): 날짜·유형·종목/섹터·한줄요약·태그·링크.
- **`events/YYYY-MM-DD-<event-slug>.md`** — 상세(온디맨드): 상황·판단·근거·결과·교훈.
- 작성: **수동**(템플릿 `events/_TEMPLATE.md` 복사). 자동 기록은 미도입(필요 시 `docs/PROPOSALS.md`).

**언제 참고:** 특별한 사건 발생 시, 또는 트레이딩 포지션이 모호할 때. 태그(종목·섹터·상황유형)로 선례 검색.

**`src/trading/journal/`와 혼동 금지:** journal = 기계 데이터(append-only DB, FactRecord 등 계약).
`trading/` = 사람/AI의 서사형 경험·판단·교훈. 다른 층위, 서로 보완.

## boot 소비
- **매 부팅(고정):** `work/CURRENT.md` 전체 + `trading/INDEX.md` 최근 N줄(가벼운 스캔).
- **온디맨드:** `trading/events/*.md` 상세는 특별사건/포지션 모호 트리거 때만 INDEX에서 골라 로드.
- `_`로 시작하는 파일(`_TEMPLATE.md`)은 실제 항목이 아니므로 스킵.

## 규칙
- 파일 내 타임스탬프는 KST 명시(CLAUDE.md rule #5).
- 내용은 한국어. 파일명은 영문 kebab + 날짜.
