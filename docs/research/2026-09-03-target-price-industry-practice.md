# 투자업계의 목표주가 산출 관행 조사 — 본 시스템(policy v2.13) 대조 (2026-09-03)

> 성격: **운영자 요청 조사 기록**(웹 검색 기준, 출처 말미). 시스템 정책(POLICY_PARAMS)이나 판단
> 로직에 인코딩된 것이 아니다. 1차 문서를 직접 열지 못하고 검색 요약·2차 인용으로만 확인한 항목은
> **[2차]**로 표시했다. 숫자는 출처의 표본·기간에 종속된다.
>
> 운영자 질문: "지금 섹터 중앙으로 목표가를 잡고 있는데, 일반적으로 투자업계에서 목표가를 어떻게
> 산출하는지". ※ 시스템은 2026-09-03 결재(v2.13)로 섹터 중앙 PBR을 폐기하고 **자기 역사 5년 일별 PBR
> 밴드 중앙**을 앵커로 쓰고 있다. 아래 대조는 v2.13 기준.

## 0. 요약

| 질문 | 업계 답 |
|---|---|
| 목표주가는 무엇으로 구하나 | **목표 멀티플 × 미래 펀더멘털**(상대가치)이 압도적. 이익형은 PER × 12개월 선행 EPS, 자산형·금융·경기민감은 PBR × BPS, 지주·복합은 SOTP/NAV − 할인. 절대가치(DCF·RIM)는 소수(미국 셀사이드 12.8%, 영국 약 절반) |
| 목표 멀티플의 근거는 | ① **자기 과거 평균·밴드** ② 동종·업종 평균 ③ 이론 정당 멀티플(정당 PBR = (ROE − g) ÷ (COE − g)) 중 하나 또는 조합. 배수 선택은 애널리스트 재량 |
| 시간 지평 | 셀사이드 = 12개월 목표주가. 바이사이드·독립 리서치(모닝스타)는 지평 없는 **내재가치 + 안전마진** |
| 얼마나 맞나 | 미국: 12개월 말 도달 38%(기간 중 한 번이라도 64%), 낙관 편향 약 +15%p, 절대 오차 45%. 한국: 달성률 **19%**, 예상·실제 수익률 괴리 약 30%, 매수 의견 93~95% |
| 본 시스템은 | "자기 과거 평균 PBR × BPS" = 업계 표준 방식 중 하나이며 자산형 종목에 맞는 선택. 빠진 것은 **ROE 정당성 검증·이익 기반 교차 검증·목표가 도달률 계측**. 안전마진 +30%는 Graham(내재가치의 2/3)보다 완화, 모닝스타 Medium 불확실성(30%)과 같은 수준 |

## 1. 셀사이드 표준 산식

### 1.1 이익 멀티플 — 목표주가 = 목표 PER × 12개월 선행 EPS
- 가장 흔한 방식. 국내 칼럼(차호중)은 "해당 종목이 과거 특정 기간 적용받았던 PER의 평균값"으로 구하는 것이
  "애널리스트들이 가장 많이 이용하는 방식 중 하나"라 명시하고, 업종 평균 PER 방식은 글로벌 경기 변동에
  휘둘린다고 지적한다. [^차호중]
- 분모는 과거 실적이 아니라 **12개월 선행(forward) EPS 컨센서스**를 쓰는 것이 관행. [^kcie-per]
- 국내 실무는 PER과 EV/EBITDA를 가장 높게 활용(국회도서관 소장 논문 요약 **[2차]**). [^nanet]

### 1.2 자산 멀티플 — 목표주가 = 목표 PBR × BPS
- **정당 PBR(justified P/B) = (ROE − g) ÷ (r − g)** — Gordon 모형에서 유도(CFA Level II 표준, 다모다란).
  ROE > 자기자본비용(r)이면 PBR > 1이 정당하고, ROE < r이면 PBR < 1이 정상이다. [^cfa-pb] [^damodaran]
