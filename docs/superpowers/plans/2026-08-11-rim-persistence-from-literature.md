# ③ RIM 지속계수를 문헌에서 못 박는다 — 구현 계획

- 날짜: 2026-08-11
- 브랜치: `docs/rim-persistence-citations` (origin/main에서 딴 것 · 인용 보강 커밋 1개 선행)
- 새 ADR: **0038**
- 관련: [ADR-0003](../../adr/0003-fair-value-weighted-average.md)(순위 인코딩 ≠ 추정) ·
  [ADR-0010](../../adr/0010-book-rejected-gate-for-rim.md)(③이 1/PBR을 되읽던 문제) ·
  [ADR-0029](../../adr/0029-one-year-was-the-wrong-ruler.md)(③은 3~5년에서 좋아진다) ·
  [ADR-0037](../../adr/0037-the-panel-is-reproducible-the-inputs-were-not.md)(패널 재현 조건)

## 무엇을 하려는가

`_rim()`의 지속계수 w를 **우리가 정한 (0.6, 0.8, 1.0)에서 문헌에서 온 (0.21, 0.62, 1.00)으로**
바꾼다. 세 점 전부 출처가 있어 **지어낸 숫자가 0개**가 된다.

| 자리 | 현행 | 새 값 | 출처 |
|---|---|---|---|
| 하단 | 0.60 (판단값) | **0.21** | Wang(2023, EAR 32(3) 663-691) · Myers(1999, TAR 74(1)) |
| **중심** | **0.80 (판단값)** | **0.62** | **Fama & French(2000, JB 73(2) 161-175)** — 수익성 평균회귀 연 38% |
| 상단 | 1.00 | 1.00 | Frankel & Lee(1998, JAE 25(3) 283-319) — 터미널 영구 지속 |

중심을 FF로 고른 이유는 **재는 대상이 우리 식과 같기 때문**이다. FF는 수익성(이익/장부가)의
평균회귀를 재고, 우리 식의 x^a = B·(ROE−r)도 ROE의 초과분이다. Wang·Myers의 ω는
**2변수 LID의 한 축**이라(짝인 γ ≈ 1.018이 장기 초과이익을 나른다) 1변수 식에 중심으로
넣으면 안 된다 — Wang 자신이 1변수 Ohlson(1995) 구현은 **중앙 34.8% 과소평가**한다고 잰다.
그 편향이 '하단'이라는 역할과는 방향이 맞으므로 **하단으로만** 쓴다. 이 사실을 코드에 적는다.

DHS(1999)의 ω(널리 0.62로 인용됨)를 중심으로 쓰지 않는 이유는 하나다 — **이 PC에서 원표를
열지 못했다.** 못 연 숫자를 상수로 박는 것은 이 저장소가 스스로 금지한 짓이다.

## 이 변경의 진짜 비용 — 먼저 읽을 것

w는 **문헌 정합성 ↔ 축 독립성의 손잡이 그 자체**다. 적정PBR = 1 + (ROE−r)·α₁,
α₁ = w/(1+r−w)이고, α₁이 ROE 정보의 무게다. r=10% 기준:

| w | α₁ | 뜻 |
|---|---|---|
| 0.21 | 0.24 | 적정PBR이 전 종목 ≈1.0 → **괴리율 = 1/PBR − 1** |
| **0.62** | **1.29** | ROE 신호의 무게가 현행의 **절반 이하** |
| 0.80 (현행) | 2.67 | — |
| 0.90 (`backtest.py` 하드코딩) | 4.50 | — |

즉 w를 내리면 **③이 PBR을 되읽는 쪽으로 더 간다.** 그게 ADR-0010이 잰 문제다(순위상관 +0.973).
**그래서 Phase A가 먼저다.** 문헌값이 축을 망가뜨리는지 확인하기 전에는 상수를 바꾸지 않는다.

### 사전등록 — 이걸 어기면 ADR-0003을 배신하는 것이다

- **w를 IC로 고르지 않는다.** 후보는 문헌값 0.62 **하나**다. 백테스트는 채택 여부를 정하는
  자리가 아니라 **부작용을 확인**하는 자리다. IC가 가장 높은 w를 찾아 쓰는 순간
  ADR-0003이 가중치에서 일부러 피한 짓(표본에 맞추기)을 하게 된다.
