"""미국 시장 provider — yfinance 단일 소스, 피어는 S&P500 GICS 분류 기반."""
from __future__ import annotations

import yfinance as yf

from .base import (DataProvider, build_peer_table, extract_financials,
                   extract_ttm, fetch_index_prices, fetch_info_metrics,
                   fetch_prices, trim_peers)
from .models import CompanyData
from .universe import detect_financial, find_us, peers_us_by_sector, select_peers_us


def _ai_classify_us(name: str, hint_industry: str):
    """(sector, industry, [심볼]) — Gemini 사용 가능 시 동종기업 심볼, 아니면 (None,None,None)."""
    try:
        from .gemini import is_available
        if not is_available():
            return None, None, None
        from ..analysis.ai_analysis import classify_peers
        c = classify_peers(name, "US", hint_industry)
    except Exception:
        return None, None, None
    syms: list[str] = []
    for p in c.get("peers", []):
        tk = (p.get("ticker", "") if isinstance(p, dict) else str(p)).strip().upper().replace(".", "-")
        # 심볼 형태만 채택 (검증은 이후 info 조회 실패 시 자동 탈락)
        if tk and tk.replace("-", "").isalnum() and len(tk) <= 6 and tk not in syms:
            syms.append(tk)
    return c.get("sector"), c.get("industry"), syms


class USProvider(DataProvider):
    market = "US"
    benchmark_name = "S&P 500"

    def resolve(self, query: str) -> dict:
        q = query.strip()
        hits = find_us(q)
        if len(hits) > 0:
            row = hits.iloc[0]
            return {"ticker": row["Symbol"], "yahoo_ticker": row["Symbol"],
                    "name": row["Name"], "sector": row["Sector"],
                    "sub_industry": row["SubIndustry"], "in_sp500": True}
        # S&P500 밖이어도 심볼 형식이면 직접 조회 허용
        if q.replace("-", "").replace(".", "").isalnum() and len(q) <= 6:
            sym = q.upper().replace(".", "-")
            return {"ticker": sym, "yahoo_ticker": sym, "name": sym,
                    "sector": None, "sub_industry": None, "in_sp500": False}
        raise ValueError(f"'{query}'에 해당하는 미국 종목을 찾지 못했습니다. "
                         "티커(예: AAPL) 또는 회사명을 입력하세요.")

    def load(self, query: str, peer_count: int = 10) -> CompanyData:
        meta = self.resolve(query)
        sym = meta["yahoo_ticker"]
        warnings: list[str] = []

        tk = yf.Ticker(sym)
        # 상장폐지·거래정지 종목은 재무·시세 수집이 모두 실패한다. 시세 조회로 먼저 감지해
        # 명확히 안내한다(파산한 옛 전기차 SPAC 등). 여기서 받은 시세는 아래에서 재사용.
        try:
            prices = fetch_prices(sym)
        except Exception:
            raise ValueError(
                f"'{sym}' 시세·재무 데이터를 찾을 수 없습니다 — 상장폐지·거래정지 상태이거나 "
                "잘못된 티커일 수 있어요. (파산·폐지된 종목은 Yahoo Finance에 데이터가 남지 않아 "
                "분석할 수 없습니다.)")

        financials, w = extract_financials(tk)
        warnings += w

        self_info = fetch_info_metrics(sym)
        name = meta["name"] if meta["in_sp500"] else (self_info.get("name") or sym)
        sector = meta["sector"] or self_info.get("sector") or ""
        industry = self_info.get("industry") or meta.get("sub_industry") or ""

        price = float(prices.iloc[-1])
        shares = self_info.get("shares")
        mcap = self_info.get("market_cap") or (price * shares if shares else None)
        if not shares or not mcap:
            raise ValueError(
                f"'{name}({sym})' 주식수·시가총액을 확인하지 못했습니다 — 상장폐지·거래정지 "
                "종목이거나 데이터가 불완전할 수 있어요.")

        ttm, w = extract_ttm(tk, shares)
        warnings += w

        index_prices = fetch_index_prices("^GSPC")

        # 피어 선정: (1순위) AI 업종분류 → (폴백) S&P500 GICS 세부산업/섹터
        ai_sector, ai_industry, ai_syms = _ai_classify_us(name, industry or sector)
        if ai_syms and len(ai_syms) >= 4:
            cands = [sym] + [s for s in ai_syms if s != sym]
            basis = f"AI 업종분류 '{ai_sector or ai_industry}'"
            sector = ai_sector or sector
            industry = ai_industry or industry
        elif meta["in_sp500"]:
            cands, basis = select_peers_us(sym, n=peer_count)
        else:
            cands, basis = peers_us_by_sector(sym, sector, n=peer_count)
            if basis is None:
                warnings.append("S&P500 밖 종목이라 업종 피어를 구성하지 못했습니다.")
        peers = build_peer_table(cands[: peer_count + 8], sym, labels=None)
        peers = trim_peers(peers, sym, peer_count)
        if basis:
            warnings.append(f"피어 기준: {basis}, {len(peers)}개 종목")
        if len(peers) < 4:
            warnings.append("피어 표본이 적어 업종 비교의 신뢰도가 낮습니다.")

        official = {
            "PER": self_info.get("per"), "PBR": self_info.get("pbr"),
            "DIV": self_info.get("div_yield"), "beta": self_info.get("beta"),
            "source": "Yahoo Finance",
            "데이터출처": {
                "주가": "Yahoo Finance 수정종가",
                "주식수·시가총액": "Yahoo Finance",
                "재무제표": "Yahoo Finance",
                "공식 멀티플": "Yahoo Finance",
                "피어 선정": basis or "업종 분류 불명",
                "피어 지표": "Yahoo Finance",
            },
        }

        return CompanyData(
            ticker=meta["ticker"], yahoo_ticker=sym, name=name, market="US",
            currency="USD", sector=sector, industry=industry,
            price=price, market_cap=float(mcap), shares_outstanding=float(shares),
            financials=financials, ttm=ttm, prices=prices,
            index_prices=index_prices, benchmark_name="S&P 500",
            peers=peers, official=official, warnings=warnings,
            is_financial=detect_financial(sector, industry, "US"),
        )