- 국내 은행 리서치가 전형: 지속가능 ROE·성장률을 가정해 적정 PBR을 구하고 BPS에 곱한다. 예: 목표 ROE 14%·g 3% →
  적정 PBR 1.57배(**[2차]**), 국내 금융그룹 COE 12~15% 기준 적정 PBR 0.7~0.9배, 지속가능 ROE 8% → 약 0.8배.
  하나증권 은행 산업 리포트(2025-05)는 "1차 target 평균 PBR 약 0.6배(KB금융 0.8배)". [^bank]
- 시사점: **저PBR이 곧 저평가는 아니다.** ROE가 COE를 밑도는 종목은 PBR 0.3~0.5가 정당 수준일 수 있다.

### 1.3 밴드 차트(자기 역사 멀티플)
- PER/PBR 밴드 차트는 종목이 역사적으로 오간 배수의 상·하단을 보여 주는 표준 도구다(예: 삼성전자 PER
  8.2~13.2배, PBR 1.1~1.7배 관측 **[2차]**). 목표 멀티플을 "과거 평균" 또는 "밴드 상단/하단"에서 고르는
  관행의 근거. [^band]

### 1.4 SOTP / NAV — 지주·복합기업
- 자회사 지분·사업부 가치를 합산(NAV)한 뒤 **할인율**을 적용. 실제 사례: 한화 목표주가에 NAV 67% 할인,
  두산 50% 할인(대신증권 리포트 검색 요약 **[2차]**). 할인의 근거는 복합기업·지주 구조 디스카운트이며 정책
  변화에 따라 축소 여지를 논한다. [^sotp]
- 본 시스템의 KG케미칼 veto(지주할인)와 같은 문제를 업계는 "할인율"이라는 파라미터로 다룬다.

### 1.5 절대가치 — DCF · RIM
- 모닝스타: 공정가치(FVE)는 DCF로 구하며 경제적 해자(ROIC > WACC 지속성)가 명시적으로 들어간다. [^ms-method]
- RIM(잔여이익모델): 자기자본 + Σ(ROE − COE) × 자본의 현재가치. 시장 상황과 무관한 절대가치라는 장점이
  있으나, 과거 국내 리포트에서는 실제 적용이 드물고 상대가치가 주류였다는 관찰. [^rim]
- 미국 셀사이드(Institutional Investor 올아메리칸 애널리스트) 리포트에서 DCF 사용은 **12.8%**, 나머지는
  이익·현금흐름 멀티플이 압도적. 방법론과 정확도·시장 반응 사이의 상관은 없었다. [^asquith]
- 영국: 애널리스트는 PE 또는 다기간 DCF 중 하나를 지배 모형으로 택하며 업종별로 다르다(음료 업종은
  비교가치 비중이 높음). 2002~04 표본에서 PE가 지배 모형인 비율 53%. [^demirakos04]

### 1.6 경기민감주 — 정상화 이익·through-cycle 멀티플
- 현재 이익 대신 **정상화(mid-cycle) 이익**을 쓴다: 7~10년 평균 이익 또는 사이클 한 바퀴의 평균 마진 ×
  정상화 매출. 여기에 시장이 정상화 이익에 역사적으로 부여한 through-cycle 멀티플을 곱한다.
- 바닥 국면에서는 이익이 눌려 PER이 무의미하므로 **PBR(장부가·대체원가 대비)**로 진입 판단 — 경기민감주의
  최적 진입은 장부가 근처·이하에서 나온다는 관행. [^cyclical]
- McKinsey "How to value cyclical companies"는 접속 실패로 직접 확인 못 함(**미검증**).

## 2. 투자의견 · 시간 지평 · 국내 규제

