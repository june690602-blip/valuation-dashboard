# 투자지표 — 기업 가치평가 대시보드

기본적 분석으로 "이 기업의 주가가 지금 적정한가, 아니라면 왜인가"를 보여주는 Streamlit 대시보드.
종목 하나만 입력하면 재무제표·주가·업종 데이터를 자동 수집해 분석합니다. (한국 + 미국 지원)

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

첫 조회는 피어 데이터 수집 때문에 수십 초 걸릴 수 있습니다 (이후는 캐시).
API 키가 전혀 필요 없습니다.

## 무엇을 보여주나

| 탭 | 내용 |
|---|---|
| ① 요약·판정 | 3가지 방법 삼각측량 적정주가 vs 현재가 (불릿 차트), 5개 카테고리 업종 상대 점수 (레이더), 규칙 기반 자동 해설 |
| ② 주가차트 | 종가+이동평균(20/60/120)+거래량, 기간 버튼, 52주 위치, 지수 대비 상대성과 |
| ③ 밸류에이션 | PER·PBR·PSR·EV/EBITDA·P/FCF·배당·PEG — 업종 중앙값·자기 5년 밴드와 비교, 5년 PER/PBR 밴드 차트 |
| ④ 재무 분석 | 매출·이익·마진 추이, 부채비율·유동비율, 현금흐름, 연간 재무제표 원본 |
| ⑤ 업종 비교 | 피어 지표 테이블, PER×ROE 산점도(싸고 좋은 사분면), **업종 내 저평가·우량 랭킹**, 지표별 백분위 상세 |
| ⑥ 자본비용(WACC) | 주간수익률 회귀 베타(β_L) → 하마다 언레버링(β_U) → 영업위험만의 자본비용(k_U) → 재무위험 프리미엄 → k_e·k_d → WACC, ROIC vs WACC 스프레드 |
| ⑦ 백테스트 | 자기 PER/PBR 백분위 신호의 예측력 검증 — 구간별 미래수익, 백분위↔미래수익 순위상관, 타이밍 전략 vs 보유 vs 지수 |
| ⑧ 주요뉴스 | Google News 헤드라인 + **Gemini AI 뉴스 분석**(감성·핵심이슈·촉매·리스크) |
| ⑨ 종합 투자평가(AI) | 기본적 분석 + 뉴스를 **Gemini가 종합** → 강세/약세 논거·스탠스 평가 |

**AI 기능(⑧⑨ + 업종분류)**: Gemini 키가 있으면 활성화됩니다. 키가 없으면 뉴스 헤드라인·차트는 그대로 보이고 AI 생성 부분만 비활성 안내로 대체됩니다. **키가 있으면 부정확한 KRX 업종분류(예: 삼성전자=통신장비) 대신 AI가 업종·경쟁사를 다시 선정**해 피어 비교가 정확해집니다.

> 처음 쓰는 분은 [docs/사용설명서.md](docs/사용설명서.md)를 보세요 (CPA 1차 수준 눈높이). 앱 안에도 "❓ 사용법" 도움말이 있습니다.

## 방법론 요약

- **적정주가 3방법**: ① 업종 피어 중앙값 멀티플(PER/PBR/EV/EBITDA, 적자기업은 PSR 보강)
  ② 자기 5년 PER/PBR 밴드 25~75분위 ③ RIM 간이형 (`V = B + B(ROE−r)·w/(1+r−w)`, r은 CAPM k_e 기본)
- **판정**: 세 방법 평균 괴리율로 5단계 (±10%/±30% 기준) + 방법 간 편차로 신뢰도 표시
- **자본비용**: β_L은 5년 주간수익률 OLS(KOSPI/KOSDAQ/S&P500), `β_U = β_L / (1+(1−t)·D/E)` (하마다),
  `k_U = R_f + β_U·MRP`, `WACC = k_e·E/V + k_d(1−t)·D/V`
- **점수화**: 업종 피어 대비 백분위(0~100), 지표별 유효범위 밖 값(음수 PBR 등)은 제외
- **자동 해설**: 15개 규칙으로 저평가 vs 밸류트랩 구분 근거 제시 (역성장+저PER → 경고 등)
- **예외 처리**: 적자기업(PER/PEG/RIM 스킵), 금융업(EV/EBITDA·부채비율·WACC 마스킹),
  자사주 매입으로 장부자본 왜곡(RIM 스킵), 상장기간 부족(베타 β=1 가정)

## 데이터 소스 (전부 무료, 키 불필요)

