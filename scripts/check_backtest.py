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
    r = run_backtest(d, kind=kind)
    print(f"=== {d.name} ({d.ticker}) | {kind} 백테스트 | 표본 {r.n_obs}일 ===")
    print(f"ok={r.ok} | window={r.window_years}y | spearman(백분위 vs 12M수익)={r.spearman}")
    print("\n[구간별 평균 미래수익률]")
    if r.bucket_returns is not None:
        print((r.bucket_returns * 100).round(1).to_string())
    print("\n[구간별 플러스 확률]")
    if r.bucket_hit is not None:
        print((r.bucket_hit * 100).round(0).to_string())
    print("\n[구간별 표본수(12M)]", r.bucket_counts)
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