- **기각 조건을 미리 적는다.** 아래 둘이 **동시에** 성립하면 0.62 채택을 보류하고,
  ADR-0039에 "문헌과 축 독립성이 충돌한다"고 적은 뒤 현행 0.8을 유지한다.
  1. ③ 괴리율과 1/PBR의 시점별 평균 스피어만이 현행 대비 **+0.02 이상** 상승
  2. ③ 단독 IC가 현행 대비 **하락**(부호 무관, 점추정 기준)
- **커버리지는 변하지 않아야 한다.** 게이트(ADR-0007·0010)는 w를 보지 않으므로 ③이 서는
  종목 수가 달라지면 그건 버그다. 이걸 Phase A의 자기검사로 쓴다.

## 파일 구조

```
scripts/backtest_panel.py          수정 — VARIANTS에 w 후보 추가 + pbr·rim_fair_pbr 열
scripts/check_rim_persistence.py   신규 — w별 ③ IC · 1/PBR 상관 · 커버리지 (Phase A 측정)
scripts/check_sensitivity.py       수정 — rim_with_center가 임의 w를 받게
src/analysis/valuation.py          수정 — RIM_PERSISTENCE 상수화 (Phase C)
src/analysis/backtest.py           수정 — 0.9 하드코딩 제거, 같은 상수를 읽게 (Phase C)
tests/test_analysis_accuracy.py    수정 — RimAssumptionTests 기대값
web/assets/stock.js                수정 — 식 표기의 w 설명 (Phase C)
docs/adr/0039-*.md                 신규 + docs/adr/README.md 한 줄
```

---

## Phase A — 측정 (판정 상수는 건드리지 않는다)

### Task 1: `backtest_panel.py`에 w 변형 추가

`VARIANTS`는 이미 "**상수 하나만 갈아 끼우고 나머지는 전부 같다**"는 규약으로 돌아간다.
같은 자리에 붙인다.

```python
VARIANTS = ("base", "norm5", "band5", "nogate", "w021", "w062", "w090")
```

`apply_variant()`에 추가 — `_rim`을 통째로 바꾸지 말고 **중심만 옮긴다**(식이 갈리면
무엇이 차이를 냈는지 알 수 없게 된다):

```python
    if name.startswith("w0"):                 # w021 · w062 · w090
        center = int(name[1:]) / 100.0
        orig = V._rim

        def moved(bps, roe, r, _c=center):
            fv, _ = orig(bps, roe, r)         # 하단·상단은 원본 그대로
            if fv is None:
                return None, None
            mid = bps + bps * (roe - r) * _c / (1 + r - _c)
            mid = max(mid, 0.0)
            return (V.FairValue("수익가치(RIM)", fv.low, mid, fv.high,
                                note=f"지속계수 중심 {_c}"), mid / bps)

        V._rim = moved
        return f"③ 지속계수 중심 0.8 → {center}"
```

**주의 — 이 방식의 한계를 알고 쓴다.** 하단·상단을 원본(0.6·1.0)으로 두므로 이 변형이 재는
것은 **중심값의 이동뿐**이다. 패널의 `수익가치(RIM)` 열은 중심(mid)에서 나오는 괴리율이라
측정 목적에는 충분하다. Phase C에서 하단까지 바꾸는 것은 **화면의 범위 표시**에만 영향을
주고 판정 수치에는 영향이 없다 — 이 사실을 ADR에 적는다.

**구현하며 하나 걸렸다**: `w021`은 중심(0.21)이 원본 하단(0.6)보다 **아래**라
`low ≤ mid ≤ high`가 깨진다. 판정과 패널은 mid만 쓰지만 `aggregate()`가 low·high도
가중평균하므로 뒤집힌 채 두면 범위가 거꾸로 선 값이 만들어진다. `min(low, mid)`·
`max(high, mid)`로 넓혀서 막았다(실측: w021 low=mid=101.18, w062·w090은 원본 범위 안).

### Task 2: 패널에 `pbr`·`rim_fair_pbr` 열 추가

