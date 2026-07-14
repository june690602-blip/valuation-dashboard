"""포트폴리오 헤드리스 검증 — 수기 계산 대조 + (옵션) 실데이터 확인.

실행: python scripts/check_portfolio.py          (수학만)
      python scripts/check_portfolio.py --data   (yfinance 실데이터까지)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from src.analysis.portfolio import (after_tax_row, annualize, monthly_returns_krw,
                                    performance, portfolio_point, portfolio_series)

fails = 0


def ok(name, cond, detail=""):
    global fails
    print(f"  {'✓' if cond else '✗ FAIL'} {name} {detail}")
    if not cond:
        fails += 1


print("[1] 2자산 수기 대조: w=(0.6,0.4), σ=(20%,10%), ρ=0.3")
mu = pd.Series({"A": 0.08, "B": 0.04})
sd = np.array([0.20, 0.10])
rho = 0.3
cov = pd.DataFrame([[sd[0] ** 2, rho * sd[0] * sd[1]],
                    [rho * sd[0] * sd[1], sd[1] ** 2]], index=["A", "B"], columns=["A", "B"])
w = pd.Series({"A": 0.6, "B": 0.4})
p = portfolio_point(w, mu, cov)
er_hand = 0.6 * 0.08 + 0.4 * 0.04                      # 6.4%
var_hand = 0.36 * 0.04 + 0.16 * 0.01 + 2 * 0.6 * 0.4 * rho * 0.2 * 0.1
ok("E(r) = 6.40%", abs(p["er"] - er_hand) < 1e-12, f"→ {p['er']*100:.2f}%")
ok("σ = √0.01888 = 13.74%", abs(p["sigma"] - np.sqrt(var_hand)) < 1e-12,
   f"→ {p['sigma']*100:.2f}%")

print("[2] 성과지표: p = 0.5·b (노이즈 없음) → β=0.5, 젠센=CAPM 잔차")
rng = np.random.default_rng(7)
b = pd.Series(rng.normal(0.008, 0.05, 60))
p_m = 0.5 * b
perf = performance(p_m, b, rf=0.03)
ok("β = 0.5", abs(perf["beta"] - 0.5) < 1e-9, f"→ {perf['beta']:.4f}")
jensen_hand = perf["er_p"] - (0.03 + 0.5 * (perf["er_b"] - 0.03))
ok("젠센α 일치", abs(perf["jensen"] - jensen_hand) < 1e-12, f"→ {perf['jensen']*100:+.2f}%p")
ok("샤프 = (Rp−Rf)/σp", abs(perf["sharpe"] - (perf["er_p"] - 0.03) / perf["sigma_p"]) < 1e-12)
ok("M² = Rf + 샤프·σb", abs(perf["m2"] - (0.03 + perf["sharpe"] * perf["sigma_b"])) < 1e-12)

print("[3] 무위험 자산 합류: 상수 월수익 → σ=0, 상관 0 처리")
dates = pd.date_range("2021-01-31", periods=48, freq="ME")
px = pd.Series(100 * np.cumprod(1 + rng.normal(0.006, 0.04, 480)),
               index=pd.date_range("2021-01-01", periods=480, freq="B"))
m = monthly_returns_krw({"S": px}, None, {"S": "KRW"}, months=36, cash_rates={"CASH": 0.03})
st = annualize(m)
ok("예금 σ = 0", abs(st["sigma"]["CASH"]) < 1e-12, f"→ {st['sigma']['CASH']:.2e}")
ok("예금 μ = 3%", abs(st["mu"]["CASH"] - 0.03) < 1e-12)
ok("상관행렬 NaN 없음", not st["corr"].isna().any().any())

print("[4] 포트폴리오 시계열 = Σ wᵢrᵢ")
ps = portfolio_series(pd.Series({"S": 0.7, "CASH": 0.3}), m)
hand = 0.7 * m["S"] + 0.3 * m["CASH"]
ok("가중합 일치", np.allclose(ps.values, hand.values))

print("[5] 세금 어림 규칙")
r = after_tax_row("예금", 0.03)
ok("예금 실효세율 15.4%", abs(r["eff_rate"] - 0.154) < 1e-9, f"→ {r['eff_rate']*100:.1f}%")
r = after_tax_row("국내주식", 0.08)                      # 인컴 2% 가정 → 세금 = 2%×15.4%
ok("국내주식: 배당분만 과세", abs(r["mu_after"] - (0.08 - 0.02 * 0.154)) < 1e-9,
   f"→ 세후 {r['mu_after']*100:.2f}%")
r = after_tax_row("해외주식", 0.10)                      # 인컴 1.3% → 세금=8.7%×22%+1.3%×15%
hand_tax = (0.10 - 0.013) * 0.22 + 0.013 * 0.15
ok("해외주식: 양도 22%+배당 15%", abs(r["mu_after"] - (0.10 - hand_tax)) < 1e-9,
   f"→ 세후 {r['mu_after']*100:.2f}%")
r = after_tax_row("국내기타ETF", -0.05)
ok("손실이면 세금 0", abs(r["mu_after"] - (-0.05)) < 1e-9)

r = after_tax_row("국내주식형ETF", 0.08, income_yield=0.02)
ok("국내주식형 ETF: 매매차익 비과세", abs(r["mu_after"] - (0.08 - 0.02 * 0.154)) < 1e-9)

if "--data" in sys.argv:
    print("[6] 실데이터: 국채ETF·금·환율 원화 월간 수익률")
    from src.data.base import fetch_prices
    prices, cur = {}, {}
    for name, yt, ccy in [("국고채10Y", "148070.KS", "KRW"), ("IEF", "IEF", "USD"),
                          ("금현물", "411060.KS", "KRW"), ("달러", "KRW=X", "KRW")]:
        try:
            prices[name] = fetch_prices(yt, "5y")
            cur[name] = ccy
        except Exception as e:
            print(f"  (수집 실패: {name} — {e})")
    fx = prices.get("달러")
    m = monthly_returns_krw(prices, fx, cur, months=60)
    st = annualize(m)
    print(f"  표본 {st['n_months']}개월, 자산 {list(m.columns)}")
    for k in m.columns:
        print(f"    {k}: μ={st['mu'][k]*100:+.1f}%  σ={st['sigma'][k]*100:.1f}%")
    ok("자산 3개 이상 합류", len(m.columns) >= 3)
    ok("채권ETF σ < 10% (상식 범위)",
       all(st["sigma"][k] < 0.10 for k in m.columns if "국고채" in k))

print(f"\n{'모든 검증 통과 ✓' if fails == 0 else f'실패 {fails}건 ✗'}")
sys.exit(1 if fails else 0)
