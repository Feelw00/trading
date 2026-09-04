# SCREEN-1 실측 — 관리종목·거래정지·감사의견 데이터 소스 (2026-09-03)

목적: 설계서 v0.3 R4 탈락 필터(하드) 중 미구현 3종(관리종목·거래정지·감사의견 비적정)의 데이터 소스를
**추측 없이** 확정한다(CLAUDE.md 금지 1). 방법 = 공식 문서로 엔드포인트·필드 존재 확인 → 기존 어댑터
(`collectors/kis.py`·`collectors/dart.py`)로 실호출 → 양성 대조군(뉴스로 확인된 관리종목·감사의견 거절 기업)에서
값이 갈리는지 관측. 판단·필터 적용은 이 문서의 범위 밖(결재 사항, POLICY_PARAMS §5 "미적용 필터 … 별도 결재").

## 1. 관리종목·거래정지 — KIS 주식현재가 시세(`inquire-price`, TR FHKST01010100) ✅ 실측 확정

기존 어댑터 `KisClient.quote_price`(업종 태깅에 이미 사용) 응답 output 80키 중 상태 필드가 존재한다.
공식 GitHub 예제(`examples_llm/domestic_stock/inquire_price`)엔 output 필드 설명이 없어(README가 API 포털로 안내)
**의미는 양성 대조군 관측으로 확정**했다.

| 종목 | 근거(뉴스, 2026-08-12·상반기) | iscd_stat_cls_code | mang_issu_cls_code | temp_stop_yn | sltr_yn | 현재가 |
|---|---|---|---|---|---|---|
| 삼성전자 005930 | 정상(KOSPI200) | 55 | N | N | N | 250,000 |
| 아이퀘스트·메타바이오메드·케이씨피드·케이디켐(코스닥 보유) | 정상 | 57 | N | N | N | — |
| 신세계I&C·동성케미컬·CJ대한통운(코스피 보유) | 정상 | 55 | N | N | N | — |
| 온타이드 005320 · 형지글로벌 308100 | 8/12 **신규 관리종목**(동전주·시총) | **51** | **Y** | N | N | 1,158 · 361 |
| 이오플로우·투비소프트·삼영이엔씨·테라사이언스·코스나인·한국유니온제약·제일엠앤에스 | 감사의견 미달 → **거래정지·상폐 심의** | **58** | **Y** | **N** | N | 정지 전 가격 |
| 노블엠앤비 106520 · 선샤인푸드 217620 | 상장폐지 결정(정리매매 보류, 시세 6/30·5/7 종료) | 00 | **None** | N | None | **0** |

확정 의미(관측):
- `mang_issu_cls_code == "Y"` = **관리종목**(정지 중 종목도 Y). `iscd_stat_cls_code == "51"` = 관리종목(거래 중).
- `iscd_stat_cls_code == "58"` = **매매거래정지**. ⚠ `temp_stop_yn`은 정지 종목에서도 'N' — 거래정지 지표가 **아니다**
  (장중 임시정지류로 추정, 미확정). 거래정지는 반드시 58로 읽는다.
- `iscd_stat_cls_code == "00"` ∧ `mang_issu_cls_code None` ∧ 현재가 0 = **상장폐지/무자료**(유니버스 제외 대상).
- 55/57은 정상 종목의 신용가능/증거금 구분으로 보이나(2차 출처·미확정) 필터와 무관 — 값 그대로 박제만.
- `mrkt_warn_cls_code`(시장경고)는 전부 '00' 관측 — 투자주의/경고/위험 코드값은 **미관측**(추측 금지, 값 박제만).
- `sltr_yn`(정리매매)·`short_over_yn`(단기과열)·`invt_caful_yn`(투자주의) 'N'만 관측 — 함께 박제.
- 시계열 없음(현재값). append-only 일일 스냅샷으로 이력을 만든다.

비용: 시세 유니버스 2,873종(9/1) × 0.12s ≈ **5.7분/일** — eod-v3 best-effort 단계로 수용 가능(수급 KIS 호출과 합산 주의).

## 2. 감사의견 — DART `accnutAdtorNmNdAdtOpinion.json` ✅ 공식 가이드 + 실측 확정

