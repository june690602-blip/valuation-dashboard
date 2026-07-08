"""분석 엔진 헤드리스 검증: python scripts/check_analysis.py [KR|US] [query]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")


def fmt(v, kind="num"):
    if v is None:
        return "N/A"
    if kind == "pct":
        return f"{v * 100:.2f}%"
    if kind == "x":
        return f"{v:.2f}배"
    return f"{v:,.2f}"


def main(market: str, query: str):
    from src.analysis.capital_cost import compute_capital_cost
    from src.analysis.commentary import build_commentary
    from src.analysis.indicators import compute_indicators
    from src.analysis.scoring import compute_scores
    from src.analysis.valuation import compute_valuation

    if market == "KR":
        from src.data.kr_provider import KRProvider
        p, rf, mrp = KRProvider(), 0.035, 0.06
    else:
        from src.data.us_provider import USProvider
        p, rf, mrp = USProvider(), 0.045, 0.05

    d = p.load(query, peer_count=10)
    print(f"=== {d.name} ({d.ticker}) | 현재가 {d.price:,.0f} {d.currency} ===\n")

    ind = compute_indicators(d)
    print("[밸류에이션]", {k: fmt(v, "x" if k != "div_yield" else "pct") for k, v in ind.valuation.items()})
    print("[수익성]  ", {k: fmt(v, "pct") for k, v in ind.profitability.items()})
    print("[성장성]  ", {k: fmt(v, "pct") for k, v in ind.growth.items()})
    print("[안정성]  ", {k: fmt(v, "pct" if k == "debt_ratio" else "x") for k, v in ind.stability.items()})
    print("[현금흐름]", {k: fmt(v, "pct" if "yield" in k or k == "ocf_ni" else "num") for k, v in ind.cashflow.items()})

    sc = compute_scores(d.peers, d.yahoo_ticker, d.is_financial)
    print(f"\n[카테고리 점수] (피어 {sc.n_peers}개 대비 백분위)")
    for cat, score in sc.scores.items():
        print(f"  {cat}: {fmt(score)}")
    print(f"  종합: {fmt(sc.overall)}")

    cc = compute_capital_cost(d, rf=rf, mrp=mrp)
    print(f"\n[자본비용] {cc.period_label}")
    print(f"  β_L(회귀)={fmt(cc.beta_l)} (원값 {fmt(cc.beta_l_raw)}, R²={fmt(cc.r2)}, n={cc.n_obs})")
    print(f"  β_U(무부채)={fmt(cc.beta_u)} | 세율={fmt(cc.tax_rate, 'pct')} | D/E={fmt(cc.de_ratio, 'pct')}")
    print(f"  k_U(영업위험만)={fmt(cc.k_u, 'pct')} | k_e={fmt(cc.k_e, 'pct')} | 재무위험프리미엄={fmt(cc.financial_risk_premium, 'pct')}")
    print(f"  k_d={fmt(cc.k_d, 'pct')} ({cc.k_d_source}) | WACC={fmt(cc.wacc, 'pct')}")
    print(f"  ROIC={fmt(cc.roic, 'pct')} | 스프레드={fmt(cc.spread, 'pct')}")

    val = compute_valuation(d, ind, r_equity=cc.k_e)
    print("\n[적정주가]")
    for e in val.estimates:
        print(f"  {e.method}: {e.low:,.0f} ~ {e.mid:,.0f} ~ {e.high:,.0f}  ({e.note})")
    print(f"  종합: {fmt(val.fair_mid)} | 괴리율 {fmt(val.gap, 'pct')} → {val.verdict} (신뢰도 {val.confidence})")
    print(f"  PER밴드 백분위={fmt(val.per_percentile)} PBR밴드 백분위={fmt(val.pbr_percentile)}")

    print("\n[해설]")
    for cm in build_commentary(d, ind, sc, cc, val):
        print(f"  ({cm.kind}) {cm.text}")

    if d.warnings or cc.warnings:
        print("\n[데이터 경고]")
        for w in d.warnings + cc.warnings:
            print("  -", w)


if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else "KR"
    query = sys.argv[2] if len(sys.argv) > 2 else ("005930" if market == "KR" else "AAPL")
    main(market, query)