1/PBR 되읽기를 재려면 그 시점 PBR이 필요한데 지금 패널에 없다(`mcap`·`price`만 있다).
`compute_valuation()`이 이미 `res.rim_fair_pbr`을 들고 있으므로 그것과 실제 PBR을 함께 싣는다.

- `pbr` = `mcap / total_equity`(그 시점 재무) — 축 계산이 쓰는 것과 같은 자기자본을 쓴다.
- `rim_fair_pbr` = `val.rim_fair_pbr`

**스키마가 바뀐다.** 기존 `panel.parquet`과 열 집합이 달라지므로 ADR-0037의 비교 규칙에
걸린다. 그래서 **base도 함께 다시 만든다** — 같은 코드·같은 `raw/`로 base와 w 변형을
연달아 생성해야 비교가 성립한다. `raw/`는 이 PC에 있다(3,876파일, 재수집 불필요).

```bash
python scripts/backtest_panel.py --variant base --market KR
python scripts/backtest_panel.py --variant w062 --market KR
python scripts/backtest_panel.py --variant w021 --market KR
python scripts/backtest_panel.py --variant w090 --market KR
```

**자기검사**: 네 패널의 `수익가치(RIM)` **비결측 행 수가 전부 같아야 한다**. 다르면 게이트가
w를 보고 있다는 뜻이고 그건 버그다 — 진행을 멈춘다.

### Task 3: `scripts/check_rim_persistence.py` (신규)

`check_backtest_combos.py`의 IC 계산부를 재사용한다(그쪽 함수를 공개로 올리는 편이
복붙보다 낫다 — EPV 계획의 Task 2가 같은 일을 했다).

내는 표:

| w | ③ 단독 IC | t | ③ 괴리율 vs 1/PBR 스피어만 | 종합 IC(①③⑤) | ③ 커버리지 |
|---|---|---|---|---|---|

- IC는 시점별 횡단면 스피어만(로그 괴리율, `fwd_12m`) → 시점 평균 + t
- **12개월과 3년을 둘 다 낸다.** ADR-0029가 ③은 3~5년에서 좋아진다고 쟀으므로 12개월만
  보면 이 축을 과소평가한다. `check_backtest_horizon.py`가 이미 지평을 다루므로 그 방식을 따른다.
- 마지막에 위 **기각 조건 두 줄을 자동으로 판정해 [확인]/[문제]로 찍는다.** 사람이 표를 보고
  해석하게 두면 원하는 쪽으로 읽게 된다.

### Task 4: `check_sensitivity.py`의 `rim_with_center`가 임의 w를 받게

지금은 `vals[w_center]`로 딕셔너리를 조회해 **0.6/0.8/1.0만** 중심이 될 수 있다. 0.62를 넣으면
`KeyError`가 난다. 식으로 직접 계산하게 고친다(위 Task 1과 같은 형태).

이걸로 **대표 종목 10곳에서 판정이 몇 개나 뒤집히는지**를 함께 잰다 — 패널 IC와 다른 질문이고,
R2 조서가 원래 묻던 질문이다.

---

## Phase B — 결정

### Task 5: ADR-0039

Phase A의 표를 그대로 싣고 결론을 적는다. **기각 조건에 걸렸으면 걸렸다고 적고 현행을 유지한다** —
그렇게 끝나도 이 작업은 실패가 아니다(ADR-0023이 EPV를 기각하고도 값어치를 했다).

담을 것:
- 왜 FF인가 / 왜 Wang을 중심에 안 쓰는가(2변수 LID의 한 축) / 왜 DHS를 인용만 하는가(원표 못 봄)
- α₁ 표와 "w는 문헌 정합성 ↔ 축 독립성의 손잡이"라는 프레이밍
- **한계 선언**: 세 문헌 전부 **미국** 표본이다. 한국 ω 추정치는 찾지 못했다.
  ADR-0030(미국 미재현)과 같은 성격의 한계이므로 화면에 이보다 강한 말을 쓰지 않는다.
- **한계 선언 2**: FF는 초과이익이 **평균 ROE로** 회귀한다고 재는데 우리 식은 **0으로** 소멸한다.
  평균 ROE > r이면 우리 쪽이 보수적으로 틀린다 — 방향을 밝히면 된다.