공식: 개발가이드 정기보고서 주요정보 #13 "회계감사인의 명칭 및 감사의견"(`/guide/detail.do?apiGrpCd=DS002&apiId=2020009`).
요청 `crtfc_key·corp_code·bsns_year(2015~)·reprt_code(11011 사업/11012 반기/11013 1Q/11014 3Q)`.
응답 필드(가이드): `rcept_no·corp_cls·corp_code·corp_name·bsns_year·adtor·adt_opinion·adt_reprt_spcmnt_matter(2019-12-08까지)·
emphs_matter·core_adt_matter(2019-12-09부터)` + 관측 `stlm_dt`. 감사의견 값 코드표는 가이드에 **없음** → 관측 어휘로 대체.

실측(11011, bsns_year=2025, 삼성전자·보유 7종·대조군 8종):
- 응답 **6행 = 당기·전기·전전기 × 2**. 2행은 필드가 완전 동일하거나(대부분) `core_adt_matter`만 다름(CJ대한통운:
  한 행에 "영업권의 손상평가" 추가) → **연결/별도 감사보고서로 추정, 구분 필드 없음**. 규칙: 당기 행 전부를 본다.
- `bsns_year`는 연도가 아니라 **"제57기 (당기)"** 라벨(공백·개행 변형 있음) → "(당기)" 포함 행이 요청 연도.
- 어휘(96행): **'적정의견' 69 · '의견거절' 18 · None 9**. None은 `adtor == '-'`(감사보고서 없음 — 삼영이엔씨 당기,
  한국유니온제약 전 행 = 사업보고서/감사보고서 미제출 상태). '한정의견'·'부적정의견'은 **미관측**(존재 추정, 값 매칭은
  "≠ '적정의견'"으로 보수 처리).
- 보유 7종 전부 당기 '적정의견'(삼정·대주·삼일·한영·삼덕·다산·삼일). 케이씨피드 rcept_no 20260814003179·메타바이오메드
  20260629000364는 3월 이후 접수번호 — **정정보고서로 추정**(API가 최신 접수분을 돌려줌, 미확정). 재수집 시 갱신 필요.
- 코스나인 전기 `emphs_matter` = "주권 매매거래 정지 및 상장폐지 사유 발생 등 … 횡령·배임" — 강조사항 텍스트도 신호가 되나
  판정 입력은 `adt_opinion`만(텍스트 판정은 LLM 서술 영역).

비용: 재무 유니버스 2,669종 × 1콜/연(사업보고서 후, 정정 대비 분기 재수집) — 한도 20,000/일 대비 무시 가능.

## 3. KRX KIND(관리종목 지정 현황·매매거래정지 현황) ⏸ 미확인
`kind.krx.co.kr/investwarn/adminissue.do`는 2회 모두 서버 "페이지 오류". 거래정지 현황 페이지는 정적 HTML에 데이터 없음
(JS 로드·EXCEL 다운로드 버튼 존재). **지정 사유·지정일**(KIS엔 없음)이 필요해지면 후속 실측. 종목 단위 판정엔 KIS로 충분.

## 4. 제안 설계 → **운영자 결재 (a)·구현 완료(같은 날 오후, policy v2.16)** — 아래 1~3 구현, 4는 미결
1. `collectors/status.py` — KIS 일일 상태 스냅샷 → `data/status.sqlite`(append-only: symbol·as_of·iscd_stat_cls_code·
   mang_issu_cls_code·mrkt_warn_cls_code·sltr_yn·short_over_yn·invt_caful_yn·last_price·source="kis-inquire-price"·fetched_at).
   eod-v3 best-effort 단계(실패해도 체인 계속, P1). 상장폐지(00·None·0원)는 `delisted_suspect` 플래그.
2. `collectors/audit.py` — DART 감사의견 연간 → `data/fins.sqlite` 신규 `audit_opinions`(symbol·fy·reprt_code·row_idx·
   adtor·adt_opinion·emphs_matter·core_adt_matter·rcept_no·stlm_dt·fetched_at). weekly-v3 지배주주지분 단계 옆, rcept_no 변경 시 새 버전.
3. **R4 하드 필터 판정(결재 대상, 순수 코드)**: 관리종목(mang Y ∨ 51) → 탈락 · 거래정지(58) → 탈락 · 감사의견 당기 행 중
   하나라도 ≠ '적정의견' → 탈락 · 당기 결측(adtor '-') → **미적용 표기 + ⚠**(추측 탈락 금지) · 상장폐지 의심 → 유니버스 제외.
4. **보유 종목 상태 전이**(정상→관리/정지, 감사의견 비적정) → §7 P0 — 별도 결재(청산 경로 연결 여부).
