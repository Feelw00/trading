---
name: approve
description: 주문 승인 — 저녁 결재 보고에서 검토한 OrderDraft를 approved로 전이한다(수동, 의도된 마찰). 승인된 것만 다음날 arm-check 활성 풀에 들어간다. "주문 승인", "승인", "/approve", "이거 승인해줘" 시 사용.
---

# approve — OrderDraft 승인 전이 (draft→approved)

저녁 결재 보고의 승인 요청을 운영자가 검토한 뒤 **명시적으로 승인**한다(설계서 §6 "의도된 마찰").
승인된 초안만 다음 거래일 arm-check/R5.5의 **활성 풀**에 들어가 발동 대상이 된다.

## 경계
- 전이는 순수 코드(`trading.approve`, append-only 새 version). LLM은 자동 승인하지 않는다 —
  **운영자가 어떤 id를 승인할지 확인**한 뒤에만 실행한다(마찰은 의도다).
- 승인은 매수 약속이 아니다 — arm 조건이 맞는 날에만 진입하고, time_stop_days 거래일 안에
  조건이 안 오면 만료된다.

## 흐름
1. `.env` 로드 후 `poetry run python -m trading.approve --list` — 미승인(draft) 목록 확인.
   - 비어 있으면 "승인할 초안 없음" 전하고 종료.
2. 목록을 운영자에게 보여주고 **어느 것을 승인할지 묻는다.** 저녁 보고의 근거·발동 조건을
   함께 상기시키되, 승인 여부는 운영자가 정한다(동조 금지).
3. 운영자가 지정한 id만 `poetry run python -m trading.approve <id> [<id> ...]` 로 승인.
4. 결과(approved/skip) 전달. 승인 후 "다음 거래일 아침 `/arm-check`로 발동 점검" 안내.

> 가드: 자동 승인 금지(운영자 확인 후) · id 명시(전체 일괄 없음) · append-only · 승인=조건부(매수 확정 아님).
