# CLAUDE.md — 프로젝트 작업 지침

> 이 파일은 GitHub에서 `@claude`가 호출될 때(또는 로컬 Claude Code에서) 따르는 규칙입니다.
> 친구가 이슈/PR에 `@claude ...`로 요청하면 Claude가 아래 규칙에 맞춰 수정하고 PR을 올립니다.

> **▶ 다음 작업은 '⑤ 정규화 이익의 창을 5년 → 8년으로'입니다 — 착수 전에
> [`docs/HANDOFF-CONFIDENCE.md`](docs/HANDOFF-CONFIDENCE.md) 맨 위를 먼저 읽으세요.**
> ADR-0015가 창을 5년으로 정한 근거(*"DART 이력이 6년이라 그 이상은 못 만든다"*)가
> **사실이 아닙니다 — 재보니 13년입니다**(`python scripts/check_dart_depth.py`).
> 6년은 DART가 아니라 `opendart.py`가 보고서를 2개만 받고 `.tail(6)`으로 자르기 때문입니다.
> 품질도 2013~2025 전 연도가 8.0/10으로 균일합니다. 대가는 **종목당 호출 2회 → 12회**라
> 화면 로딩이 느려진다는 것이고, 캐시로 풀리는지 먼저 재야 합니다.
> `valuation.py`와 ADR-0015가 지금 **틀린 말을 하고 있으니 함께 고치세요.**
> 그다음 남은 것은 **백테스트 연구**와 **EPV를 축으로 짓기**입니다(인계문에 셋 다 있습니다).
>
> 아래는 그중 'EPV를 짓기'의 배경입니다 — **판정 경로를 건드리는 작업입니다**
> (여기까지는 한 줄도 안 건드렸습니다).
> 읽을 순서는 **0022 → 0023 → 0024**입니다 — ADR-0023이 미리 정한 관문으로 EPV를 **기각**했고,
> **ADR-0024가 그 관문의 상한(상관 0.5) 자체를 없앴습니다.** 재려던 해악(신뢰도 부풀림)을
> ADR-0022의 `n_eff`가 이미 값 매기고 있었기 때문입니다. **EPV는 새 기준을 통과합니다** —
> 커버리지 손실 0으로 절대가치 축이 KR 43%→60% · US 65%→89%로 늡니다.
> **ADR-0024는 이해충돌을 안고 있습니다**(기각이 동기였는데 새 기준이 EPV를 통과시킵니다).
> 그 문서에 방어 절을 따로 뒀으니 읽고 판단하세요.
> 361 passed, 99 subtests · CI 관문 11개 · ADR은 0024까지.
> 지난 브랜치들의 기록은 [`docs/HANDOFF.md`](docs/HANDOFF.md)에 있습니다.

## 프로젝트 개요
기본적 분석 기반 **주식 가치평가 대시보드**(한국+미국). 종목 하나를 넣으면 재무·주가·업종 데이터를
자동 수집해 "지금 주가가 적정한가, 아니라면 왜인가"를 판정·시각화한다.
프런트는 둘: **Meridian 웹**(`server.py` + `web/`, 실제 실행 진입점 — `대시보드실행.bat`)과
Streamlit(`app.py`). 두 프런트 모두 같은 분석 엔진(`src/analysis`)을 쓴다. 8개 탭
(기업·뉴스, 요약·판정, 주가차트, 밸류에이션, 재무, 업종비교, 자본비용(WACC), AI투자평가).
**백테스트 탭은 무기한 보류**(ADR-0009) — 버튼만 막았고 패널 마크업·`backtest.py`·
`renderBacktest()`는 그대로 살아 있다. 되살리려면 `web/stock.html`의 주석만 풀면 된다.
적정주가 **판정은 펀더멘털 4방법 삼각측량**(업종 상대가치·역사적 밴드·RIM·정규화 이익)으로 내고,
①은 피어 중앙값이 아니라 **업종·규모·수익성 회귀**로 적정 배수를 구하며(ADR-0014),
⑤는 **5년 평균 순이익 × 그 회귀 적정 PER**이다(ADR-0015).
컨센서스 선행 이익(④)을 얹은 값은 **판정에 넣지 않고 화면에 나란히 병기**한다(ADR-0006).
두 값의 차이가 '지금 주가에 실린 시장의 실적 기대분'이다. + 증권가 컨센서스 교차검증
+ 비관/기준/낙관 시나리오.

