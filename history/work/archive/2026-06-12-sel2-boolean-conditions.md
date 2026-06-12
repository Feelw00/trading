# 2026-06-12 — SEL-2: selector boolean 조건 평가 (==true/==false)

arm-check 실호출(P-6)에서 발견: R5가 `prev_day_high_reclaim ==true` 같은 boolean 흐름변수 조건을
산출하는데, selector(`engine._COND`)는 `<op><숫자>`만 평가 → `==true`가 "평가 불가=미충족"으로
빠졌다. 현재가가 전고를 회복(flowsnap 1.0)해도 발동 판정에 반영 안 됨.

## 해소 (selector + R5 프롬프트 둘 다)

- **selector** `engine.eval_condition`: `_BOOL`(`(==|!=)(true|false)`, 대소문자 무관) 추가. 관측치
  1.0=참 / 0.0=거짓(flowsnap 인코딩과 정합)으로 평가. 시각·문자열 등은 여전히 평가 불가.
- **R5 프롬프트**: 조건식 문법 명시 — 연속 변수는 `<op><숫자>`, boolean 변수
  (prev_day_high_reclaim·volume_climax·new_low_renewal_fail)는 `==true/==false`, 그 외 형식 금지.
- **explain**: boolean을 '= 예(true)/아니오(false)'로 표기(평가 불가 표시 제거). arm-check 스킬의
  '평가 불가 경고' 안내도 시각·문자열 한정으로 정정.

"LLM 불신·코드 강제" 정신: R5가 `==true`를 내든 코드(selector)가 일관 평가. 기존 6/11 플레이북
(`==true`)이 재산출 없이 즉시 작동.

## 검증

- pytest **330 passed**, mypy strict **0 issues (77 files)**.
- 테스트: `test_boolean_condition_eval`(==true/==false/!=, 대소문자), `test_explain_condition_boolean`,
  진짜 평가불가 유지(`test_..._still_unevaluable_for_non_numeric_non_bool`, 시각 09:30),
  end-to-end `test_boolean_arm_condition_activates`(boolean 조건 → 발동).
- 실DB: 6/12 arm-check가 `prev_day_high_reclaim = 예(true) → 충족(O)` 출력(과거엔 '평가 불가'로 빠짐).

## 상태

SEL-2 🟢 해소. 남은 미해소: 텔레그램 양방향 직접 승인(인프라 부재, 후속).