| 데이터 | 한국 | 미국 |
|---|---|---|
| 연간 재무제표 (우선) | **OpenDART 공시 원본** (키 있을 때, ~6개년 연결) | — |
| 재무제표(보완)·주가 | yfinance (`005930.KS`) | yfinance |
| 시총·주식수·상장목록·업종분류 | FinanceDataReader (KRX) | 위키피디아 S&P500 (GICS) |
| 공식 PER/PBR/EPS/BPS/배당 | 네이버 금융 API | Yahoo Finance |

| 뉴스 | Google News RSS (무키) + yfinance.news 폴백 | 좌동 |
| AI 분석·업종분류 | **Gemini** (무료 키) | 좌동 |

**API 키 설정** (둘 다 무료, 선택): `.streamlit/secrets.toml`에 아래를 넣거나 동명 환경변수로 설정.
- `OPENDART_API_KEY` — [opendart.fss.or.kr](https://opendart.fss.or.kr), 한국 공시 원본 재무. 없으면 yfinance 폴백.
- `GEMINI_API_KEY` — [aistudio.google.com](https://aistudio.google.com), AI 뉴스분석·업종분류·투자평가. 없으면 AI 부분만 비활성.
- `GEMINI_MODEL` (선택) — 모델 고정용. 비우면 사용 가능한 flash 계열을 자동 선택.

키가 없어도 앱은 그대로 동작합니다. 키는 `.gitignore`로 커밋에서 제외됩니다.

> pykrx는 KRX 로그인 계정이 필요해져 사용하지 않습니다. 캐시는 `data/cache/`에 저장됩니다
> (지표 12~24시간). 사이드바 "데이터 캐시 비우기"로 강제 갱신할 수 있습니다.

## 폴더 구조

```
app.py                     Streamlit 엔트리 (사이드바·헤더·6개 탭·인앱 도움말)
src/data/    models.py     시장 무관 표준 데이터 모델 (CompanyData)
             base.py       yfinance 재무제표·OHLCV 추출·TTM 합산·피어 테이블
             opendart.py   OpenDART 공시 원본 재무 (한국, 연간 ~6개년)
             gemini.py     Gemini REST 클라이언트 (키/모델 자동해석)
             news.py       Google News RSS 헤드라인 수집
             kr_provider.py / us_provider.py / universe.py / naver.py / cache.py
src/analysis/indicators.py 5개 카테고리 지표 (순수 함수)
             scoring.py    피어 백분위 점수화 + 저평가·우량 랭킹
             capital_cost.py  베타 회귀 → 하마다 → CAPM → WACC → ROIC 스프레드
             valuation.py  상대가치·역사적 밴드·RIM 삼각측량
             backtest.py   밸류에이션 신호 예측력 검증 (룩어헤드 방지)
             ai_analysis.py  Gemini 업종분류·뉴스분석·종합 투자평가
             commentary.py 규칙 기반 한국어 해설
src/ui/      charts.py     Plotly 차트 / components.py 포맷터·배지
scripts/     check_data.py · check_analysis.py · check_backtest.py  (헤드리스 검증용)
docs/        사용설명서.md  (CPA 1차 눈높이)
```

## 한계 (알고 쓰기)

- 한국 종목의 **연간** 재무는 OpenDART 공시 원본을 우선 사용해 공식 숫자와 정렬됩니다.
  단, 헤더의 **PER/ROE(TTM)** 는 여전히 yfinance 분기 합산 기준이라 네이버 트레일링 값과 소폭 다를 수 있어
  ②탭에 공식 참고치를 함께 표시합니다. (완전 정렬하려면 DART 분기까지 쓰면 됨 — 다음 단계)
- 무료 소스라 항목 결측·오차가 있습니다.
- KRX 업종분류가 실제 사업 구성과 다를 수 있습니다 (예: 삼성전자 = '통신 및 방송 장비 제조업').
- 학습·분석 보조 도구이며 투자 조언이 아닙니다.

## 다음 단계 아이디어

- ✅ **OpenDART 연동 완료** — 한국 연간 재무를 공시 원본으로 우선 사용(연결 ~6개년). 백테스트 표본이 크게 늘었습니다(예: 삼성전자 544→1,032일, '저평가 구간' 0→364일).
- ✅ **AI 확장 완료** — 주가차트·주요뉴스·종합 투자평가 탭 + Gemini 업종분류(피어 재선정).
- **DART 분기 연동**: TTM까지 공시 기준으로 맞춰 헤더 PER/ROE도 네이버와 완전 정렬
- **전 종목 일괄 스크리닝**: 업종 내 랭킹(⑤탭)은 구현됨. 다음은 전 시장 괴리율 랭킹 (배치 수집 필요)
- 간이 DCF 추가, 워치리스트, 분기 실적 전후 비교