## 실행 / 검증
- 실행: `pip install -r requirements.txt` → `streamlit run app.py`
- 헤드리스 검증(키 없이 됨): `python scripts/check_analysis.py KR 005930` / `US AAPL`,
  `python scripts/check_backtest.py KR 005930`
- 규모 편향 진단(수동·네트워크 필요, CI 아님): `python scripts/check_size_bias.py --limit 400`.
  판정이 '싸다'가 아니라 '작다'를 재고 있지 않은지 시총 구간별로 전수 측정한다.
- **커밋·PR 전에는 `python scripts/check_all.py`를 돌린다.** 이것이 CI 관문의 전부다 —
  `.github/workflows/quality.yml`을 직접 읽어 거기 적힌 명령을 그대로 실행하므로
  워크플로가 바뀌어도 어긋나지 않는다. 통과하면 CI도 통과한다.
  **관문 목록을 손으로 골라 돌리지 말 것.** 실제로 그래서 한 번 터졌다 — `check_design.py`만
  돌리고 "웹 변경은 괜찮다"고 판단했는데, 인라인 스타일 예산(`check_structure.py`)을
  넘긴 것을 CI가 잡았다. **한 관문이 통과했다는 사실은 다른 관문에 대해 아무 말도 하지 않는다.**
- 위 `check_all.py`는 **네트워크가 필요한 진단을 포함하지 않는다**(CI에도 없다).
  판정 로직을 건드렸으면 `check_analysis.py`·`check_warranted.py`·`check_normalized.py`·
  `check_valuation_basis.py`를 따로 돌려 실제 종목에서 확인할 것.

## 구조 (핵심)
- `src/data/` — 데이터 수집. `models.py`(시장 무관 표준 모델 `CompanyData`), `base.py`(yfinance),
  `opendart.py`(한국 공시 원본), `naver.py`, `news.py`, `gemini.py`(AI), `kr_provider.py`/`us_provider.py`.
- `src/analysis/` — **순수 함수**로 작성(입력=CompanyData, 부작용 없음). `indicators.py`, `scoring.py`,
  `capital_cost.py`(베타·하마다·WACC), `valuation.py`(적정주가 4방법 계산 · 판정은 ①②③ 종합 · ④는 병기, ADR-0006), `backtest.py`, `ai_analysis.py`.
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

**ADR 인덱스는 충돌하지 않는다 — 손으로 풀지 말 것.** `docs/adr/README.md`는 PR마다 자기
ADR 한 줄을 표 끝에 붙이는 append-only 표라, 기본 머지 전략이 이걸 매번 충돌로 봤다
(#99·#100·#101에서 세 번 반복). `.gitattributes`의 `docs/adr/README.md merge=union`이
양쪽 줄을 모두 남겨 충돌 자체를 없앤다. **표에 줄을 넣을 때 순서를 맞추려고 애쓰지 않아도
된다** — union이 번호순을 흐트러뜨릴 수 있고, `scripts/check_adr_index.py`가 그건 알림만
한다. 실패시키는 건 셋뿐이다: 같은 줄이 두 번(union이 프로즈까지 겹쳤을 때) · 파일은
있는데 표에 없음 · 표에는 있는데 파일이 없음.
  `.gitattributes`는 **머지를 수행하는 브랜치의 작업 트리**에 있어야 적용된다(main에만 있으면
  안 된다). 지금 main에서 딴 브랜치는 자동으로 갖는다 — 이 규칙이 들어오기 전에 만든
  브랜치만 `git checkout origin/main -- .gitattributes`를 한 번 하면 된다.

## 보안 (중요)
- **API 키를 절대 코드/커밋에 넣지 말 것.** 키는 `.streamlit/secrets.toml`(=`.gitignore`로 제외)
  또는 환경변수(`OPENDART_API_KEY`, `GEMINI_API_KEY`)로만 읽는다.
- 키가 없어도 앱이 동작해야 함(폴백 유지).

## 성격
투자 조언이 아니라 **학습·분석 보조 도구**. 새 기능에도 "판단 근거를 보여주되 단정하지 않는다"는
톤을 유지하고, AI 생성 결과에는 면책 문구를 붙인다.
