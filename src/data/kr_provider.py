"""한국 시장 provider.

- 재무제표·시세(차트/베타용): yfinance (005930.KS / 293490.KQ)
- 시총·상장주식수·공식종가: FinanceDataReader KRX 목록 (KRX 공식)
- 공식 멀티플(PER/PBR/EPS/BPS/배당): 네이버 금융 API
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf

from .base import (DataProvider, build_peer_table, extract_financials,
                   fill_self_from_financials,
                   extract_ttm, fetch_index_prices, fetch_prices, trim_peers)
from .models import FIN_COLUMNS, CompanyData, Consensus, recomm_label
from .naver import fetch_naver_fundamental
from .opendart import get_dart_financials
from .universe import (detect_financial, find_kr, get_kr_listing,
                       select_peers_kr, yahoo_ticker_kr)


def _ai_classify_kr(name: str, hint_industry: str, listing: pd.DataFrame):
    """(sector, industry, [코드]) — Gemini 사용 가능 시 동종기업 코드, 아니면 (None,None,None)."""
    try:
        from ..data.gemini import is_available
        if not is_available():
            return None, None, None
        from ..analysis.ai_analysis import classify_peers
        c = classify_peers(name, "KR", hint_industry)
    except Exception:
        return None, None, None
    codes: list[str] = []
    for p in c.get("peers", []):
        nm = p.get("name", "") if isinstance(p, dict) else str(p)
        tk = "".join(ch for ch in str(p.get("ticker", "") if isinstance(p, dict) else "") if ch.isdigit())
        code = None
        if len(tk) == 6 and tk in listing.index:
            code = tk
        elif nm:
            try:
                hit = find_kr(nm)
                if not hit.empty:
                    code = hit.iloc[0]["Code"]
            except Exception:
                code = None
        if code and code in listing.index and code not in codes:
            codes.append(code)
    return c.get("sector"), c.get("industry"), codes


def merge_financials(dart: pd.DataFrame, yf_fin: pd.DataFrame) -> pd.DataFrame:
    """DART(우선) + yfinance(보완) 병합. DART에 없는 항목(EBITDA·차입금·CAPEX 등)은 yfinance로 채움."""
    merged = dart.combine_first(yf_fin)
    # 파생 컬럼이 양쪽 원본에 모두 없더라도 아래 재계산이 동작하도록 먼저 표준 스키마를 맞춘다.
    for c in FIN_COLUMNS:
        if c not in merged.columns:
            merged[c] = np.nan
    # 파생 항목 재계산 (영업이익·감가상각·현금흐름 출처가 섞였으므로)
    need = merged["ebitda"].isna() & merged["operating_income"].notna() & merged["da"].notna()
    merged.loc[need, "ebitda"] = merged["operating_income"] + merged["da"]
    need = merged["ocf"].notna() & merged["capex"].notna()
    merged.loc[need, "fcf"] = merged["ocf"] - merged["capex"]
    return merged.sort_index()


def _kr_etf_name(query: str) -> str | None:
    """질의가 국내 ETF(6자리 코드 또는 정확한 이름)면 그 이름, 아니면 None. 실패해도 None."""
    try:
        from .universe import get_kr_etf
        etfs = get_kr_etf()
        if not len(etfs):
            return None
        q = query.strip()
        m = etfs[etfs["Code"] == q.zfill(6)] if q.isdigit() else etfs[etfs["Name"].str.upper() == q.upper()]
        return str(m.iloc[0]["Name"]) if len(m) else None
    except Exception:
        return None


class KRProvider(DataProvider):
    market = "KR"

    def resolve(self, query: str) -> dict:
        hits = find_kr(query)
        if hits.empty:
            etf_name = _kr_etf_name(query)
            if etf_name:  # ETF는 재무제표가 없어 밸류에이션 불가 — 일반 '못 찾음'과 구분해 안내
                raise ValueError(f"'{etf_name}'은(는) ETF예요 — 이 페이지는 기업 재무 기반 밸류에이션이라 "
                                 "ETF는 분석하지 않습니다. 검색에서 ETF를 선택하면 포트폴리오에 담아드려요.")
            raise ValueError(f"'{query}'에 해당하는 한국 종목을 찾지 못했습니다. "
                             "6자리 코드(예: 005930) 또는 정확한 종목명을 입력하세요.")
        row = hits.iloc[0]

        def _s(v):
            return v if isinstance(v, str) else ""

        return {
            "ticker": row["Code"],
            "yahoo_ticker": yahoo_ticker_kr(row["Code"], row.get("Market", "KOSPI")),
            "name": row["Name"],
            "krx_market": str(row.get("Market", "KOSPI")),
            "sector": _s(row.get("Sector")),
            "industry": _s(row.get("SubSector")),
            "shares": float(row["Stocks"]) if pd.notna(row.get("Stocks")) else None,
            "marcap": float(row["Marcap"]) if pd.notna(row.get("Marcap")) else None,
        }

    def load(self, query: str, peer_count: int = 10,
             exclude: tuple = (), extra: tuple = ()) -> CompanyData:
        meta = self.resolve(query)
        code, yt = meta["ticker"], meta["yahoo_ticker"]
        warnings: list[str] = []

        tk = yf.Ticker(yt)
        financials, w = extract_financials(tk)
        warnings += w

        # OpenDART 공시 원본으로 연간 재무 보강 (키 있을 때). yfinance는 결측 보완용.
        fin_source = "Yahoo Finance"
        try:
            dart_fin, dart_src, dart_w = get_dart_financials(code)
        except Exception:
            dart_fin, dart_src, dart_w = None, "", []
        warnings += dart_w
        if dart_fin is not None and not dart_fin.empty:
            financials = merge_financials(dart_fin, financials)
            fin_source = dart_src
            warnings.append(f"재무제표: {dart_src} {dart_fin.shape[0]}개년 사용 "
                            "(EBITDA·차입금 등 일부는 yfinance로 보완)")

        prices = fetch_prices(yt)
        price = float(prices.iloc[-1])

        shares = meta["shares"]
        shares_source = "KRX 상장목록"
        if not shares:
            info = tk.info or {}
            shares = info.get("sharesOutstanding")
            shares_source = "Yahoo Finance"
        if not shares:
            raise ValueError(f"{meta['name']}({code}) 상장주식수를 확인하지 못했습니다.")
        # 내부 일관성을 위해 시총 = 공식 주식수 × 최근 종가 (공식 시총은 참고치로 보관)
        mcap = shares * price

        ttm, w = extract_ttm(tk, shares)
        warnings += w

        official: dict = {"source": None}
        consensus: Consensus | None = None
        try:
            nv = fetch_naver_fundamental(code)
            official = {
                "PER": nv.get("per"), "선행PER": nv.get("forward_per"),
                "PBR": nv.get("pbr"), "EPS": nv.get("eps"), "BPS": nv.get("bps"),
                "DIV": nv.get("div_yield"), "DPS": nv.get("dps"),
                "시가총액": nv.get("market_cap") or meta["marcap"],
                "source": nv.get("source"),
            }
            score = nv.get("recomm_score")   # FnGuide 척도 = 이미 5=적극매수
            consensus = Consensus(
                forward_eps=nv.get("forward_eps"),
                forward_per=nv.get("forward_per"),
                target_mean=nv.get("target_mean"),
                recomm_score=score, recomm_label=recomm_label(score),
                as_of=nv.get("consensus_date") or "",
                source="네이버금융 · FnGuide 컨센서스(국내 42개 증권사 리포트 추정치 평균)",
            )
            if not consensus.has_any():
                consensus = None
                warnings.append("애널리스트 컨센서스가 없는 종목입니다 "
                                "(커버리지 없음 — 소형주에 흔함).")
        except Exception:
            warnings.append("네이버 금융 지표 조회 실패 — 재무제표 기반 계산값만 사용합니다.")
        official["재무출처"] = fin_source

        benchmark = "KOSDAQ" if meta["krx_market"].upper().startswith("KOSDAQ") else "KOSPI"
        index_prices = fetch_index_prices("^KQ11" if benchmark == "KOSDAQ" else "^KS11")

        # 피어: (1순위) AI 업종분류 동종기업 → (폴백) KRX 업종분류 시총 상위
        listing = get_kr_listing().set_index("Code")
        sector, industry = meta["sector"], meta["industry"]
        ai_sector, ai_industry, ai_codes = _ai_classify_kr(meta["name"], sector, listing)
        if ai_codes and len(ai_codes) >= 4:
            peer_codes = [code] + [c for c in ai_codes if c != code]
            sector = ai_sector or sector
            industry = ai_industry or industry
            peer_basis = f"AI 업종분류 '{ai_sector or industry}'"
        else:
            peer_codes = select_peers_kr(code, n=peer_count + 5)
            peer_basis = f"KRX 업종분류 '{sector}'" if sector else "업종분류 불명"

        # 사용자 피어 편집 — 제외는 다운로드 전에 걸러 낭비를 없애고, 추가는 검색으로
        # 코드를 찾아 뒤에 붙인다. 편집 사실은 피어 기준 문구에 남긴다(재현성·투명성).
        if exclude:
            ex = {str(e).strip() for e in exclude if str(e).strip()}
            def _kept(c):
                nm = str(listing.loc[c]["Name"]) if c in listing.index else ""
                return c not in ex and nm not in ex
            peer_codes = [c for c in peer_codes if c == code or _kept(c)]
        added_codes: list[str] = []
        if extra:
            for q2 in extra:
                q2 = str(q2).strip()
                if not q2:
                    continue
                try:
                    hit = find_kr(q2)
                    c2 = str(hit.iloc[0]["Code"]) if not hit.empty else None
                except Exception:
                    c2 = None
                if c2 and c2 in listing.index and c2 not in peer_codes:
                    peer_codes.append(c2)
                    added_codes.append(c2)
                else:
                    warnings.append(f"피어 추가 실패: '{q2}' — 상장목록에서 찾지 못했습니다.")
        added = len(added_codes)
        if exclude or added:
            peer_basis += f" · 사용자 편집(제외 {len(exclude)}·추가 {added})"

        peer_yts, labels = [], {}
        for c in peer_codes:
            if c in listing.index:
                r = listing.loc[c]
                pyt = yahoo_ticker_kr(c, r.get("Market", "KOSPI"))
                peer_yts.append(pyt)
                labels[pyt] = r["Name"]
        peers_full = build_peer_table(peer_yts, yt, labels)
        peers_full = self._patch_kr_peers(peers_full, listing)
        peers = trim_peers(peers_full, yt, peer_count + added)
        # 사용자가 추가한 피어는 시총 컷에 잘리지 않게 고정(핀)
        if added:
            pin = [yahoo_ticker_kr(c, listing.loc[c].get("Market", "KOSPI")) for c in added_codes]
            back = [t for t in pin if t in peers_full.index and t not in peers.index]
            if back:
                peers = pd.concat([peers, peers_full.loc[back]])
        peers = fill_self_from_financials(peers, yt, financials, mcap)
        warnings.append(f"피어 기준: {peer_basis}, {len(peers)}개 종목")
        if len(peers) < 4:
            warnings.append("같은 업종 피어가 적어 업종 비교의 신뢰도가 낮습니다.")
        official["데이터출처"] = {
            "주가": "Yahoo Finance 수정종가",
            "주식수": shares_source,
            "시가총액": "최근 주가 × 상장주식수",
            "재무제표": fin_source,
            "공식 멀티플": official.get("source") or "재무제표 기반 계산",
            "피어 선정": peer_basis,
            "피어 지표": "Yahoo Finance, 결측 시 KRX·네이버 금융 보완 · 자사 심층 지표는 재무제표로 보완",
        }

        return CompanyData(
            ticker=code, yahoo_ticker=yt, name=meta["name"], market="KR",
            currency="KRW", sector=sector, industry=industry,
            price=price, market_cap=float(mcap), shares_outstanding=float(shares),
            financials=financials, ttm=ttm, prices=prices,
            index_prices=index_prices, benchmark_name=benchmark,
            peers=peers, official=official, warnings=warnings,
            is_financial=detect_financial(meta["sector"], meta["industry"], "KR"),
            consensus=consensus,
        )

    @staticmethod
    def _patch_kr_peers(peers: pd.DataFrame, listing: pd.DataFrame) -> pd.DataFrame:
        """yfinance info 결측을 KRX(시총)·네이버(멀티플) 값으로 보정."""
        if peers.empty:
            return peers
        for yt in peers.index:
            code = str(yt).split(".")[0]

            def _fill(col, val):
                if val is not None and pd.isna(peers.at[yt, col]):
                    peers.at[yt, col] = val

            if code in listing.index and pd.notna(listing.loc[code].get("Marcap")):
                _fill("market_cap", float(listing.loc[code]["Marcap"]))
            try:
                nv = fetch_naver_fundamental(code)
            except Exception:
                continue
            _fill("per", nv.get("per"))
            _fill("forward_per", nv.get("forward_per"))
            _fill("pbr", nv.get("pbr"))
            _fill("div_yield", nv.get("div_yield"))
            _fill("roe", nv.get("roe_approx"))
        return peers
