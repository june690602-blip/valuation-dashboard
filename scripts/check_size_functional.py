"""규모 항의 함수형 비교 — 선형 대 구간 대 스플라인 (check_size_extrapolation.py 후속).

    python scripts/check_size_functional.py          # KR·US 둘 다
    python scripts/check_size_functional.py KR

네트워크가 필요하다(전 종목 스냅숏). CI가 아니라 수동 계열이다.

## 왜 이걸 재는가

`check_size_extrapolation.py`가 이렇게 나왔다: 상한 가드는 **0곳**이고(학습 표본이
전 종목이라 범위 밖이 없다), 문제는 **최상위 1%에서만** 터진다(KR PBR 평균 잔차
−0.590, 삼성전자는 −1.694 = 5.4배 과대). 십분위 10은 −0.071로 잠잠하다.

즉 가드가 아니라 **함수형**이 의심된다 — `log(배수) = α + β·log(시총) + …`이
중간 구간에서 적합한 기울기를 꼬리까지 직선으로 연장한다.

ADR-0014가 **ROE에 대해 이미 같은 것을 발견했다**: 선형으로 넣으니 부호가 뒤집혀서
(t = −15.2) 구간 더미로 바꿨다. **규모에는 그 검사를 안 했다.** 여기서 한다.

## 비교하는 네 형태 (ROE 더미·업종 더미는 전부 동일하게 들어간다)

    M0 선형        α + β·x                          ← 현행
    M1 구간        α + Σ 십분위 더미                  (기울기 없음)
    M2 스플라인     α + β·x + Σ max(0, x − knot)      (연속·구간별 기울기)
    M3 선형+꼬리    α + β·x + 상위1% 더미              (가장 작은 변경)

    x = log(시가총액), knot = log(시총)의 80·95 분위

## 표본 내 잔차로 재면 안 된다

상위 구간에 더미를 넣으면 **그 구간의 표본 내 잔차는 정의상 0에 가까워진다.**
"고쳤다"가 아니라 "그 점들을 지나가게 그렸다"일 뿐이다. 그래서 **5겹 교차검증**으로
표본 외 오차를 재고, 그중에서도 **최상위 1%·5% 구간의 표본 외 오차**를 따로 본다.
이 구간이 좋아지지 않으면 그 형태는 채택하지 않는다.

설계행렬의 수준(십분위 경계·knot·업종 라벨)은 전체 표본에서 한 번 정한다. 폴드마다
다시 정하면 형태끼리 비교가 안 된다 — 이 누수는 네 형태에 **동일하게** 걸리므로
형태 간 비교에는 영향이 없다(절대 오차 수준을 인용할 때는 감안해야 한다).

종료 코드 1은 '현행이 최선이 아님'이다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.warranted import roe_bucket, sector_labels       # noqa: E402
from src.data.universe_multiples import (LEG_BOUNDS, collect_kr,   # noqa: E402
                                         collect_us)

FOLDS = 5
SEED = 20260804          # 고정 — 다시 돌리면 같은 분할이 나와야 재현이다
KNOT_QS = (0.80, 0.95)   # 스플라인 마디. 꼬리가 어디서 꺾이는지 보려는 위치다
TAIL_QS = (0.99, 0.95)   # 표본 외 오차를 따로 볼 꼬리 구간

WATCH = {
    "KR": {"005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER",
           "005380": "현대차"},
    "US": {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA",
           "GOOGL": "Alphabet"},
}
FORMS = ("M0 선형", "M1 구간", "M2 스플라인", "M3 선형+꼬리")


def _prepare(snap: pd.DataFrame, leg: str) -> pd.DataFrame | None:
    """production과 같은 규칙으로 거른다(LEG_BOUNDS를 그대로 쓴다)."""
    if leg not in snap.columns:
        return None
    lo, hi = LEG_BOUNDS[leg]
    v = pd.to_numeric(snap[leg], errors="coerce")
    d = snap.assign(multiple=v)[(v > lo) & (v < hi) & (snap["mcap"] > 0)]
    d = d.dropna(subset=["multiple", "mcap", "sector"]).reset_index(drop=True)
    return d if len(d) >= 500 else None


def _common_blocks(d: pd.DataFrame) -> list[np.ndarray]:
    """ROE 구간 더미 + 업종 더미. **네 형태에 똑같이 들어간다** — 규모 항만 비교한다."""
    rb = d["roe"].map(roe_bucket) if "roe" in d.columns else pd.Series([None] * len(d))
    sec = sector_labels(d["sector"].astype(str))
    cols = []
    for lv in sorted(x for x in set(rb.dropna()))[1:]:
        cols.append((rb == lv).to_numpy(float))
    for lv in sorted(set(sec))[1:]:
        cols.append((sec == lv).to_numpy(float))
    return cols


def _size_block(x: np.ndarray, form: str, knots, dec_edges, tail_cut) -> list[np.ndarray]:
    """규모 항만 형태별로 바꿔 끼운다."""
    if form == "M0 선형":
        return [x]
    if form == "M1 구간":
        # 십분위 더미. 기울기를 아예 안 쓰므로 꼬리에서 직선 연장이 원리적으로 불가능하다.
        idx = np.digitize(x, dec_edges)
        return [(idx == k).astype(float) for k in range(1, len(dec_edges) + 1)]
    if form == "M2 스플라인":
        return [x] + [np.maximum(0.0, x - k) for k in knots]
    if form == "M3 선형+꼬리":
        return [x, (x >= tail_cut).astype(float)]
    raise ValueError(form)


def _design(d: pd.DataFrame, form: str, knots, dec_edges, tail_cut) -> np.ndarray:
    x = np.log(d["mcap"].to_numpy(float))
    cols = [np.ones(len(d))] + _size_block(x, form, knots, dec_edges, tail_cut)
    cols += _common_blocks(d)
    return np.column_stack(cols)


def _cv_predict(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """5겹 교차검증 표본 외 예측. 랭크 부족은 lstsq가 최소노름해로 흡수한다."""
    rng = np.random.default_rng(SEED)
    fold = rng.permutation(len(y)) % FOLDS
    out = np.full(len(y), np.nan)
    for f in range(FOLDS):
        tr, te = fold != f, fold == f
        beta, *_ = np.linalg.lstsq(X[tr], y[tr], rcond=None)
        out[te] = X[te] @ beta
    return out


def _watch_row(d: pd.DataFrame, X: np.ndarray, y: np.ndarray, codes) -> dict:
    """전체 표본 적합값(표본 내) — 대표 종목이 어디로 가는지 보려는 것뿐이다."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    key = d["code"].astype(str)
    return {c: float(np.exp(pred[key == c][0])) for c in codes if (key == c).any()}