---

## Phase C — 구현 (ADR 승인 후)

### Task 6: `valuation.py` 상수화

지금 w는 `_rim()` 안에 리터럴 튜플로 박혀 있다. 상수로 올린다 — `backtest.py`가 같은 값을
읽어야 하고, 변형 스크립트가 갈아 끼울 자리도 여기다.

```python
# 세 점 전부 문헌에서 온다 — 이 축에서 우리가 지어낸 숫자는 없다. 출처는 아래 주석.
RIM_PERSISTENCE = (0.21, 0.62, 1.00)
RIM_PERSISTENCE_CENTER = 0.62
```

`_rim()`은 이 상수를 순회하고 `vals[RIM_PERSISTENCE_CENTER]`를 중심으로 쓴다.
`note` 문자열도 `f"지속계수 {min}~{max}"`로 상수에서 만든다(지금은 `"0.6~1.0"`이 손으로 박혀 있다).

이미 붙여 둔 문헌 주석(이 브랜치의 선행 커밋)을 **새 값에 맞게 고친다** — 지금 그 주석은
"하단 0.6이 문헌의 위쪽이고 중심 0.8은 어느 추정치보다도 높다"고 적혀 있는데,
채택 후에는 그 문장이 거짓이 된다.

### Task 7: `backtest.py`의 0.9 하드코딩 제거

`_rim_discount()`가 `fair_pbr = 1.0 + (roe - r) * 0.9 / (0.1 + r)`로 **w=0.9를 따로 쓴다.**
같은 저장소 안에서 w가 두 개인 상태다 — ADR-0009가 옛 백테스트를 접은 이유 중 하나가
정확히 이것이었다("③은 지속계수 0.9 고정(판정은 0.6~1.0)").

`RIM_PERSISTENCE_CENTER`를 읽게 고치고, 함수 docstring의 "지속계수 0.9 시나리오"도 함께 고친다.
`scripts/check_data_integrity.py`에 `("src/analysis/backtest.py", "_rim_discount")` 등록이
있으므로 그 항목의 설명도 확인한다.

### Task 8: 테스트

`tests/test_analysis_accuracy.py::RimAssumptionTests` — 기대값이 전부 바뀐다.
B=100, ROE=15%, r=10%에서:

| | 현행 | 새 값 |
|---|---|---|
| `fv.low` | 106.0 | **101.179775** |
| `fv.mid` | 113.333 | **106.458333** |
| `fv.high` | 150.0 | 150.0 (불변) |
| `fair_pbr` | 1.1333 | **1.064583** |
| `note` 포함 문자열 | `"0.6~1.0"` | `"0.21~1.0"` |

메서드 이름 `test_persistence_scenarios_are_0_6_to_1_0_centered_at_0_8`도 바꾼다.
`test_formula_matches_ohlson_alpha1`의 루프 `((0.6, fv.low), (0.8, fv.mid))`는
**상수에서 읽게** 고쳐 다음에 또 안 썩게 한다.

새 테스트 하나 추가: **`backtest.py`의 복원 w와 `valuation.py`의 판정 w가 같다**는 등식.
지금 갈라져 있던 자리라 회귀 방지 가치가 있다.

### Task 9: 화면

- `stock.js:1183` — `③ RIM: V = B + B(ROE−r)·w/(1+r−w)` 뒤에 w 범위와 출처 한 줄
- `_rim`의 `note`가 그대로 방법 표에 나가므로 **문구가 자동으로 따라온다**(손으로 적지 않는다)
- `check_screen_language.py`가 판정 문구를 전수 등록하므로, 문구를 바꾸면 그쪽도 확인

---

## Phase D — 검증

```bash
.venv/Scripts/python.exe scripts/check_all.py           # CI 관문 11개 (필수)
.venv/Scripts/python.exe scripts/check_analysis.py KR 005930
.venv/Scripts/python.exe scripts/check_analysis.py US AAPL
.venv/Scripts/python.exe scripts/check_valuation_basis.py
.venv/Scripts/python.exe scripts/check_sensitivity.py
```

