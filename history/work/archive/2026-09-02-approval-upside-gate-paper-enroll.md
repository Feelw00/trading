# 2026-09-02 (오후) — 승인 노출 하한 · 페이퍼 편입 원칙 · 목표가 괴리 표기 · /picks 열 정리

## 계기
운영자 질문: "선정 후보에 LF가 있는데 이전에 수익률 컷으로 아웃된 거 아니었어?"
- 사실: LF(093050)는 9/1 여력 +1%로 **페이퍼 등록**만 제외(policy v2.5 등록 하한 +30%),
  심사 원장에는 같은 날 운영자 승인이 남아 있었다. 9/2 /picks 원장·큐 분리(v2.11)로 "판정 있는
  종목 전부"를 보이게 되자 승인 종목에 노출 — 표에 여력 +0%는 찍혔지만 상태 구분이 없었다.
- 판정: 판정 결함이 아니라 표시·게이트 결함. 1차 제안(표기 열 플래그)에서 운영자 지시가
  확장됨(4건) → 분석·대안 제시 후 "나머지는 알아서, 30% 미만은 승인 종목에서 제외" 확정.

## 운영자 지시(원문 요지)와 인코딩 — 에코백(7/14 규칙)
| 지시 | 인코딩 | 좁힘/확장 |
|---|---|---|
| picks 표 열이 너무 많다 | 원장·큐 표 15열 → 7열(종목·산업·국면·회귀 여력·위험조정수익률·심사·표기). 분해 지표는 종목 상세 | 열 선정은 세션 판단 |
| 승인 종목은 실투자 핵심 → 30% 제한 | **승인 노출 = approved ∧ 회귀 여력 ≥ +30%** 파생 게이트(`Pick.effective_verdict`). 미달 "⏸승인 보류"로 조건부 표, 원장 불변, 회복 시 자동 복귀. 자동 심사 rule-v1에 하한 추가·수동 approved 거부 | "실현 예상 수익" = 회귀 여력으로 해석. 판정 자체에 넣지 않고 파생 게이트(가격 잡음 배제). **보유 중 예외 없음** |
| paper는 실투자·명시 이동만 | weekly-v3 `paper-register` 폐지. guide-orders가 가이드 밖 실보유를 실평단=시작가로 자동 편입(`enroll_holding`, 승인·하한 불문 — "심사 외" 표기). `paper register <심볼>` 명시 경로 유지(등록 자격 유지) | 심사 외 실보유 편입은 세션 판단(사실 기록) |
| 실투자 종목 예상치 변경 → 실시간 반영 또는 표기 | **표기**: /paper 목표가·추정 목표가 열, 등록 대비 ±15% ⚠(호버 %), eod-v3 보고 최상위 줄. 반영 `paper retarget <심볼> [가격\|auto] --reason` | "실시간 반영"은 보류(GUIDE-1 결재) — 매도선·조건주문 안정 |

## 산출
- `web/picks.py`: `MIN_UPSIDE_PCT` 게이트·`effective_verdict`·`held`·플래그("⏸승인 보류", "보유 중(가이드)")·
  `approved_picks` 필터·배지 "⏸승인 보류"·표 7열·헬퍼 `sector_median_pbr/regression_upside/current_upside`.
- `review.py`: rule-v1 여력 < +30% → hold("회귀 여력 +30% 회복") · CLI approved 하한 거부(rc 2).
- `paper.py`: `register` 명시 심볼 필수(승인 아님 거부) · `enroll_holding` · `current_targets` ·
  `TargetDrift/target_drift` · `retarget` 서브커맨드 · 기본 마킹에 괴리 줄(`_print_drift`).
- `guide_orders.py`: `run(enroll=)` 4a 실보유 편입(이벤트 `enrolled`), 편입 불가 신규 보유만 P1.
- `run.py`: weekly-v3에서 `paper-register` 제거.
- `web/paper_page.py`: 목표가·추정 목표가(⚠) 열 · "심사 외" 배지 · 편입 안내 문구.
- 문서: POLICY_PARAMS v2.12 블록 · OPEN_QUESTIONS GUIDE-1 · CURRENT.

## 검증
- 실데이터: 승인 노출 5종(전부 보유) · LF `approved_blocked` 여력 +0.16% · 보유 5종 괴리 ⚠ 없음.
- 테스트 신설: `test_picks_gate.py`(경계 30% 포함·결측 불충족·보유 예외 없음·정렬),
  `test_review`(여력 20% → hold), `test_paper`(편입·중복 방지·추정 목표·괴리 임계),
  `test_guide_orders`(편입 즉시 매도 계획·재편입 없음). pytest 전체 + mypy strict 통과.
- 웹 재기동 후 /picks(7열·승인 5·조건부에 LF 보류)·/paper(목표가·추정 목표가 열) 확인.

## 후속
- GUIDE-1 결재(임계 15%·자동 반영 여부·심사 외 실보유 취급).
- 9/3 08:40 guide-orders 첫 자동 실행 — 편입 경로 무해 확인(신규 보유 없음 예상).
- 9/5 weekly-v3 — 자동 심사 하한 첫 발동·페이퍼 자동 등록 부재 확인.
