---
description: 자료 수집 라운드 수동 트리거 (LLM 수집 → SQLite). 인자로 클러스터 1개 또는 전체.
argument-hint: "[cluster_id]  예) semis_display  (비우면 전체 11)"
allowed-tools: Read, Write, Bash
---

`collect` 스킬을 사용해 자료 수집 라운드를 수행한다.

**대상:** $ARGUMENTS  (비어 있으면 전체 11개 클러스터: 섹터 9 + 뉴스 2)

준수 사항(스킬의 환각 가드):
- **하네스:** 승인된 소스 어댑터(KIS/KIS MCP 등, COLLECT-2)만 사용. **독자 웹서치 금지.** 기억 기반 수치 금지.
- 각 레코드에 `source`(URL)·`as_of`·`fetched_at`(KST) 필수. 미검증은 `verified=0` + `UNVERIFIED` 사유.
- 결과는 `.runtime/collect/<날짜>/<cluster_id>.sqlite`에 append-only INSERT.
- 끝에 요약 보고: 수집 건수 / UNVERIFIED 건수 / 누락 항목.