**`python`이 아니라 `.venv/Scripts/python.exe`를 쓴다.** 이 PC의 `python`은
`C:/Python314/python`이고 pandas가 없어, 그냥 돌리면 관문 4개가 `ModuleNotFoundError`로
실패한다 — 코드 문제로 오진하기 쉬운 자리다.

네트워크 진단은 `check_all.py`에 없으므로 **따로 돌린다**(판정 로직을 건드리는 변경이다).

## 완료 조건

- [x] Phase A 표가 나왔고 기각 조건 두 줄이 자동 판정으로 찍힌다
      — **12개월로는 둘 다 걸렸고**(Δρ +0.035 · Δ③IC −0.001), 사용자 결정으로 자를 5년으로
      옮겨 재판정해 조건 ②가 통과했다(Δ③IC +0.004). **결과를 본 뒤 자를 바꾼 것**이라
      ADR-0039에 이해충돌 절을 뒀고, 두 자의 결과를 출력에 모두 남겼다.
- [x] 네 패널의 ③ 비결측 행 수가 동일 — 전부 3,620행(게이트는 w를 보지 않는다)
- [x] ADR-0039 작성 + `docs/adr/README.md` 한 줄 (`check_adr_index.py` 확인 3 · 문제 0)
- [x] `valuation.py`와 `backtest.py`가 같은 `RIM_PERSISTENCE_CENTER`를 읽고,
      그 등식을 `test_backtest_reconstruction_uses_the_same_persistence`가 지킨다
- [x] `check_all.py` 통과 11 · 실패 0
- [x] **base 패널을 새 상수로 재빌드했고 `panel_w062`와 전 열 최대차 0** —
      Phase A가 시뮬레이션한 것과 Phase C가 출시한 것이 같은 물건이라는 확인이다
- [x] 네트워크 진단: `check_analysis KR 005930`·`US AAPL` 통과
- [ ] **`check_valuation_basis`는 못 돌렸다** — yfinance가 캐시 없는 종목의 연간
      손익계산서를 주지 않는다(`base.py::extract_financials`). 코드 문제가 아니다:
      origin/main으로 되돌려도 같은 지점에서 같은 오류가 난다. **Yahoo가 복구되면 돌릴 것.**
      다만 패널 재빌드가 `compute_valuation()`을 10시점 × ~875종목에 실제로 돌렸으므로
      판정 경로 자체는 그보다 넓게 exercised 됐다.
- [x] CLAUDE.md 머리말의 "다음 ADR 번호"를 0039로 갱신 + ADR-0039 요약 추가

## 실행하며 걸린 함정 (다음 사람에게)

- **`cmd 2>&1 | tail -3`은 exit code를 가린다.** 파이프라인 종료코드는 `tail`의 것이라
  항상 0이다. 백그라운드 패널 빌드가 `YFRateLimitError`로 죽었는데 "정상 종료"로 보고됐다.
  **빌드를 파이프에 물리지 말 것.**
- **`git stash`는 `data/backtest/*.parquet`도 되감는다.** 코드 변경이 원인인지 가리려고
  stash를 썼다가 패널까지 되돌아갔고, `stash pop`이 mtime을 갱신해 "재빌드 성공"처럼 보였다.
  코드만 격리하려면 `git stash push -- <코드경로>`로 좁힐 것.
- **`panel_manifest.json`을 쓰는 것은 `backtest_panel.py`가 아니라 `backtest_pack.py`다.**
  패널을 다시 만들어도 매니페스트는 그대로다 — 지문을 남기려면 pack을 따로 돌려야 한다.

## 하지 않는 것

- **w를 백테스트로 최적화하지 않는다.** 후보는 문헌값 하나다.
- **미국 패널은 이번에 안 만든다.** ADR-0030대로 미국은 별개 질문이고, 이 변경의 쟁점
  (③의 1/PBR 되읽기)은 한국 PBR 분포에서 나온 문제다.
- **가중치(③ 23.1%)는 건드리지 않는다.** ADR-0003·0028의 자리다.
- **게이트(ADR-0007·0010) 임계는 건드리지 않는다.** 다만 w를 내려 ③이 1/PBR에 가까워지면
  ADR-0010의 전제가 흔들린다 — 그 사실만 ADR-0039에 적어 다음 사람에게 넘긴다.