- **12개월 목표주가**가 셀사이드 표준 지평. 투자의견은 목표주가 상승 여력으로 정의하되 증권사마다 다르다:
  매수 = +15% 이상, 중립 = −15~+15%, 비중축소 = −15% 이하가 흔한 틀이고, 한화투자증권은 HOLD 구간을
  −10~+10%로 좁힌 사례. [^rating]
- **목표주가 괴리율 공시제**: 금감원·금융투자협회 규정 개정으로 **2017-09-01 시행**. 리포트에 목표주가와
  대상 기간 실제 주가의 괴리율을 수치로 표기해야 한다. 시행 후 관찰: 평균 괴리율 27.82%(2017-08) → 30.20%
  (2018-06), 매수 추천 88.5%, 매도 2% 미만 — "거품이 빠지지 않았다"는 평가. [^gap-law] [^gap-1y]
- **자본시장연구원(김준석) 25년·74만 건 분석**(서울신문 2026-05-04 보도): 목표주가 제시 보고서 100건 중
  95건이 상승 전제, **목표주가 달성률 19%**, 2015년 이후 예상수익률과 실제수익률 괴리 평균 약 30%.
  원인은 증권사 수익 기여·기업과의 관계 등 **이해상충**. 보고서의 69%가 시총 상위 200개 기업에 집중,
  발간 대상은 전체 상장사의 30%. [^kcmi]

## 3. 바이사이드 · 독립 리서치의 목표가(내재가치 + 안전마진)

- **Graham / Tweedy Browne**: 보수적으로 추정한 내재가치(인수 가치 또는 자산·현금흐름 담보가치)의
  **2/3 이하**에서 매수. 내재가치 추정이 맞다면 주가가 50% 올라도 고평가가 아니라는 것이 안전마진의 논리.
  Tweedy Browne은 장기 수익의 더 큰 몫이 사업 성장보다 **매수 시 안전마진**에서 나왔다고 진술. [^tweedy]
- **모닝스타**: DCF 공정가치(FVE)에 **불확실성 등급별 안전마진**을 곱해 별점을 만든다. 12개월 목표가 아니라
  지평 없는 내재가치. 5★(매수 고려) 요구 할인 / 1★(매도 고려) 프리미엄: [^ms-uncert]

| 불확실성 | 5★ 할인 | 1★ 프리미엄 |
|---|---|---|
| Low | 20% | 25% |
| Medium | 30% | 35% |
| High | 40% | 55% |
| Very High | 50% | 75% |
| Extreme | 75% | 300% |

## 4. 실증 — 목표주가는 얼마나 맞나

| 연구 | 표본 | 결과 |
|---|---|---|
| Bradshaw·Brown·Huang (RAST 2013) | 미국 12개월 목표주가, 2000~2009 | 지평 말 도달 **38%**, 기간 중 한 번이라도 **64%**. 목표가 내재 수익률이 실제보다 평균 **15%p** 높음(낙관). 절대 오차 평균 **45%**. 애널리스트 간 지속적 실력 차는 통계적으로는 있으나 경제적으로 미미 [^bradshaw] |
| Demirakos·Strong·Walker (EAR 2010) | 영국 490건, 2002~04 | 표면상 PE가 DCF보다 정확하나, 평가 난이도·모형 선택 편향을 통제하면 DCF가 우세. 즉 "쉬운 종목엔 PE, 어려운 종목엔 DCF"가 선택되고 있었다 [^demirakos10] |
| Asquith·Mikhail·Au (JFE 2005) | 미국 올아메리칸 리포트 | DCF 12.8%. 방법론과 정확도·시장 반응 무관 [^asquith] |
| 자본시장연구원 (2026 보도) | 한국 25년 74만 건 | 달성률 19%, 괴리 30%, 매수 95% [^kcmi] |

읽는 법: 업계 표준 산식을 그대로 써도 12개월 내 도달은 절반 미만이고 체계적 낙관이 있다. 산식보다 **낙관
편향(이해상충)과 시간 지평**이 오차의 주범이다. 이해상충이 없고 지평이 긴 시스템은 같은 산식으로도 더
나은 도달률을 기대할 수 있지만, 그것은 **측정해야 아는 것**이다(§6 ③).

