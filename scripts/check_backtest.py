"""백테스트 헤드리스 검증: python scripts/check_backtest.py [KR|US] [query] [PER|PBR]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")


def main(market, query, kind):
    from src.analysis.backtest import run_backtest
    if market == "KR":
        from src.data.kr_provider import KRProvider
        p = KRProvider()
    else:
        from src.data.us_provider import USProvider
        p = USProvider()
    d = p.load(query, peer_count=8)
    r = run_backtest(d, kind=kind, threshold=0.30)
    print(f"=== {d.name} ({d.ticker}) | {kind} 백테스트 | 표본 {r.n_obs}일 ===")
    print(f"ok={r.ok} | 저평가 임계 +{r.threshold*100:.0f}% | 신호 {r.signal_days}일 | "
          f"spearman(저평가율 vs 12M수익, 양수=툴유효)={r.spearman}")
    print("\n[이벤트 스터디: 저평가 매수 후 평균수익 / 승률 / 표본  vs  전체 평균]")
    for hz in ("3개월", "6개월", "12개월"):
        ev, bs = r.event_stats.get(hz, {}), r.baseline_stats.get(hz, {})
        def pc(x):
            return f"{x*100:+.1f}%" if isinstance(x, (int, float)) else "N/A"
        print(f"  {hz}: 신호 {pc(ev.get('mean'))} (승률 {pc(ev.get('hit'))}, n={ev.get('n',0)})"
              f"  vs 전체 {pc(bs.get('mean'))}")
    print("\n[누적수익 CAGR]")
    for k, v in r.cagr.items():
        print(f"  {k}: {v*100:.1f}%" if v is not None else f"  {k}: N/A")
    if r.equity is not None:
        print("\n[누적수익 마지막값]")
        print(r.equity.iloc[-1].round(2).to_string())
    for w in r.warnings:
        print("  [경고]", w)


if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else "KR"
    query = sys.argv[2] if len(sys.argv) > 2 else ("005930" if market == "KR" else "AAPL")
    kind = sys.argv[3] if len(sys.argv) > 3 else "PER"
    main(market, query, kind)
