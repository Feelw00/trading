# 2026-09-04 — P-19 ④ KRX 업종 태깅 '업종 없음' 129종 박제(`source="none"`)

> 운영자 지시: "P-19 ④ 태깅 영구 스킵 129종 박제 진행해" (16:00 KST). CURRENT B-5 항목 — 결재 불요(데이터 수집 효율).

## 1. 문제
- eod-v3 체인의 `sectors.main` → `classify_krx`가 미태깅 종목에 매일 KIS `inquire-price`를 호출. 업종명(`bstp_kor_isnm`)이 비면
  **행을 남기지 않고 스킵**(일시 장애의 영구화 방지 설계) → 같은 129종을 매일 재시도 = 일 129콜 낭비.
- 로그(`.runtime/logs/cron/eod-v3.log`): as_of 8/31·9/1·9/2 cron 3회 연속 `대상 129 · 태깅 0 · 스킵 129`. 8/31 P-18 수동 실행은 130.

## 2. 재시도 소진 확인 + 실체 조사
- 미태깅 129 = 최신 시세일(9/2) 전 종목 2,873 − `kis-bstp-v1` 행 보유 종목. 시장별 **KONEX 108 · KOSDAQ 17 · KOSPI 4**.
  - KONEX 108(전 KONEX 종목 = 108 → KONEX 전수), 외국기업 950번대 10(엑세스바이오·코오롱티슈진·프레스티지바이오파마 등),
    신형 6자리 코드 3(`0070X0`·`0203K0`·`0001A0`), 정규 코스피/코스닥 10(NH농우바이오·아시아종묘·동원수산·신라교역·맵스리얼티·
    지에스이·신라섬유·부방·에이전트AI·디티씨).
  - PROPOSALS의 "초소형" 표현은 부정확 — 코오롱티슈진 1.5조·맵스리얼티 6,700억 포함. 본질은 **KRX 업종지수 밖**(KONEX·외국기업·
    농업/수산/부동산 등).
- KIS 실호출 5종(140610 엔솔바이오·950160 코오롱티슈진·0001A0 덕양에너젠·094800 맵스리얼티·054050 NH농우바이오, 대조군 005930):
  전부 **rt_cd=0 정상 응답 79~80필드**, `stck_prpr` 있음, `bstp_kor_isnm`은 **null 또는 " "**. 대조군은 '전기·전자'.
  → 일시 장애가 아니라 소스에 업종이 없다. 영구 스킵 타당.

## 3. 구현 (`src/trading/sectors.py`)
| 항목 | 내용 |
|---|---|
| `KRX_NONE_SOURCE = "none"` | 박제 소스. `stock_sectors`에 sector='unclassified' 행(confidence 0). fins의 `record_attempt(..., "none", ...)` 선례와 같은 의미("소스가 정상 응답으로 무자료") |
| `classify_krx(..., pin_source=KRX_NONE_SOURCE)` | **정상 응답 마커 = `stck_prpr` 존재**. 업종명 공백 ∧ 마커 있음 → 박제 · 예외/빈 응답(마커 없음) → 종전대로 행 없이 스킵(재시도). `ClassifySummary.pinned` 추가 |
| `krx_todo(store, names, retry_pinned=False)` | 대상 선정 순수 함수: 기본 = 전 종목 − `kis-bstp-v1` − `none` · `--retry-pinned` = `none` − `kis-bstp-v1`(성공분 제외) · 미상장(names 밖) 제외 |
| `main()` | `--retry-pinned` 플래그. 로그 "대상 N · 태깅 · **박제(업종 없음→none)** · 스킵(재시도 예정)" / "KRX 업종 태깅 최신 — 신규 대상 없음" |
- 신규 상장분에도 같은 규칙 자동 적용(정상 응답+업종 없음 = 즉시 박제). 첫날 업종 미배정 같은 드문 경우는 `--retry-pinned`로 복구 —
  성공하면 `kis-bstp-v1` 행이 first-wins, `none` 행은 append-only로 남되 무해(`sector_map`은 unclassified 제외).
- 소비자 무영향: `sector_map(KRX_SOURCE)`·`sector_names(KRX_SOURCE)`·산업 상대 위치·화이트리스트 멤버는 `kis-bstp-v1`만 읽는다.
  `names_any()`(표시 폴백)에 129종 이름이 추가로 잡힘(무해·오히려 이름 커버 확대).

## 4. 실행·검증
- 15:57 수동 `python -m trading.sectors`: `대상 129 · 태깅 0 · 박제 129 · 스킵 0`(24초, KIS 129콜 = 마지막 재시도). DB `none`·unclassified
  129행(as_of 20260902). 잔여 미태깅 0. 재실행: "신규 대상 없음"·1초·KIS 콜 0.
- 테스트: `_FakeKis`에 `blank`(정상 응답·업종 공백) 모양 추가, 기존 테스트 확장 + 신규 3(pin_source=None 종전 동작·`krx_todo` 모드·
  박제 후 재태깅 first-wins). pytest 전체 721 통과 · mypy 217 clean.
- `screen()`은 읽기 전용이라 수동 실행이 후보 DB에 쓰지 않음(확인).

## 5. 관찰·후속
- 9/4 18:00 eod-v3: 9/3 EOD 신규 상장이 없으면 "KRX 업종 태깅 최신 — 신규 대상 없음" 기대(콜 0).
- 옵션(미결·결재거리 아님): weekly-v3에 `--retry-pinned` 주간 1회(129콜/주)를 넣어 신형 코드 종목의 업종 배정을 자동 포착할지 —
  현재는 수동. 필요 시 PROPOSALS에 추가.
- "129" 숫자는 9/2 시세일 기준. KONEX 신규 상장·950 외국기업 추가 시 자동 증가.