def run(market: str) -> int:
    print(f"\n{'=' * 78}\n{market} — 규모 항 함수형 비교 (5겹 교차검증)\n{'=' * 78}")
    snap = collect_kr() if market == "KR" else collect_us()
    print(f"스냅숏 {len(snap):,}종목   폴드 {FOLDS}   시드 {SEED}\n")

    watch = WATCH.get(market, {})
    worse = 0
    for leg in LEG_BOUNDS:
        d = _prepare(snap, leg)
        if d is None:
            print(f"■ {leg} — 표본 부족\n")
            continue
        y = np.log(d["multiple"].to_numpy(float))
        x = np.log(d["mcap"].to_numpy(float))
        knots = [float(np.quantile(x, q)) for q in KNOT_QS]
        dec_edges = [float(np.quantile(x, q / 10)) for q in range(1, 10)]
        tail_cut = float(np.quantile(x, TAIL_QS[0]))
        tails = {q: x >= float(np.quantile(x, q)) for q in TAIL_QS}

        print(f"■ {leg}   표본 {len(d):,}")
        head = f"    {'형태':<14}{'전체 MAE':>10}{'전체 R²':>9}"
        for q in TAIL_QS:
            head += f"{f'상위{(1 - q) * 100:.0f}% MAE':>13}{f'평균잔차':>10}"
        print(head)
        print("    " + "─" * (len(head) - 4))

        rows, fitted = {}, {}
        for form in FORMS:
            X = _design(d, form, knots, dec_edges, tail_cut)
            p = _cv_predict(X, y)
            r = y - p
            mae = float(np.mean(np.abs(r)))
            r2 = 1 - float(r.var() / y.var())
            line = f"    {form:<14}{mae:>10.3f}{r2:>9.3f}"
            cell = {}
            for q in TAIL_QS:
                m = tails[q]
                cell[q] = (float(np.mean(np.abs(r[m]))), float(np.mean(r[m])))
                line += f"{cell[q][0]:>13.3f}{cell[q][1]:>+10.3f}"
            print(line)
            rows[form] = (mae, r2, cell)
            if watch:
                fitted[form] = _watch_row(d, X, y, watch)

        base_mae, _, base_cell = rows["M0 선형"]
        best = min(FORMS, key=lambda f: rows[f][2][TAIL_QS[0]][0])
        tail_gain = base_cell[TAIL_QS[0]][0] - rows[best][2][TAIL_QS[0]][0]
        all_gain = base_mae - rows[best][0]
        print(f"    → 상위{(1 - TAIL_QS[0]) * 100:.0f}% 최선: {best} "
              f"(MAE {tail_gain:+.3f} · 전체 MAE {all_gain:+.3f})")

        if watch and fitted:
            print(f"    {'대표 종목':<12}{'실제':>8}" + "".join(f"{f:>14}" for f in FORMS))
            key = d["code"].astype(str)
            for code, name in watch.items():
                if not (key == code).any():
                    continue
                act = float(d.loc[key == code, "multiple"].iloc[0])
                line = f"    {name:<12}{act:>8.2f}"
                for f in FORMS:
                    v = fitted[f].get(code)
                    line += f"{v:>14.2f}" if v else f"{'—':>14}"
                print(line)

        if best != "M0 선형" and tail_gain > 0.02:
            worse += 1
            print(f"    [문제] 현행(선형)보다 나은 형태가 있다 — {best}\n")
        else:
            print("    [확인] 현행 선형이 꼬리에서도 최선이다\n")
    return worse


def main() -> int:
    args = [a.upper() for a in sys.argv[1:]] or ["KR", "US"]
    worse = sum(run(m) for m in args if m in ("KR", "US"))
    print(f"{'=' * 78}\n현행보다 나은 형태가 있는 다리 {worse}개")
    print("읽는 법 — '상위1% MAE'가 이 검사의 표적이다. 전체 MAE는 **나빠지지 않기만** 하면")
    print("된다(꼬리 26곳을 고치자고 2,500곳을 망치면 안 된다). 평균잔차가 0에서 멀면 그 구간을")
    print("체계적으로 틀리는 것이고, 부호가 음수면 배수를 과대추정해 '저평가'로 민다는 뜻이다.")
    return 1 if worse else 0


if __name__ == "__main__":
    raise SystemExit(main())
