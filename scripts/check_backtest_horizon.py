"""보유기간을 늘리면 결론이 바뀌나 — 1·2·3·5년을 **전부** 낸다.

    python scripts/check_backtest_horizon.py
    python scripts/check_backtest_horizon.py --market US

사전등록: `docs/review/B1-백테스트-사전등록.md` **개정 2**.

ADR-0028의 결론은 전부 12개월 보유 기준이고, **그 12개월에 근거가 없었다.**
이 도구가 하는 주장은 *"지금 가격이 내재가치에서 벗어나 있다"*이지
*"1년 안에 수렴한다"*가 아니다. ⑤의 근거인 A&B(2006)도 여러 해 누적으로 쟀다.

**읽는 법 — 최고점이 아니라 모양이다**(개정 2):

    A 단조 증가 → 수렴이 천천히 일어난다 (가치 신호가 맞다)
    B 평평     → 1년 결론이 그대로 유효하다
    C 단조 감소 → 1년 효과는 단기 반등이었다 (결론을 좁혀야 한다)
    D 뒤집힘   → 과잉반응 교정 뒤 재이탈 (가장 나쁘다)

**겹치는 구간은 t값을 부풀린다.** 3년 보유를 매년 시작하면 인접 구간이 2년 겹친다.
그래서 비중복 부분표본을 함께 내고, **시점이 3개 이하인 칸은 t값을 아예 적지 않는다** —
적으면 없는 정밀도를 주장하는 것이다.
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
sys.path.insert(0, str(ROOT / "scripts"))

from check_backtest_combos import (AXES, BAND, EPV, NORM,  # noqa: E402
                                   REL, RIM, W, _plain, score_date)

HORIZONS = {"1년": 365, "2년": 730, "3년": 1095, "5년": 1826}
MIN_T_DATES = 4          # 이보다 적으면 t값을 내지 않는다 (개정 2)
SHORT = {REL: "①상대", BAND: "②밴드", RIM: "③RIM", NORM: "⑤정규", EPV: "EPV"}


def _t(x) -> float:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 2 or x.std(ddof=1) == 0:
        return np.nan
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def forward(px: pd.Series, t: pd.Timestamp, entry: float, days: int):
    """(수익률, 완주 여부). 폐지되면 정리매매 종가까지 실현하고 잔여는 현금."""
    end = t + pd.Timedelta(days=days)
    win = px[(px.index > t) & (px.index <= end)]
    if not len(win):
        return np.nan, False
    # 데이터가 그 기간을 덮지 못하면(아직 미래) 이 관측은 버린다 — 폐지와 구분해야 한다
    return float(win.iloc[-1] / entry - 1.0), True


def build(panel: pd.DataFrame, raw: Path) -> pd.DataFrame:
    """패널에 기간별 선행수익률을 붙인다. 주가는 수집 원본에서 다시 읽는다."""
    last_px = {}
    cache = {}
    for code in panel["code"].unique():
        p = raw / f"px_{code}.parquet"
        if not p.exists():
            continue
        s = pd.read_parquet(p)["close"]
        s.index = pd.to_datetime(s.index)
        cache[code] = s.sort_index()
        last_px[code] = s.index[-1]

    data_end = max(last_px.values())
    out = panel.copy()
    for label, days in HORIZONS.items():
        vals = []
        for r in panel.itertuples():
            s = cache.get(r.code)
            if s is None:
                vals.append(np.nan)
                continue
            horizon_end = r.date + pd.Timedelta(days=days)
            # 구간이 데이터 끝을 넘으면 아직 알 수 없다 — 폐지(정리매매 실현)와 다르다
            if horizon_end > data_end and last_px[r.code] >= data_end - pd.Timedelta(days=30):
                vals.append(np.nan)
                continue
            v, _ok = forward(s, r.date, r.price, days)
            vals.append(v)
        out[f"fwd_{label}"] = vals
    return out


def non_overlapping(years: list[int], step: int) -> list[int]:
    keep, nxt = [], -1
    for y in sorted(years):
        if y >= nxt:
            keep.append(y)
            nxt = y + step
    return keep


def report(panel: pd.DataFrame, name: str, fn, title: str) -> None:
    print(f"\n{title}")
    print(f"{'보유':<6}{'시점':>5}{'IC':>8}{'t':>7}{'Q5−Q1':>9}{'단조':>7}"
          f"{'양(+)':>7}   │{'비중복 시점':>10}{'IC':>8}{'t':>7}")
    print("─" * 76)
    for label, days in HORIZONS.items():
        col = f"fwd_{label}"
        rows = []
        for t, sub in panel.groupby("date"):
            g = sub.apply(fn, axis=1)
            s = score_date(g, sub[col])
            s["year"] = t.year
            rows.append(s)
        d = pd.DataFrame(rows).dropna(subset=["ic"])
        if d.empty:
            continue
        ic = d["ic"].to_numpy(float)
        tv = _t(ic) if len(d) >= MIN_T_DATES else np.nan
        step = max(1, round(days / 365))
        nz = non_overlapping(d["year"].tolist(), step)
        dn = d[d["year"].isin(nz)]
        icn = dn["ic"].to_numpy(float)
        tn = _t(icn) if len(dn) >= MIN_T_DATES else np.nan
        ts = f"{tv:>7.2f}" if np.isfinite(tv) else f"{'—':>7}"
        tns = f"{tn:>7.2f}" if np.isfinite(tn) else f"{'—':>7}"
        print(f"{label:<6}{len(d):>5}{ic.mean():>8.3f}{ts}"
              f"{np.nanmean(d['spread']):>8.1%}{np.nanmean(d['mono']):>7.2f}"
              f"{int((ic > 0).sum()):>4}/{len(d):<2}   │{len(dn):>10}"
              f"{icn.mean():>8.3f}{tns}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    args = ap.parse_args()
    base = ROOT / "data" / ("backtest" if args.market == "KR" else "backtest_us")
    p = base / "panel.parquet"
    if not p.exists():
        print(f"{p}가 없다 — 패널을 먼저 만들어라.")
        return 1
    panel = pd.read_parquet(p)
    panel["date"] = pd.to_datetime(panel["date"])
    for m in AXES:
        if m not in panel.columns:
            panel[m] = np.nan

    print(f"[{args.market}] 패널 {len(panel):,}행 · 시점 {panel['date'].nunique()}개")
    panel = build(panel, base / "raw")
    print("기간별 관측 수:", {k: int(panel[f'fwd_{k}'].notna().sum()) for k in HORIZONS})
    print("\n※ t값이 '—'인 칸은 시점이 3개 이하다 — 없는 정밀도를 주장하지 않으려고 비운다."
          "\n※ 겹침 허용 칸의 t는 부풀려져 있다. 오른쪽 비중복 칸과 함께 읽어라.")

    report(panel, "B1", _plain(W), "[B1 현행 4축] 보유기간별")
    for m in (REL, BAND, RIM, NORM, EPV):
        report(panel, m, _plain({m: 1.0}), f"[{SHORT[m]} 단독] 보유기간별")

    print("\n판정 기준은 **최고점이 아니라 모양**이다(사전등록 개정 2):")
    print("  A 단조증가=수렴이 느리다 · B 평평=1년 결론 유효 · "
          "C 단조감소=1년은 단기반등이었다 · D 뒤집힘=가장 나쁘다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
