# CLAUDE.md — 프로젝트 작업 지침

> 이 파일은 GitHub에서 `@claude`가 호출될 때(또는 로컬 Claude Code에서) 따르는 규칙입니다.
> 친구가 이슈/PR에 `@claude ...`로 요청하면 Claude가 아래 규칙에 맞춰 수정하고 PR을 올립니다.

## 프로젝트 개요
기본적 분석 기반 **주식 가치평가 대시보드**(한국+미국). 종목 하나를 넣으면 재무·주가·업종 데이터를
자동 수집해 "지금 주가가 적정한가, 아니라면 왜인가"를 판정·시각화한다.
프런트는 둘: **Meridian 웹**(`server.py` + `web/`, 실제 실행 진입점 — `대시보드실행.bat`)과
Streamlit(`app.py`). 두 프런트 모두 같은 분석 엔진(`src/analysis`)을 쓴다. 9개 탭
(기업·뉴스, 요약·판정, 주가차트, 밸류에이션, 재무, 업종비교, 자본비용(WACC), 백테스트, AI투자평가).
적정주가는 4방법 삼각측량(업종 상대가치·역사적 밴드·RIM·컨센서스 선행 이익) + 증권가 컨센서스
교차검증 + 비관/기준/낙관 시나리오.

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
  `capital_cost.py`(베타·하마다·WACC), `valuation.py`(적정주가 4방법·가중종합, ADR-0003), `backtest.py`, `ai_analysis.py`.
- `src/ui/` — `charts.py`(Plotly), `components.py`(포맷터·배지). `app.py`가 엔트리.

## 코딩 규칙
- 주석·UI 문구는 **한국어**. 기존 코드의 톤·밀도를 따를 것.
- 분석 로직은 `src/analysis/`의 순수 함수로. 시장이 늘면 `src/data/`에 provider만 추가.
- 무료 데이터라 **결측이 흔함** → 값이 없으면 `None` 처리하고 절대 크래시 내지 말 것(N/A 표기).
- 차트 색은 `src/ui/components.py`의 검증된 팔레트(Streamlit)와 `web/assets/meridian.css`의
  `--dv-*` 토큰(Meridian 웹)만 사용. **색은 면과 선에 쓰고, 값을 리터럴로 적지 않는다.**
- **판정에는 색을 쓰지 않는다**(R4). "저평가/고평가"는 우리가 내린 판단이고 등락률·수익률은
  숫자의 부호인데, 둘을 같은 색으로 칠하면 초록을 본 사람이 "싸다"인지 "올랐다"인지 갈라낼 수
  없다(게다가 이 둘은 자주 반대 방향이다). 판정 헤드라인·배지·눈금 라벨은 **무채 잉크**로만
  쓰고 세기는 진하기로 말한다. 방향은 문자와 눈금 위 위치가 말한다.
  초록(`--dv-positive`)·클레이(`--dv-negative`)는 **숫자의 부호 전용**이다.
- 글자로 쓰는 색은 지면 위에서 WCAG AA(4.5:1)를 넘겨야 한다. 면·선으로만 쓰는 값과 토큰이
  다르다(`--dv-green`은 면, `--dv-positive`는 글자). 확인: `python scripts/check_design.py`
- 새 파이썬 의존성을 추가하면 `requirements.txt`도 갱신.

## 브랜치 · PR 규칙 (중요)

로컬 작업과 GitHub의 `@claude`가 각자 브랜치를 만들다 보니, base(합쳐질 목적지)를 잘못 잡아
**변경이 main에 닿지 못하고 사라지는 사고가 반복**됐다(#31·#36). 머지 버튼은 정상으로 보이고
PR 상태도 MERGED가 되기 때문에 눈으로는 성공한 것처럼 보인다. 아래를 지킨다.

- **브랜치는 항상 최신 main에서 딴다.** 작업 전 `git fetch origin` →
  `git switch -c <새브랜치> origin/main`. 다른 작업 브랜치 위에서 그대로 파생하지 않는다.
- **PR의 base는 언제나 `main`.** 다른 PR 브랜치를 base로 쓰지 않는다.
- 불가피하게 스택을 쌓아야 하면 PR 본문 **첫 줄에** 의존 PR 번호와 머지 순서를 밝히고,
  의존 PR이 머지되는 즉시 이 PR의 base를 `main`으로 되돌린다.
- **내가 만들지 않은 브랜치는 force-push하지 않는다.** 자기 브랜치도 `--force-with-lease`만 쓴다.
- 머지 뒤에는 반드시 실제 반영을 확인한다: `git fetch origin && git log --oneline origin/main -5`
- 위 규칙은 `.github/workflows/pr-base-guard.yml`이 PR마다 자동 검사한다(죽은 base면 CI 실패).

## 보안 (중요)
- **API 키를 절대 코드/커밋에 넣지 말 것.** 키는 `.streamlit/secrets.toml`(=`.gitignore`로 제외)
  또는 환경변수(`OPENDART_API_KEY`, `GEMINI_API_KEY`)로만 읽는다.
- 키가 없어도 앱이 동작해야 함(폴백 유지).

## 성격
투자 조언이 아니라 **학습·분석 보조 도구**. 새 기능에도 "판단 근거를 보여주되 단정하지 않는다"는
톤을 유지하고, AI 생성 결과에는 면책 문구를 붙인다.