## 5. 본 시스템(v2.13) 대조

| 항목 | 업계 관행 | 본 시스템 | 평가 |
|---|---|---|---|
| 앵커 | 목표 멀티플 × 펀더멘털 | **자기 역사 5년 일별 PBR 중앙 × 연간 자본총계** | 표준 방식 중 "과거 평균 멀티플"에 해당. 자산형·바닥 국면 종목에 PBR을 쓰는 관행과 정합 |
| 비교군 | 자기 역사 / 업종 평균 / 정당 배수 | 자기 역사만(업종 평균은 버킷 이질성으로 폐기) | 정합. 단 업계는 **큐레이션된 피어**로 업종 비교를 살린다 — KRX 버킷이 문제였지 피어 비교 자체가 무효는 아님 |
| 분모 시점 | 12개월 **선행** EPS/BPS | 최근 **연간** 자본총계(트레일링, 4/1 적용) | 보수적(이익 누적으로 BPS가 늘면 목표가 과소). 예측 금지 원칙의 의도적 선택 |
| ROE 정당성 | 정당 PBR = (ROE − g) ÷ (COE − g) | **없음**(코어 8축에 ROE 하한·이익방향·ROE 변동계수는 있음) | **갭.** ROE 3~5% 종목이 과거 PBR 0.8을 회복한다는 가정은 근거가 약하다. 정당 PBR을 **상한 캡**으로 두는 것이 업계식 보완 |
| 이익 교차 | PER·EV/EBITDA 병행 | 없음(위험조정수익률 = 1/PER ÷ (1+CV) 표시만) | **갭.** 자기 역사 PER 중앙 × 연간 EPS를 두 번째 목표로 두고 min() 또는 병기 |
| 경기민감 | 정상화 이익 · through-cycle 멀티플 | R3 온도계 국면 + 5년 밴드(사이클 한 바퀴) | 부분 정합. 밴드 중앙은 사실상 through-cycle PBR |
| 지주·복합 | NAV − 할인율 | veto 태그(지주할인·복합할인) | 업계는 파라미터, 우리는 배제. 현 단계 보수적 선택으로 타당 |
| 안전마진 | 셀사이드 BUY +15~20% / Graham 33%+ / 모닝스타 20~75% | **+30%**(등록·승인 노출 하한) | 정합. Graham 2/3 기준(+50%)보다 완화, 모닝스타 Medium과 동일 |
| 시간 지평 | 12개월 | 없음(3년 미수렴 시 청산·2년 정체 경고) | 바이사이드형. 낙관 편향 원인 하나(단기 지평) 제거 |
| 정확도 계측 | 괴리율 공시(규제) | **미측정** | **갭.** 등록 목표 도달률·기간을 박제해야 산식 튜닝 근거가 생긴다 |

## 6. 시사점 · 후보 (미구현 — PROPOSALS·결재 대상)

1. **정당 PBR 상한 캡**: 목표 PBR = min(자기 역사 중앙, (ROE − g) ÷ (COE − g)). ROE는 5년 중앙, g·COE는 정책
   파라미터(부록 B 결재 필요 — 국내 금융 COE 12~15%가 참고치, 비금융 소형주는 더 높을 수 있음). 저ROE
   종목의 과대 목표를 억제. 효과 예상: 오공(ROE 낮음)·삼보판지류의 여력 축소, 아이퀘스트(ROE 높음) 유지.
2. **이익 교차 목표**: 목표가₂ = 자기 역사 5년 PER 중앙 × 최근 연간 EPS. 표시는 병기, 게이트는 min() —
   자산은 싼데 이익이 안 나는 종목(가치 함정) 거름.
