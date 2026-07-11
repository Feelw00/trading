# 2026-07-11 — P-2 미분류 LLM 폴백 분류기 (llm-fallback-v1)

## 배경
P-1 후에도 게이트 통과 243종 중 151종 미분류 — 전부 혼재 KSIC(649 다각화 지주·292 장비·
262 전자부품·582 SW·465 도매)라 결정론 크로스워크로는 불가능한 영역. P-9 도메인 축의 잔여 선행 조건.

## 산출
1. **`src/trading/sector_llm.py`** — `python -m trading.sector_llm [--limit N] [--dry-run]`
   - 대상: 게이트 통과 중 4소스 병합 미분류 & `llm-fallback-v1` 미시도분(시도분 스킵 — sectors.py 패턴).
   - `claude -p` 배치 25종/콜(R2 패턴: LLMClient 주입·complete_json·실패 배치는 시도 기록 없이 스킵→재시도 가능).
   - 프롬프트: 29 taxonomy를 SECTORS 메타에서 동적 생성(하드코딩 금지) + "모르면 빈 배열·추측 금지" 강제.
   - **환각가드(코드 재검증)**: taxonomy 밖 값·배치 밖 종목코드 → 항목째 폐기+카운트,
     confidence<0.7 또는 basis(근거 한 줄) 누락 → 미채택(미분류 기록), 종목당 최대 2섹터.
   - 모델 주입: `SECTOR_LLM_MODEL` → `R2_MODEL` → `CLAUDE_MODEL`(.env.example에 추가).
2. **병합 순위 확장**: `screener.SECTOR_SOURCES = (manual, llm-cls, dart-ksic, llm-fallback)` —
   폴백은 최후순위라 큐레이션·grounded를 절대 덮지 않음. factpack·discuss_pack은 SECTOR_SOURCES
   참조라 자동 반영.
3. **`/collect` 스킬 배선**: 4단계로 sector_llm 추가(신규 진입 종목만 — 시도분 스킵이라 일일 비용 미미).

## CLAUDE.md 정합
섹터 분류는 LLM 허용 영역(원 `llm-cls-v1`이 멀티에이전트 산출, R1/R5.5 판단 아님 — 태깅 메타데이터).
absolute #2의 "판단 로직 LLM 금지"와 구분: 스크리너 점수·게이트는 여전히 순수 코드,
LLM은 DB에 태그를 남길 뿐이고 그마저 코드가 재검증한다.

## 실행 결과 (2026-07-11)
- dry-run 10종 → 채택 9/10(1종 정직 미분류) 확인 후 전량 실행.
- 7배치 151종: **채택 123 · 미분류유지 28 · 폐기 0 · 배치 실패 0**.
- 분포: semiconductor 33 · holding 20 · chemicals 13 · machinery 12 · construction 9 ….
- 스크리너 **상위 30 미분류: 22(아침) → 16(P-1) → 2(P-2)**. 잔존 2종(데이타솔루션·기가비스)은
  LLM이 모른다고 남긴 것 — 발명하지 않는 게 의도된 동작.
- 스팟체크: 와이지-원=machinery(절삭공구)·파크시스템스=semiconductor(계측장비)·
  금호타이어=auto·S-Oil=chemicals(에너지 버킷 부재로 최근접) — 타당.

## 검증
- mypy strict + 신규 테스트 8건(프롬프트 생성·채택/미채택·발명값 폐기·배치밖 코드 폐기·
  basis 필수·다중섹터 상한·배치 실패 시 기록 없음·dry-run 무기록·모델 우선순위) + 전체 스위트 통과.

## 남은 것 / 메모
- basis(분류 근거)는 채택 게이트로만 쓰고 DB엔 저장 안 함(스키마 변경 회피) — 오분류 논쟁 생기면
  컬럼 추가 검토.
- cross-check(다중 패스 검증)는 미도입 — 오분류 실증 사례가 나오면 추가(PROPOSALS P-2 미결 항목).
- 잔존 미분류 28종은 재실행해도 스킵됨(시도 기록) — LLM이 진짜 모르는 소형주. 필요시 수기 큐레이션.
