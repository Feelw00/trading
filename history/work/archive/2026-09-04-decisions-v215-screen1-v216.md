# 2026-09-03 오후 ~ 09-04 오전 — 결재 6건 반영(policy v2.15) + SCREEN-1 관리종목·거래정지·감사의견 필터(v2.16)

## 계기
- 부팅 후 운영자 "다음 해야할 항목들을 다시 검토하고 갱신" → CURRENT 할 일 전면 재검토(완료 항목 6건 제거, 관리종목 필터가
  PIVOT-3로 오기돼 있던 것을 SCREEN-1로 분리 등록) → "내가 결정해야 하는 것만" 6건 제시 → 결재 → "SCREEN-1 데이터 소스
  실측부터" → "(a)로 하고 수집기 2종부터 구현".

## 1. 결재 6건 반영 (policy v2.15, OPEN_QUESTIONS GUIDE-1 🟢·EXEC-14 🟢, PROPOSALS P-19 ①·P-20 ①⑤)
| # | 운영자 결정 | 인코딩 | 좁힘·에코백 |
|---|---|---|---|
| ① 과열 산업 제외 | (a) 가이드 등록 자격만 | `paper.register_block_reason`(승인 → 과열(`cycle_caution`) → 여력 순 순수 함수), `paper register`만 거부 | 승인 노출·자동 심사·**실보유 자동 편입은 과열 무관**(보유는 사실) |
| ② §6 매수 규칙 | "매수는 내가 알아서" | EXEC-14 🟢 — 운영자 재량 수동, 자동 DCA(Phase 4 매수) 보류, 매수 상한·트랜치는 참고 표기 | 코드 변경 없음 |
| ③ 목표가 자동 반영 | (a) 없음 | GUIDE-1 ② 종결(retarget만) | 코드 변경 없음 |
| ④ 승인 없는 실보유 | (a) 편입하되 매도선 없음·경고 | **좁힘**: 페이퍼 포지션을 만들지 않는 `EnrollBlocked`(편입 보류) — 목표가 0 센티널은 마킹 사다리(매도선 0원 즉시 체결·정리) 오작동 위험. guide-orders 매 실행 "⚠ 가이드 밖(편입 보류)" + 신규 발견 시 P1 1회(`enroll_blocked` 이벤트), 승인되면 다음 08:40 자동 편입 | /paper 가이드 표에 시작가·수익률 행이 생기지 않음 — 운영자 보고 완료 |
| ⑤ 매도 예약 | "다음 매도선 1개만이 의도" | P-19 ① ❌ 기각 | — |
| ⑥ 전량 정리 | "내가 직접" | P-20 ⑤ EXEC-13 ❌ 기각 | — |
- 현 실보유 7종은 전부 승인·편입 완료라 ④의 행동 변화 없음(9/4 08:40 "유지 7" 확인). 테스트 +3.

## 2. SCREEN-1 — 소스 실측 (`docs/research/2026-09-03-screen1-status-audit-sources.md`)
- 원칙: 금지 1(엔드포인트 추측 금지) — 공식 문서로 존재 확인 → 기존 어댑터 실호출 → 뉴스로 확인된 양성 대조군에서 값 분기 관측.
- **KIS `inquire-price`(`KisClient.quote_price`)**: `mang_issu_cls_code`=="Y" 관리종목(온타이드·형지글로벌 8/12 신규 지정),
  `iscd_stat_cls_code` 51 관리(거래 중)·**58 매매거래정지**(이오플로우·테라사이언스 등 7종)·00+None+현재가 0 상장폐지
  (노블엠앤비·선샤인푸드). ⚠ `temp_stop_yn`은 정지 종목도 'N' — 지표 아님(실측 없이 구현했으면 틀렸을 지점).
  공식 GitHub 예제엔 output 필드 설명 없음(README가 API 포털로 안내) → 의미는 관측으로 확정.
- **DART `accnutAdtorNmNdAdtOpinion`**(가이드 DS002 #13, apiId 2020009): 응답 6행 = 당기·전기·전전기 × 2(연결/별도 추정,
  구분 필드 없음 — CJ대한통운 core_adt_matter만 상이), `bsns_year`는 "제57기 (당기)" 라벨, 어휘 적정의견·의견거절·None(adtor '-').