3. **목표가 도달률 계측(R7 확장)**: 페이퍼 등록 목표에 대해 도달 여부·소요일·최대 접근률을 박제하고 분기
   R7에서 업계 벤치마크(38%/19%)와 비교. 산식 변경(v2.12→v2.13)의 효과도 이것으로만 검증 가능.
4. **불확실성 연동 안전마진**(모닝스타식): ROE 변동계수·관측 연수로 불확실성 등급을 매겨 하한을
   +30/40/50%로 차등 — 현재 단일 30%.
5. **분모 정밀화**: 트레일링 연간 자본총계 유지(예측 금지)하되, 반기 BS 반영 여부는 밴드의 과거점과 같은
   잣대 원칙 안에서만(현재점만 바꾸면 편향).
6. 피어 비교 부활은 **큐레이션 피어 그룹**(PIVOT-10 화이트리스트 방식)이 있을 때만 — KRX 버킷 재사용 금지.

## 출처 (열람일 2026-09-03)

[^차호중]: 차호중, "증권사 목표주가에 대한 이해" (다음/재테크 칼럼) — https://v.daum.net/v/76CFB6NfWB (직접 열람: 과거 평균 PER 최다·업종 평균 PER 약점)
[^kcie-per]: 금융투자교육원 KCIE, "저 PER주라고 사니? 나는 다 따져보고 산다" — https://www.kcie.or.kr/mobile/guide/series/3/60/web_view?series_idx=60&content_idx=1216 (12개월 선행 PER 관행) [2차]
[^nanet]: 국회도서관 소장 학위논문(국내 실무 PER·EV/EBITDA 활용) — https://dl.nanet.go.kr/SearchDetailView.do?cn=KDMT1200474745 [2차]
[^cfa-pb]: AnalystPrep, "Price Multiples Based on Forecasted Fundamentals" (CFA L2) — https://analystprep.com/study-notes/cfa-level-2/price-multiples-based-on-forecasted-fundamentals/ ; Breaking Down Finance — https://breakingdownfinance.com/finance-topics/equity-valuation/justified-price-to-book-multiple/ [2차]
[^damodaran]: Damodaran, "Determinants of Price to Book Ratios" — https://pages.stern.nyu.edu/~adamodar/New_Home_Page/invfables/pbvdeterminants.htm (직접 열람: PBV = (ROE − gn)/(ke − gn))
[^bank]: 하나증권 은행 산업 리포트(2025-05-29) "1차 target 평균 PBR 약 0.6배" — https://www.hanaw.com/download/research/FileServer/WEB/industry/industry/2025/05/28/Bank_20250529.pdf ; 블로터 "밸류업 허들 PBR 0.8배" — https://www.bloter.net/news/articleView.html?idxno=641535 ; 조세일보 "지속가능 ROE 12%" — https://www.payzon.co.kr/pzNews/14/546746/ [2차]
[^band]: 붉은말 블로그 "PBR 밴드 차트" — https://redhorseblog.co.kr/pbr-밴드-차트-저-pbr주-고점-확인하는-방법/ ; brunch "투자자라면 알아야 할 필수 지식" — https://brunch.co.kr/@leeeeesh/104 [2차]
[^sotp]: 대신증권 한화 Initiation(2026-04-02)·지주업 리포트(2025-10-27), 삼성증권 지주회사(2020-04-01) — alphasquare 미러 https://file.alphasquare.co.kr/media/pdfs/... ; 쿠키딜 "밸류에이션 방법론 NAV" — https://cookiedeal.io/blog/post/... [2차]
[^ms-method]: Morningstar Equity Research Methodology(PDF) — https://www.morningstar.com/content/dam/marketing/shared/research/methodology/705988Morningstar_Equity_Research_Methodology.pdf (PDF 본문 파싱 실패 — 검색 요약 [2차])
[^ms-uncert]: Morningstar, "An Introduction to the Morningstar Uncertainty Rating" — https://www.morningstar.com/stocks/an-introduction-morningstar-uncertainty-rating ; "How to Measure a Stock's Uncertainty" — https://www.morningstar.com/stocks/how-measure-stocks-uncertainty (직접 열람 403 — 검색 요약 [2차])
[^rim]: 헤럴드경제 "잔여이익(RI)으로 이해하는 낮은 PBR" — https://biz.heraldcorp.com/article/3327345 ; 한경용어사전 RIM — https://dic.hankyung.com/economy/view/?seq=5481 [2차]
[^asquith]: Asquith, Mikhail, Au (2005) "Information Content of Equity Analyst Reports", JFE 75 — https://www.sciencedirect.com/science/article/abs/pii/S0304405X04001369 [2차: DCF 12.8%]
[^demirakos04]: Demirakos, Strong, Walker (2004) "What Valuation Models Do Analysts Use?" — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3479656 [2차]
[^demirakos10]: Demirakos, Strong, Walker (2010) "Does Valuation Model Choice Affect Target Price Accuracy?", European Accounting Review 19(1) — https://ideas.repec.org/a/taf/euract/v19y2010i1p35-72.html (초록 직접 열람)
[^bradshaw]: Bradshaw, Brown, Huang (2013) "Do sell-side analysts exhibit differential target price forecasting ability?", Review of Accounting Studies 18(4) — https://ideas.repec.org/a/spr/reaccs/v18y2013i4d10.1007_s11142-012-9216-5.html (초록 직접 열람)
[^cyclical]: modelreef "Valuing Cyclical Stocks: Normalised Earnings, Mid-Cycle Margins" — https://modelreef.io/resources/articles/stock-valuation/valuing-cyclical-stocks-normalised-earnings-mid-cycle-margins-and-through-the-cycle-multiples ; Industrials IB "Through-Cycle Multiples" — https://ibinterviewquestions.com/guides/industrials-investment-banking/through-cycle-multiples-peak-trough-analysis ; Andersen "Valuing Cyclical Companies" — https://eg.andersen.com/valuing-cyclical-companies/ [2차]; McKinsey "How to value cyclical companies" — https://www.mckinsey.com/capabilities/strategy-and-corporate-finance/our-insights/how-to-value-cyclical-companies (접속 실패, 미검증)
[^rating]: 한화투자증권 "리서치 투자의견 등급기준 변경" — https://www.hanwhawm.com/main/bbs/content_print.cmd?nn_id=9&vc_bid=hpromise ; 아시아경제 "투자의견 등급 기준 제각각"(2009) — https://www.asiae.co.kr/article/2009081306392576199 [2차]
[^gap-law]: 서울경제 "내달부터 보고서에 실제주가와 괴리율 수치 공시 의무화" — https://www.sedaily.com/NewsView/1OETN3J15Y ; 인베스트조선(2019-01-29) — https://www.investchosun.com/site/data/html_dir/2019/01/29/2019012986001.html [2차]
[^gap-1y]: 아시아경제 "목표주가 괴리율 공시제, 허탕만 쳤다"(2018-06-15) — https://www.asiae.co.kr/article/2018061510563351077 (직접 열람: 27.82%→30.20%, 매수 88.5%)
[^kcmi]: 서울신문 "증권가 리포트 10건 중 9건 '매수'… 목표가 달성률은 19%"(2026-05-04, 자본시장연구원 김준석 분석 보도) — https://www.seoul.co.kr/news/economy/2026/05/04/20260504500081 (직접 열람)
[^tweedy]: Tweedy, Browne "Investment Philosophy" — https://www.tweedyfunds.com/investment-philosophy/ ; GuruFocus "Christopher Browne: Enduring Principles of Value Investing" — https://www.gurufocus.com/news/887429/... ; Wikipedia "Margin of safety (financial)" — https://en.wikipedia.org/wiki/Margin_of_safety_(financial) [2차]
