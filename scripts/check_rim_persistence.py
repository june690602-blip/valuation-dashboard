"""③ 지속계수 w를 문헌값으로 내리면 축이 망가지나 — 부작용 확인.

    python scripts/check_rim_persistence.py
    python scripts/check_rim_persistence.py --no-horizon   # 1년만 (빠름)

계획: `docs/superpowers/plans/2026-08-11-rim-persistence-from-literature.md` Phase A.

**이 스크립트는 w를 고르지 않는다.** 후보는 문헌값 0.62 하나다
(Fama & French 2000, JB 73(2) 161-175 — 수익성 평균회귀 연 38%를 지속으로 뒤집은 값).
IC가 가장 높은 w를 찾아 쓰는 순간 ADR-0003이 가중치에서 일부러 피한 짓
(표본에 맞추기)을 하게 된다. 여기서 재는 것은 **채택했을 때 무엇이 망가지는가**뿐이다.

**왜 망가질 수 있나** — 적정PBR = 1 + (ROE−r)·α₁, α₁ = w/(1+r−w)이다.
w가 낮을수록 α₁이 작아져 적정PBR이 전 종목 1.0에 붙고, 그러면 괴리율이 사실상
1/PBR − 1이 된다. 그것이 ADR-0010이 잡아낸 문제다(순위상관 +0.973).
r=10%에서 α₁은 w=0.8이면 2.67인데 w=0.62면 1.29 — **ROE 신호의 무게가 절반 이하다.**

기각 조건 (사전등록 — 결과를 보고 고치지 않는다)
------------------------------------------------
아래 둘이 **동시에** 성립하면 0.62 채택을 보류하고 현행 0.8을 유지한다.
  ① ③ 괴리율과 1/PBR의 순위상관이 현행 대비 **+0.02 이상** 상승
  ② ③ 단독 IC가 현행 대비 **하락**
하나만 걸리면 기각이 아니다 — 문헌을 따르는 대가로 감수할 수 있는 범위라 보고 ADR에 적는다.

**⚠ 판정의 자를 결과를 본 뒤에 바꿨다 — 숨기지 않는다.**
처음 사전등록은 조건 ②를 **12개월**로 쟀고, 그 자로 재면 −0.001(노이즈 수준)로 **걸렸다.**
그 결과를 본 뒤 자를 5년으로 옮겼고, 5년에서는 +0.004로 통과한다. 사용자가 내린 결정이다.

바꾼 근거는 있다 — [ADR-0029](../docs/adr/0029-one-year-was-the-wrong-ruler.md)가 이미
*"12개월은 이 도구의 주장에 맞는 자가 아니었다"*고 결론냈고, ③은 12개월 0.070 → 3~5년
0.152로 좋아진다. **폐기된 자로 관문을 세운 것이 설계 실수였다.**

그러나 **그 실수를 결과를 보기 전에 알아채지 못했다.** 자를 옮긴 시점이 결과 이후라는
사실은 근거의 타당성과 별개로 남는다. ADR-0024가 같은 일을 했고 그 문서에 이해충돌 절을
따로 두었다 — ADR-0039도 그렇게 한다. 읽는 사람이 판단할 수 있게 **두 자의 결과를 모두**
표에 남긴다(`--adjudicate-on`을 바꿔 재현할 수 있다).

준비 (같은 코드·같은 raw로 연달아 만들어야 비교가 성립한다 — ADR-0037)
------------------------------------------------------------------------
    python scripts/backtest_panel.py --variant base --market KR
    python scripts/backtest_panel.py --variant w021 --market KR
    python scripts/backtest_panel.py --variant w062 --market KR
    python scripts/backtest_panel.py --variant w090 --market KR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from check_backtest_combos import (METHOD_WEIGHTS, RIM,  # noqa: E402
                                   _plain, _t_stat, score_date)
from src.analysis.valuation import FUNDAMENTAL_METHODS  # noqa: E402

OK, BAD, NA = "[확인]", "[문제]", "[불가]"

# 라벨 → 패널 접미사. base가 현행(중심 0.8)이다.
CANDIDATES = (
    ("0.21  Wang2023·Myers1999", "_w021"),
    ("0.62  Fama-French2000", "_w062"),
    ("0.80  현행 (판단값)", ""),
    ("0.90  backtest.py 하드코딩", "_w090"),
)
BASE_LABEL = "0.80  현행 (판단값)"
LIT_LABEL = "0.62  Fama-French2000"

RHO_RISE_LIMIT = 0.02      # 기각 조건 ① — 이 이상 오르면 걸린다
MIN_N = 30                 # score_date와 같은 문턱


def verdict_weights() -> dict:
    """판정에 실제로 드는 축과 그 가중 — 손으로 적지 않고 판정 코드에서 가져온다.

    ADR-0035로 판정이 ①③⑤가 됐다. 여기 목록을 손으로 적으면 축이 또 바뀔 때 조용히 썩는다.
    """
    return {m: METHOD_WEIGHTS[m] for m in FUNDAMENTAL_METHODS}


def load(suffix: str) -> pd.DataFrame:
    p = ROOT / "data" / "backtest" / f"panel{suffix}.parquet"
    if not p.exists():
        raise SystemExit(f"{BAD} 패널이 없다: {p.name}\n"
                         f"      → python scripts/backtest_panel.py "
                         f"--variant {suffix.lstrip('_') or 'base'} --market KR")
    df = pd.read_parquet(p)
    if "pbr" not in df.columns:
        raise SystemExit(f"{BAD} {p.name}에 `pbr` 열이 없다 — 이 열을 넣기 전에 만든 패널이다.\n"
                         "      base를 포함해 **전부 다시 만들어야** 비교가 성립한다(ADR-0037).")
    return df


def add_horizons(panels: dict[str, pd.DataFrame]) -> set[str]:
    """장기 선행수익률을 붙인다. 한 번만 계산해 모든 변형에 나눠 준다.

    수익률은 (code, date, price)에서만 나오고 그 셋은 변형끼리 동일하다 — w는 판정만
    바꾸지 주가를 바꾸지 않는다. 변형마다 raw를 다시 읽으면 같은 값을 네 번 만든다.
    """
    from check_backtest_horizon import HORIZONS, build

    base = panels[BASE_LABEL]
    built = build(base, ROOT / "data" / "backtest" / "raw")
    cols = [f"fwd_{k}" for k in HORIZONS]
    keys = built[["date", "code"] + cols]
    for label, df in panels.items():
        panels[label] = df.merge(keys, on=["date", "code"], how="left")
    return cols


def rho_vs_inv_pbr(df: pd.DataFrame) -> tuple[float, float, int]:
    """③ 괴리율과 1/PBR의 시점별 순위상관 → (평균, t, 잰 시점 수).

    ADR-0010이 잰 것과 같은 양이다. 그쪽은 오늘 하루 전 종목이고 여기는 시점별 횡단면이라
    값이 같을 이유는 없다 — **후보끼리의 차이**를 보려는 것이지 절대 수준을 재는 것이 아니다.
    (실측 base: 0.954. ADR-0010의 +0.973을 다른 표본에서 다시 만난 셈이다.)

    `pbr` 열에는 24,533배 같은 값이 섞여 있는데 **버그가 아니다** — 900xxx(국내 상장 중국기업)는
    재무가 위안화, 시총이 원화라 배수가 통화만큼 어긋난다. 그 종목들은 `compute_valuation()`이
    통화 불일치로 ③을 이미 뺐으므로(rim_fair_pbr = None) 여기 계산에 들어오지 않는다.
    실제로 ③이 선 행의 PBR은 최대 4.999로 `PBR_GATE`(5.0) 안에 있다 — 게이트가 본 PBR과
    이 열이 같은 값이라는 확인이기도 하다.

    t값은 내지 않는다. 시점별 ρ가 전부 0.95 언저리라 분산이 없어 t가 수백으로 튀는데,
    그건 'ρ가 0이 아니다'라는 자명한 명제를 재는 것이지 정밀도가 아니다.
    """
    vals = []
    for _t, sub in df.groupby("date"):
        g, pbr = sub[RIM], sub["pbr"]
        ok = g.notna() & pbr.notna() & (pbr > 0) & np.isfinite(g)
        if ok.sum() < MIN_N:
            continue
        vals.append(float(g[ok].rank().corr((1.0 / pbr[ok]).rank())))
    arr = np.asarray(vals, float)
    return (float(np.nanmean(arr)) if len(arr) else np.nan, _t_stat(arr), len(arr))


def ic_of(df: pd.DataFrame, fn, ret_col: str) -> tuple[float, float, float]:
    """(IC 평균, t, 커버리지). 시점별로 재고 시점에 대해 평균한다(종목 수로 t검정하지 않는다)."""
    ics, covers = [], []
    for _t, sub in df.groupby("date"):
        g = sub.apply(fn, axis=1)
        s = score_date(g, sub[ret_col])
        ics.append(s["ic"])
        covers.append(s["n"] / len(sub) if len(sub) else np.nan)
    arr = np.asarray(ics, float)
    return (float(np.nanmean(arr)) if len(arr) else np.nan, _t_stat(arr),
            float(np.nanmean(np.asarray(covers, float))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-horizon", action="store_true",
                    help="1년만 잰다(raw 주가를 다시 읽지 않아 빠르다)")
    ap.add_argument("--adjudicate-on", default="5년", choices=("1년", "3년", "5년"),
                    help="기각 조건을 어느 보유기간으로 판정할지. 최초 사전등록은 1년이었고 "
                         "결과를 본 뒤 5년으로 옮겼다(위 docstring의 ⚠ 절).")
    args = ap.parse_args()

    panels = {label: load(sfx) for label, sfx in CANDIDATES}
    ret_cols = ["fwd_12m"]
    if not args.no_horizon:
        print("장기 선행수익률 계산 중(raw 주가 읽는 중)…")
        cols = add_horizons(panels)
        ret_cols += [c for c in cols if c in ("fwd_3년", "fwd_5년")]

    # ── 자기검사 — 게이트가 w를 보면 안 된다 ──
    print("\n자기검사 — ③이 선 종목 수가 후보끼리 같은가")
    print("─" * 76)
    counts = {label: int(df[RIM].notna().sum()) for label, df in panels.items()}
    rows = {label: len(df) for label, df in panels.items()}
    same = len(set(counts.values())) == 1 and len(set(rows.values())) == 1
    for label in panels:
        print(f"  {label:<28} 전체 {rows[label]:,}행 · ③ 있음 {counts[label]:,}행")
    if same:
        print(f"  {OK} 전 후보 동일 — 게이트(ADR-0007·0010)는 w를 보지 않는다")
    else:
        print(f"  {BAD} 후보마다 다르다 — 게이트가 w에 반응한다는 뜻이고 그건 버그다.")
        print("       여기서 멈춘다. 아래 표를 읽지 말 것 — 표본이 다르면 비교가 성립하지 않는다.")
        return 1

    # ── 본표 ──
    vw = verdict_weights()
    marks = "".join(sorted({"업종 상대가치": "①", "수익가치(RIM)": "③",
                            "정규화 이익": "⑤"}.get(m, "?") for m in vw))
    solo, combo = _plain({RIM: 1.0}), _plain(vw)
    out = {}
    for ret in ret_cols:
        label_ret = {"fwd_12m": "1년"}.get(ret, ret.replace("fwd_", ""))
        print(f"\n보유 {label_ret} — ③ 단독 IC · 종합({marks}) IC · 1/PBR 되읽기")
        print("─" * 76)
        print(f"  {'w':<28}{'③단독 IC':>10}{'t':>7}{'종합 IC':>9}{'t':>7}"
              f"{'ρ(③,1/PBR)':>13}{'③커버':>8}")
        for label, df in panels.items():
            s_ic, s_t, s_cov = ic_of(df, solo, ret)
            c_ic, c_t, _ = ic_of(df, combo, ret)
            rho, _rt, _rn = rho_vs_inv_pbr(df)
            out[(ret, label)] = {"ic": s_ic, "rho": rho}
            print(f"  {label:<28}{s_ic:>10.3f}{s_t:>7.2f}{c_ic:>9.3f}{c_t:>7.2f}"
                  f"{rho:>13.3f}{s_cov:>8.1%}")
        print("  ρ가 1.000에 가까울수록 ③은 독립된 관점이 아니라 PBR을 되읽는 것이다.")
        if ret != "fwd_12m":
            print(f"  ⚠ {label_ret} 보유를 매년 시작하면 인접 구간이 겹친다 — **위 t값은 부풀려져 "
                  "있다**(개정 2).\n"
                  "    여기서는 후보끼리의 IC 차이만 읽고, t는 읽지 않는다.")

    # ── 사전등록한 기각 조건 — 사람이 표를 해석하게 두지 않는다 ──
    # **두 자의 결과를 모두 찍는다.** 판정에 쓰는 자만 보이면, 자를 옮겼다는 사실이
    # 출력에서 사라져 다음 사람이 "처음부터 5년이었다"고 읽는다.
    ret = {"1년": "fwd_12m"}.get(args.adjudicate_on, f"fwd_{args.adjudicate_on}")
    if ret not in {r for r, _ in out}:
        print(f"\n{NA} 기각 조건: 보유 {args.adjudicate_on}을 재지 않았다"
              " (--no-horizon을 켜면 1년만 잰다).")
        return 0
    print(f"\n기각 조건 — **보유 {args.adjudicate_on} 기준으로 판정한다**")
    print("─" * 76)
    print("  참고: 최초 사전등록은 1년이었고, 그 결과를 본 뒤 5년으로 옮겼다."
          " 두 자를 모두 적는다.")
    for r in dict.fromkeys(k for k, _ in out):   # 키가 (지평, 후보)라 지평이 중복된다
        if r not in ("fwd_12m", ret):
            continue
        b_, l_ = out[(r, BASE_LABEL)], out[(r, LIT_LABEL)]
        tag = "← 판정" if r == ret else "  (최초 사전등록)"
        lab = "1년" if r == "fwd_12m" else r.replace("fwd_", "")
        print(f"    {lab:<4} Δρ {l_['rho'] - b_['rho']:+.3f} · "
              f"Δ③IC {l_['ic'] - b_['ic']:+.3f}   {tag}")
    print()
    base, lit = out[(ret, BASE_LABEL)], out[(ret, LIT_LABEL)]
    d_rho, d_ic = lit["rho"] - base["rho"], lit["ic"] - base["ic"]
    c1 = np.isfinite(d_rho) and d_rho >= RHO_RISE_LIMIT
    c2 = np.isfinite(d_ic) and d_ic < 0
    print(f"  ① ρ(③,1/PBR) 상승 {d_rho:+.3f}  (기준 +{RHO_RISE_LIMIT:.2f} 이상)"
          f"   → {'걸림' if c1 else '통과'}")
    print(f"  ② ③ 단독 IC 변화 {d_ic:+.3f}  (기준 하락)"
          f"           → {'걸림' if c2 else '통과'}")
    if c1 and c2:
        print(f"\n  {BAD} 둘 다 걸렸다 — **0.62 채택을 보류하고 현행 0.8을 유지한다.**")
        print("       ADR-0039에 '문헌과 축 독립성이 충돌한다'고 적고 그대로 남긴다.")
        return 1
    if c1 or c2:
        print(f"\n  {OK} 하나만 걸렸다 — 기각은 아니다. **걸린 쪽을 ADR-0039에 그대로 적는다.**")
    else:
        print(f"\n  {OK} 둘 다 통과 — 문헌값 0.62를 채택해도 축이 망가지지 않는다.")
    print("\n  주의: 이 표는 w를 고르는 근거가 아니다. 후보는 문헌값 하나였고,")
    print("        여기서 잰 것은 그것을 채택했을 때의 부작용뿐이다(ADR-0003).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
