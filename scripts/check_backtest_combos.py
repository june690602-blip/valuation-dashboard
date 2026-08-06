"""어느 조합이 맞았나 — 사전등록한 후보 14개만 채점한다.

    python scripts/check_backtest_combos.py
    python scripts/check_backtest_combos.py --full     # 분할 안 한 전체기간도 함께

사전등록: `docs/review/B1-백테스트-사전등록.md` §3·§4·§5.
**이 파일은 거기 적힌 후보만 잰다.** 새 후보를 넣고 싶으면 사전등록 문서를 먼저 고치고
왜 고치는지를 ADR에 남긴다(ADR-0024가 그 전례다).

채점(사전등록 §3)
-----------------
- **주** IC — 시점별 횡단면 Spearman(로그 괴리율, 이후 12개월 수익률).
  **9개 시점에 대해** 평균·t검정한다. 종목 수로 t검정하면 표본을 1,500배 부풀린 거짓이다.
- **부** Q5−Q1 — 괴리율 5분위 동일가중 롱숏 스프레드, 시점별로 낸 뒤 평균.
- **부** 단조성 — 5분위 평균수익률이 단조 증가하는가.
- 커버리지를 **반드시 함께** 낸다. 축을 빼면 눈금은 좋아지고 표본은 줄어든다.

종합 괴리율은 **판정과 같은 방식**으로 낸다 — `fair_mid = Σ wᵢ·fairᵢ`이므로
`fair_mid/price = Σ wᵢ·exp(gᵢ)`이고, 로그 괴리율은 `log(Σ wᵢ·exp(gᵢ))`다.
로그를 그냥 평균하면(기하평균) 판정과 다른 값이 되고, 그것이 ADR-0009이 잡아낸
*"우리 툴이 아닌 것을 검증하고 있었다"*와 같은 종류의 실수다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.valuation import METHOD_WEIGHTS                       # noqa: E402

DATA = ROOT / "data" / "backtest"

REL, BAND, RIM, NORM, EPV = ("업종 상대가치", "역사적 밴드", "수익가치(RIM)",
                             "정규화 이익", "EPV")
AXES = (REL, BAND, RIM, NORM, EPV)
SHORT = {REL: "①상대", BAND: "②밴드", RIM: "③RIM", NORM: "⑤정규", EPV: "EPV"}

W = {m: METHOD_WEIGHTS[m] for m in (REL, BAND, RIM, NORM)}   # 25/25/15/25
IN_SAMPLE = (2017, 2018, 2019, 2020)                          # 사전등록 §4
OUT_SAMPLE = (2021, 2022, 2023, 2024, 2025)
N_QUANTILE = 5


# ── 후보 (사전등록 §5) — **여기 적힌 것만 잰다** ─────────────────────
def _plain(weights: dict):
    """가중평균 괴리율. 있는 축만으로 재정규화 — 판정의 `_weighted`와 같은 규칙."""
    def f(row):
        num = den = 0.0
        for m, w in weights.items():
            g = row.get(m)
            if g is not None and np.isfinite(g):
                num += w * np.exp(g)
                den += w
        return np.log(num / den) if den > 0 else np.nan
    return f


def _merged_rel_norm(row):
    """①⑤ 통합 — 둘을 먼저 한 축으로 합치고, 그 축이 ②③과 나란히 선다 (후보 B3).

    근거: `n_eff` 중앙 1.65~1.89(ADR-0022) · ①⑤가 **같은 회귀 배수**를 공유(ADR-0015).
    통합 축의 가중은 ①⑤ 가중의 합(0.5)이다 — 정보를 버리는 것이 아니라 이름을
    사실에 맞추는 것이므로 몫은 그대로 둔다.
    """
    pair = [np.exp(row[m]) for m in (REL, NORM)
            if row.get(m) is not None and np.isfinite(row.get(m, np.nan))]
    merged = float(np.mean(pair)) if pair else None
    num = den = 0.0
    if merged is not None:
        num, den = (W[REL] + W[NORM]) * merged, W[REL] + W[NORM]
    for m in (BAND, RIM):
        g = row.get(m)
        if g is not None and np.isfinite(g):
            num += W[m] * np.exp(g)
            den += W[m]
    return np.log(num / den) if den > 0 else np.nan


CANDIDATES = {
    # 5-A 축 하나하나 (질문 1)
    "A1 ①상대 단독":      _plain({REL: 1.0}),
    "A2 ②밴드 단독":      _plain({BAND: 1.0}),
    "A3 ③RIM 단독":       _plain({RIM: 1.0}),
    "A4 ⑤정규 단독":      _plain({NORM: 1.0}),
    "A5 EPV 단독":        _plain({EPV: 1.0}),
    # 5-B 어떻게 묶나 (질문 2)
    "B1 현행 4축":        _plain(W),
    "B2 현행 동일가중":    _plain({m: 1.0 for m in W}),
    "B3 ①⑤통합+②③":     _merged_rel_norm,
    "B4 절대가치 강화":    _plain({**W, RIM: W[RIM] * 2}),
    "B5 EPV추가 5축":     _plain({**W, EPV: W[RIM]}),
    "B6 ②③만(옛 백테)":  _plain({BAND: W[BAND], RIM: W[RIM]}),
}
CANDIDATE_COUNT_NOTE = ("A 5개 + B 6개 + C 3개(변형 패널) = **14개**를 봤다. "
                        "후보가 많을수록 우연히 잘 맞는 것이 하나는 나온다.")

# ── 사후 관찰 — **사전등록 밖이다** ─────────────────────────────────
# 결과를 보고 만든 후보다. 여기 두는 이유는 숨기지 않기 위해서지 채택하려는 것이 아니다.
# 채택하면 그것이 정확히 사전등록이 막으려던 과최적화다. 다음 표본에서 **미리 등록해**
# 확인할 것 — 그때까지 이 줄들은 '가설'이지 '결과'가 아니다.
POSTHOC = {
    "P1 ①③⑤ (②를 뺌)": _plain({REL: W[REL], RIM: W[RIM], NORM: W[NORM]}),
    "P2 ①⑤ 만": _plain({REL: W[REL], NORM: W[NORM]}),
    "P3 ①③⑤+EPV": _plain({REL: W[REL], RIM: W[RIM], NORM: W[NORM], EPV: W[RIM]}),
}


# ── 채점 ────────────────────────────────────────────────────────────
def score_date(g: pd.Series, r: pd.Series) -> dict:
    """한 시점 한 후보. g=로그 괴리율, r=이후 12개월 수익률."""
    ok = g.notna() & r.notna() & np.isfinite(g) & np.isfinite(r)
    g, r = g[ok], r[ok]
    n = len(g)
    if n < 30:
        return {"n": n, "ic": np.nan, "spread": np.nan, "mono": np.nan}
    ic = float(g.rank().corr(r.rank()))
    try:
        q = pd.qcut(g.rank(method="first"), N_QUANTILE, labels=False)
    except ValueError:
        return {"n": n, "ic": ic, "spread": np.nan, "mono": np.nan}
    means = r.groupby(q).mean()
    spread = float(means.iloc[-1] - means.iloc[0]) if len(means) == N_QUANTILE else np.nan
    mono = (float(np.corrcoef(np.arange(len(means)), means.to_numpy())[0, 1])
            if len(means) == N_QUANTILE else np.nan)
    return {"n": n, "ic": ic, "spread": spread, "mono": mono}


def _t_stat(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if len(x) < 2 or x.std(ddof=1) == 0:
        return np.nan
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def evaluate(panel: pd.DataFrame, name: str, fn) -> pd.DataFrame:
    rows = []
    for t, sub in panel.groupby("date"):
        g = sub.apply(fn, axis=1)
        s = score_date(g, sub["fwd_12m"])
        s.update({"date": t, "year": t.year, "cand": name,
                  "cover": s["n"] / len(sub) if len(sub) else np.nan})
        rows.append(s)
    return pd.DataFrame(rows)


def summarise(per_date: pd.DataFrame, years) -> dict:
    d = per_date[per_date["year"].isin(years)]
    ic = d["ic"].to_numpy(float)
    return {"ic": float(np.nanmean(ic)) if len(ic) else np.nan,
            "t": _t_stat(ic),
            "spread": float(np.nanmean(d["spread"].to_numpy(float))) if len(d) else np.nan,
            "mono": float(np.nanmean(d["mono"].to_numpy(float))) if len(d) else np.nan,
            "cover": float(np.nanmean(d["cover"].to_numpy(float))) if len(d) else np.nan,
            "pos": int((ic > 0).sum()), "k": int(np.isfinite(ic).sum())}


def _table(title: str, rows: list[tuple[str, dict, dict]]) -> None:
    print(f"\n{title}")
    print(f"{'후보':<20}{'선택 IC':>9}{'검증 IC':>9}{'검증 t':>8}"
          f"{'검증 Q5−Q1':>11}{'단조':>7}{'양(+)':>7}{'커버':>7}")
    print("─" * 78)
    for name, ins, oos in rows:
        print(f"{name:<20}{ins['ic']:>9.3f}{oos['ic']:>9.3f}{oos['t']:>8.2f}"
              f"{oos['spread']:>10.1%}{oos['mono']:>7.2f}"
              f"{oos['pos']:>4d}/{oos['k']:<2d}{oos['cover']:>7.0%}")


def load(variant: str) -> pd.DataFrame | None:
    p = DATA / ("panel.parquet" if variant == "base" else f"panel_{variant}.parquet")
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    for m in AXES:
        if m not in df.columns:
            df[m] = np.nan
    return df


def drop_high_drift(panel: pd.DataFrame, limit: float) -> pd.DataFrame:
    """주식수가 크게 늘어난 종목을 뺀다 — 시총 근사가 결론을 만들었는지 보는 검사.

    `backtest_shares.py`가 잰 드리프트를 쓴다. 근사의 방향이 '나중에 증자한 회사를
    과거에 비싸 보이게' 하는 쪽이고, 증자 많은 회사는 실제로 이후 수익률이 나쁘다 —
    즉 근사가 예측력을 **지어낼 수** 있다. 빼고도 결론이 남는지가 그 검사다.
    """
    p = DATA / "share_drift.parquet"
    if not p.exists():
        print("  ※ share_drift.parquet이 없다 — backtest_shares.py를 먼저 돌려라.")
        return panel
    d = pd.read_parquet(p)
    cols = [c for c in d.columns if c.startswith("drift")]
    worst = d[cols].max(axis=1)
    bad = set(worst[worst > limit].index)
    out = panel[~panel["code"].isin(bad)]
    print(f"  드리프트 >{limit}배 종목 {len(bad)}곳 제외 → "
          f"패널 {len(panel):,} → {len(out):,}행")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="분할 안 한 전체기간도 낸다")
    ap.add_argument("--exclude-drift", type=float, metavar="배수",
                    help="주식수가 이 배수 넘게 늘어난 종목을 빼고 다시 잰다(예: 1.5)")
    args = ap.parse_args()

    panel = load("base")
    if panel is None:
        print("panel.parquet이 없다 — scripts/backtest_panel.py를 먼저 돌려라.")
        return 1
    if args.exclude_drift:
        panel = drop_high_drift(panel, args.exclude_drift)

    dates = sorted(panel["date"].unique())
    print(f"패널 {len(panel):,}행 · 시점 {len(dates)}개 "
          f"({pd.Timestamp(dates[0]).date()} ~ {pd.Timestamp(dates[-1]).date()})")
    print(f"시점당 종목 중앙 {int(panel.groupby('date').size().median())}곳 · "
          f"폐지 편입 {int(panel['delisted_in_window'].sum())}건")
    print(f"\n※ 독립 관측은 종목 수가 아니라 **시점 {len(dates)}개**다(사전등록 §2).")

    all_rows, keep = [], {}
    for name, fn in CANDIDATES.items():
        pd_ = evaluate(panel, name, fn)
        keep[name] = pd_
        all_rows.append((name, summarise(pd_, IN_SAMPLE), summarise(pd_, OUT_SAMPLE)))

    _table("[5-A] 축 하나하나가 예측력이 있나 — 인계문 질문 1",
           [r for r in all_rows if r[0].startswith("A")])
    _table("[5-B] 축을 어떻게 묶을 것인가 — 인계문 질문 2",
           [r for r in all_rows if r[0].startswith("B")])

    # ── 5-C 변형 (다른 패널이 있어야 잰다) ──
    c_rows = []
    for var, label in (("norm5", "C1 ⑤창 5년"), ("nogate", "C2 ③게이트 끔"),
                       ("band5", "C3 ②밴드 5년")):
        p = load(var)
        if p is None:
            c_rows.append((label, None))
            continue
        pdv = evaluate(p, label, _plain(W))
        c_rows.append((label, (summarise(pdv, IN_SAMPLE), summarise(pdv, OUT_SAMPLE))))
    base_ins, base_oos = summarise(keep["B1 현행 4축"], IN_SAMPLE), summarise(
        keep["B1 현행 4축"], OUT_SAMPLE)
    ready = [(lab, v[0], v[1]) for lab, v in c_rows if v]
    if ready:
        _table("[5-C] 이미 내린 결정을 되짚는다 — 질문 3·4 (기준선 = B1 현행 4축)",
               [("B1 현행(기준선)", base_ins, base_oos), *ready])
    missing = [lab for lab, v in c_rows if not v]
    if missing:
        print(f"\n  ※ 아직 안 돌린 변형: {', '.join(missing)} "
              f"— `backtest_panel.py --variant`로 만든다.")

    if args.full:
        _table("[참고] 분할 안 한 전체기간 — **성능이 아니라 적합도다**",
               [(n, summarise(keep[n], IN_SAMPLE + OUT_SAMPLE),
                 summarise(keep[n], IN_SAMPLE + OUT_SAMPLE)) for n in CANDIDATES])

    # ── 화면이 실제로 내는 판정이 갈리나 ──
    # 조합의 IC보다 이쪽이 사용자에게 가까운 질문이다: '크게 저평가'라고 쓴 종목이
    # '크게 고평가'라고 쓴 종목보다 실제로 나았나.
    d = panel[panel["date"].dt.year.isin(OUT_SAMPLE)].dropna(subset=["verdict", "fwd_12m"])
    if len(d):
        g = d.groupby("verdict")["fwd_12m"].agg(["count", "mean", "median"])
        order = ["크게 저평가", "저평가", "적정 수준", "고평가", "크게 고평가"]
        print("\n[판정] 화면의 판정별 이후 12개월 수익률 — 검증기간")
        print(f"{'판정':<12}{'종목수':>7}{'평균':>9}{'중앙':>9}")
        print("─" * 38)
        for v in order:
            if v not in g.index:
                continue
            r = g.loc[v]
            print(f"{v:<12}{int(r['count']):>7}{r['mean']:>9.1%}{r['median']:>9.1%}")
        print("중앙값이 전부 음수인 것은 수익률 분포가 오른쪽으로 길기 때문이다"
              "(소수의 큰 상승이 평균을 끌어올린다).")

    _table("[사후] 사전등록 밖 — **결론이 아니라 다음 표본에서 확인할 가설이다**",
           [(n, summarise(evaluate(panel, n, f), IN_SAMPLE),
             summarise(evaluate(panel, n, f), OUT_SAMPLE)) for n, f in POSTHOC.items()])

    per_date = pd.concat(keep.values(), ignore_index=True)
    per_date.to_csv(DATA / "ic_by_date.csv", index=False)
    print(f"\n시점별 원자료 → {DATA / 'ic_by_date.csv'}")
    print(f"\n{CANDIDATE_COUNT_NOTE}")
    print("**선택기간에서만 좋은 것은 진 것으로 본다**(사전등록 §3). "
          "결론은 검증기간 열에서만 읽어라.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