- KRX KIND 관리종목 페이지는 2회 서버 오류 → 미확인(지정 사유·일자 필요 시 후속).
- 결재 질문: (a) R4 탈락 필터 편입 vs (b) 표기만 → 운영자 **(a)**.

## 3. 구현 (policy v2.16)
- `collectors/status.py`: `StatusStore`(`data/status.sqlite` — `kis_status` UNIQUE(symbol, as_of)·`audit_opinions`
  UNIQUE(symbol, fy, reprt_code, rcept_no, row_idx)·`audit_fetch_log`), `collect_kis_status`(같은 날 관측분 무호출 스킵,
  **4스레드 + 시간 예산 12분**, 토큰 직렬 워밍), `classify_kis`(순수), `flagged_summary`, CLI `python -m trading.collectors.status`.
- `collectors/audit.py`: `collect_audit_opinions`(같은 접수번호 무시·정정은 새 버전), `current_opinion`(최신 접수분 당기 행 전부,
  ≠적정의견 → adverse, adtor '-' → unaudited, 없음 → missing), CLI. `DartClient.audit_opinion` 신설.
- `screen/rules.py status_filter`: 상폐 의심·관리·정지·감사의견 비적정 → 탈락 사유(관측 근거 명시), 데이터 부재·판독 불가·당기 결측 →
  종목별 `unapplied` 고지(추측 탈락 금지). `UNAPPLIED_V1`에서 "감사의견·관리종목(소스 미확보)" 제거.
  `screen/run.py load_status_inputs`(신선도 7일)·`run_screen(kis_flags, audits)`; 스크리너 CLI·`weekly_digest` 동일 입력.
- `run.py`: eod-v3 끝 `status-v3` · weekly-v3 `audit`(owner-equity 다음) · 수동 라운드 `status-v3`·`audit-v3`.
- 파서 갭(전수 실측에서 발견): "(당기)" 표기 없이 "제10기·제9기·제8기"만 쓰는 회사(샘표식품 등) → `mark_current`가 최상위
  기수를 당기로 판독, `current_opinion`은 저장값이 아니라 라벨을 매번 재판독(append-only 행 불변·재수집 불요).
  교정 전 당기 없음 126 → 교정 후 68(전부 adtor '-' 미제출).

## 4. 실측·검증
- 전수(9/3 16:50~17:05): KIS 2,669콜 ≈ 0.16s/콜 → 관리 167·정지 127·상폐 의심 2. DART 2,669콜 16,014행 → 적정 2,525·
  비적정 49(의견거절 47·**한정의견 2** — 미관측 어휘가 ≠적정의견 규칙에 잡힘)·당기 없음 68. 보유 7종 전부 정상·적정.
- R4(스크리너 재실행 9/3 17:04·9/4 10:30): **다른 게이트 전부 통과했는데 SCREEN-1만으로 탈락 10종**(SHD·조일알미늄·동원수산·
  제이엠아이·에스앤더블류·다산솔루에타·패션플랫폼·아틀라스링크·세림B&G·원티드랩(한정)) — 전부 심사 원장 무판정.
  통과+당기 결측 ⚠ 8종(원장 hold 1: 삼원강재). 사유 부착 레코드: 관리 167·정지 127·비적정 49·상폐 1.
- 자동 체인: 9/3 18:00 eod-v3 13분(status-v3 호출 0 — 선행 수집분 스킵) · 9/4 08:40 guide-orders 유지 7.
- 테스트 +13(결재 3·수집기 7·필터 3), pytest 전체·mypy strict(215 files) 통과.

## 5. 후속·미결
- **보유 종목 상태 전이 알림**(정상→관리·정지·비적정): P0(veto 창) vs P1(실행 보고 꼬리) — 결재 대기(CURRENT A-4, 세션 권고 P1).
- `mrkt_warn_cls_code` '00' 외 값(투자주의·경고·위험)·`sltr_yn`·`short_over_yn` 'Y'는 미관측 — 관측되면 어휘 박제 후 표기 검토.
- KIND(지정 사유·일자) 실측은 필요 시. DART 013(빈 응답) 27종은 `audit_fetch_log` empty로 남김(corp 미등재 아님 — 원인 미확인).
- 관찰: 9/4 18:00 eod-v3 `status-v3` 첫 실호출(4스레드, 체인 ≤16분 목표) · 9/5 09:30 weekly-v3 `audit`·v2.16 필터 첫 cron 적용.
