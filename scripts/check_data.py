"""데이터 레이어 헤드리스 검증: python scripts/check_data.py [KR|US] [query]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
pd.set_option("display.float_format", lambda x: f"{x:,.3f}")


def main(market: str, query: str):
    if market == "KR":
        from src.data.kr_provider import KRProvider
        p = KRProvider()
    else:
        from src.data.us_provider import USProvider
        p = USProvider()

    d = p.load(query, peer_count=10)
    print(f"=== {d.name} ({d.ticker} / {d.yahoo_ticker}) [{d.market}] ===")
    print(f"섹터: {d.sector} | 업종: {d.industry} | 금융업: {d.is_financial}")
    print(f"현재가: {d.price:,.0f} {d.currency} | 시총: {d.market_cap:,.0f} | 주식수: {d.shares_outstanding:,.0f}")
    print(f"벤치마크: {d.benchmark_name} ({len(d.index_prices)}일) | 주가 {len(d.prices)}일")
    print(f"공식/참고 지표: {d.official}")
    print("\n--- 연간 재무 (최근 3개년, 주요 항목) ---")
    cols = ["revenue", "operating_income", "net_income", "total_equity",
            "total_debt", "cash", "interest_expense", "ocf", "fcf", "eps"]
    print(d.financials[cols].tail(3).T)
    print("\n--- TTM ---")
    print(d.ttm if d.ttm is not None else "(없음 → 연간 폴백)")
    print(f"\n--- 피어 {len(d.peers)}개 ---")
    print(d.peers[["name", "market_cap", "per", "pbr", "roe", "op_margin", "rev_growth"]])
    if d.warnings:
        print("\n[경고]")
        for w in d.warnings:
            print(" -", w)


if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else "KR"
    query = sys.argv[2] if len(sys.argv) > 2 else ("005930" if market == "KR" else "AAPL")
    main(market, query)
