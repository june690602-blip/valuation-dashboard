# CLAUDE.md — 프로젝트 작업 지침

> 이 파일은 GitHub에서 `@claude`가 호출될 때(또는 로컬 Claude Code에서) 따르는 규칙입니다.
> 친구가 이슈/PR에 `@claude ...`로 요청하면 Claude가 아래 규칙에 맞춰 수정하고 PR을 올립니다.

## 프로젝트 개요
기본적 분석 기반 **주식 가치평가 대시보드**(한국+미국). 종목 하나를 넣으면 재무·주가·업종 데이터를
자동 수집해 "지금 주가가 적정한가, 아니라면 왜인가"를 판정·시각화한다. Streamlit 9개 탭
(기업·뉴스, 요약·판정, 주가차트, 밸류에이션, 재무, 업종비교, 자본비용(WACC), 백테스트, AI투자평가).

## 실행 / 검증
- 실행: `pip install -r requirements.txt` → `streamlit run app.py`
- 헤드리스 검증(키 없이 됨): `python scripts/check_analysis.py KR 005930` / `US AAPL`,
  `python scripts/check_backtest.py KR 005930`
- 코드 수정 후에는 최소한 `python -c "import py_compile; py_compile.compile('바꾼파일')"`로 문법 확인,
  가능하면 위 헤드리스 스크립트로 실제 동작을 확인할 것.

## 구조 (핵심)
- `src/data/` — 데이터 수집. `models.py`(시장 무관 표준 모델 `CompanyData`), `base.py`(yfinance),
  `opendart.py`(한국 공시 원본), `naver.py`, `news.py`, `gemini.py`(AI), `kr_provider.py`/`us_provider.py`.
- `src/analysis/` — **순수 함수**로 작성(입력=CompanyData, 부작용 없음). `indicators.py`, `scoring.py`,
  `capital_cost.py`(베타·하마다·WACC), `valuation.py`(적정주가 3방법), `backtest.py`, `ai_analysis.py`.
- `src/ui/` — `charts.py`(Plotly), `components.py`(포맷터·배지). `app.py`가 엔트리.

## 코딩 규칙
- 주석·UI 문구는 **한국어**. 기존 코드의 톤·밀도를 따를 것.
- 분석 로직은 `src/analysis/`의 순수 함수로. 시장이 늘면 `src/data/`에 provider만 추가.
- 무료 데이터라 **결측이 흔함** → 값이 없으면 `None` 처리하고 절대 크래시 내지 말 것(N/A 표기).
- 차트 색은 `src/ui/components.py`의 검증된 팔레트만 사용(파랑=저평가·빨강=고평가는 판정 전용).
- 새 파이썬 의존성을 추가하면 `requirements.txt`도 갱신.

## 보안 (중요)
- **API 키를 절대 코드/커밋에 넣지 말 것.** 키는 `.streamlit/secrets.toml`(=`.gitignore`로 제외)
  또는 환경변수(`OPENDART_API_KEY`, `GEMINI_API_KEY`)로만 읽는다.
- 키가 없어도 앱이 동작해야 함(폴백 유지).

## 성격
투자 조언이 아니라 **학습·분석 보조 도구**. 새 기능에도 "판단 근거를 보여주되 단정하지 않는다"는
톤을 유지하고, AI 생성 결과에는 면책 문구를 붙인다.
