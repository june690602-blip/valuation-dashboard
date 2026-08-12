"""미국 시장 provider — yfinance 단일 소스, 피어는 S&P500 GICS 분류 기반."""
from __future__ import annotations

import pandas as pd
import yfinance as yf

from .base import (PRICE_PERIOD, DataProvider, build_peer_table,
                   extract_financials, extract_ttm, fetch_index_prices,
                   fetch_info_metrics, fetch_price_pair,
                   fill_self_from_financials, trim_peers)
from .models import CompanyData, Consensus, recomm_label
from .parallel import gather
from .universe import detect_financial, find_us, peers_us_by_sector, select_peers_us


def _us_price_pair(sym: str):
    """(수정종가, 미조정종가|None, 경고) — 시세 실패가 **치명**인 것이 KR과 다른 점이다.

    상장폐지·거래정지 종목은 재무·시세가 모두 실패하는데 시세 쪽이 먼저, 그리고 확실히
    실패한다. 그래서 원인을 짚는 안내문을 여기서 만든다 — `Outcome.unwrap()`이 이 예외를
    타입·메시지 그대로 다시 올리므로, 병렬로 바뀌어도 사용자가 보는 문장은 같다.
    """
    try:
        return fetch_price_pair(sym, PRICE_PERIOD)
    except Exception:
        raise ValueError(
            f"'{sym}' 시세·재무 데이터를 찾을 수 없습니다 — 상장폐지·거래정지 상태이거나 "
            "잘못된 티커일 수 있어요. (파산·폐지된 종목은 Yahoo Finance에 데이터가 남지 않아 "
            "분석할 수 없습니다.)")


def _ai_classify_us(name: str, hint_industry: str):
    """(sector, industry, [심볼], 실패사유) — Gemini 사용 가능 시 동종기업 심볼.

    실패사유는 **시도했다가 실패했을 때만** 채운다(키 없음은 정상이라 None).
    한국 쪽 `_ai_classify_kr`과 같은 규칙이다 — 자세한 이유는 그쪽 도크스트링.
    """
    try:
        from .gemini import is_available
        if not is_available():
            return None, None, None, None
        from ..analysis.ai_analysis import classify_peers
        c = classify_peers(name, "US", hint_industry)
    except Exception as e:
        from .gemini import failure_reason
        return None, None, None, failure_reason(e)
    syms: list[str] = []
    for p in c.get("peers", []):
        tk = (p.get("ticker", "") if isinstance(p, dict) else str(p)).strip().upper().replace(".", "-")
        # 심볼 형태만 채택 (검증은 이후 info 조회 실패 시 자동 탈락)
        if tk and tk.replace("-", "").isalnum() and len(tk) <= 6 and tk not in syms:
            syms.append(tk)
    return c.get("sector"), c.get("industry"), syms, None


def _yf_fund_info(symbol: str) -> tuple[str | None, str | None]:
    """(quoteType, 이름) — yfinance가 알려주는 상품 종류. 실패하면 (None, None).

    quoteType은 'ETF' | 'MUTUALFUND' | 'EQUITY' … 로 온다. 두 값을 한 번에 돌려주는 건
    info 조회가 네트워크 왕복이라 종류 확인과 이름 조회를 나눠 부르면 두 번 나가기 때문이다.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        name = info.get("shortName") or info.get("longName")
        return info.get("quoteType"), (str(name) if name else None)
    except Exception:
        return None, None


def _us_etf_name(query: str) -> str | None:
    """질의가 미국 ETF·펀드면 그 이름, 아니면 None. 실패해도 None(기업 경로로 넘어감).

    1) 큐레이션 목록(US_ETFS)에서 심볼 정확 일치 — 네트워크 없이 즉시.
    2) 목록에 없으면 yfinance quoteType으로 확인.

    2단계가 없으면 목록(71개) 밖의 ETF가 전부 '기업'으로 취급돼 재무제표를 찾다가
    "연간 손익계산서를 가져오지 못했습니다" 오류로 끝난다 — 화면에서 볼 방법이 사라진다
    (실측: QLD·SSO). 목록은 자동완성용이라 인기 종목만 담기므로 판별까지 맡기면 안 된다.
    """
    try:
        from .universe import get_us_etf
        etfs = get_us_etf()
        m = etfs[etfs["Symbol"].str.upper() == query.upper()] if len(etfs) else etfs
        if len(m):
            return str(m.iloc[0]["Name"])
    except Exception:
        pass
    quote_type, name = _yf_fund_info(query)
    if quote_type in ("ETF", "MUTUALFUND"):
        return name or query.upper()
    return None


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
        # ETF는 재무제표가 없어 기업 밸류에이션 불가 — 아래 '직접 티커' 폴백으로 넘어가
        # 손익계산서 오류를 내기 전에 여기서 잡아 ETF 3축 분석으로 넘긴다.
        etf_name = _us_etf_name(q)
        if etf_name:
            from .models import IsETFError
            raise IsETFError(f"'{etf_name}({q.upper()})'은(는) ETF예요 — 기업 재무 대신 "
                             "ETF 분석(괴리·바스켓·배당밴드)으로 보여드릴게요.")
        # S&P500 밖이어도 심볼 형식이면 직접 조회 허용
        if q.replace("-", "").replace(".", "").isalnum() and len(q) <= 6:
            sym = q.upper().replace(".", "-")
            return {"ticker": sym, "yahoo_ticker": sym, "name": sym,
                    "sector": None, "sub_industry": None, "in_sp500": False}
        raise ValueError(f"'{query}'에 해당하는 미국 종목을 찾지 못했습니다. "
                         "티커(예: AAPL) 또는 회사명을 입력하세요.")

    def load(self, query: str, peer_count: int = 10,
             exclude: tuple = (), extra: tuple = ()) -> CompanyData:
        meta = self.resolve(query)
        sym = meta["yahoo_ticker"]
        warnings: list[str] = []

        # ── 이름·업종·주식수를 먼저 받는다 (ADR-0045) ────────────────────
        # US가 KR과 갈리는 자리다. `_ai_classify_us`는 **이름과 업종**을, `extract_ttm`은
        # **주식수**를 필요로 하는데 셋 다 `fetch_info_metrics`에서 나온다 — KR은 이것들이
        # 상장목록(`resolve`)에 있어 한 그룹으로 묶이지만 US는 그럴 수 없다.
        # 이 한 번(info 캐시 24시간)을 먼저 치르고 **나머지를 통째로 겹치는** 편이,
        # 실측 최대 병목인 Gemini를 뒤로 미루는 것보다 빠르다.
        #
        # **실패를 여기서 올리지 않는다.** 시세 실패가 더 친절한 안내문을 갖고 있어
        # 그쪽이 먼저 말해야 하기 때문이다(순차 시절의 순서 = 시세 → 재무 → info).
        # 실패한 경우 아래 태스크들은 빈 정보로 돌지만, 그 결과는 조인에서 버려진다.
        info_got = gather([("info", lambda: fetch_info_metrics(sym))])["info"]
        self_info = info_got.or_else({})

        name = meta["name"] if meta["in_sp500"] else (self_info.get("name") or sym)
        sector = meta["sector"] or self_info.get("sector") or ""
        industry = self_info.get("industry") or meta.get("sub_industry") or ""
        # AI가 덮어쓰기 **전**의 GICS 섹터를 붙잡아 둔다 — 회귀 계수표와 금융업 판별이
        # 조회하는 문자열이 이것이다(ADR-0044). KR은 `meta["sector"]`가 그 자리다.
        sector_official = sector
        shares = self_info.get("shares")

        # 이 다섯은 서로 모른다. 제출 순서 = 느린 것부터(Gemini가 단독 최대 병목이다).
        got = gather([
            ("ai", lambda: _ai_classify_us(name, industry or sector)),
            ("fin", lambda: extract_financials(yf.Ticker(sym))),
            ("price", lambda: _us_price_pair(sym)),
            # yfinance는 스레드 안전이 보장되지 않아 태스크마다 자기 Ticker를 만든다.
            ("ttm", lambda: extract_ttm(yf.Ticker(sym), shares)),
            ("index", lambda: fetch_index_prices("^GSPC", PRICE_PERIOD)),
        ], stage="자료 수집")

        # ── 조인 ─────────────────────────────────────────────────────────
        # **경고 순서는 순차 시절과 같아야 한다**(합격선: 페이로드 불변). 상장폐지 종목이
        # 보는 안내문의 우선순위도 여기서 지켜진다 — 시세가 먼저, 그 다음이 info다.
        # 미조정 종가는 역사적 PER·PBR 밴드와 52주 범위용이고, 실패해도 분석 계층이 폴백한다.
        prices, prices_raw, w = got["price"].unwrap()
        warnings += w

        financials, w = got["fin"].unwrap()
        warnings += w

        info_got.unwrap()      # info 자체가 실패했다면 이제 올린다(시세 안내문 다음이다)

        price = float(prices.iloc[-1])
        mcap = self_info.get("market_cap") or (price * shares if shares else None)
        if not shares or not mcap:
            raise ValueError(
                f"'{name}({sym})' 주식수·시가총액을 확인하지 못했습니다 — 상장폐지·거래정지 "
                "종목이거나 데이터가 불완전할 수 있어요.")

        ttm, w = got["ttm"].unwrap()
        warnings += w

        # ADR은 재무를 본국 통화로 공시하는데 주가는 달러다. 그대로 나누면 지표가 환율배만큼
        # 틀어지므로(실측: TSMC 자체 PER 0.93, 야후 공시 35.60) 분석 계층이 해당 지표를
        # 막을 수 있게 통화를 실어 보낸다. 환율 변환은 ADR 비율(1 증서 = 보통주 몇 주)이
        # 무료 소스에 없어 별도 설계가 필요하다.
        fin_ccy = self_info.get("financial_currency")
        if fin_ccy and str(fin_ccy).upper() != "USD":
            warnings.append(
                f"재무제표가 {fin_ccy}로 공시되는데 주가는 USD입니다(ADR 등) — 주가를 재무 값으로 "
                "나누는 지표(PER·PBR·PSR·EV/EBITDA)와 적정가 ①②③은 환율만큼 어긋나 "
                "N/A 처리합니다. 컨센서스 선행이익 방법만 사용합니다.")

        index_prices = got["index"].unwrap()

        # 피어 선정: (1순위) AI 업종분류 → (폴백) S&P500 GICS 세부산업/섹터
        ai_sector, ai_industry, ai_syms, ai_err = got["ai"].or_else((None, None, None, None))
        if ai_err:
            warnings.append(
                f"AI 업종분류를 시도했으나 실패해 GICS 분류로 대체했습니다({ai_err}). "
                "피어 구성이 달라지므로 업종 비교와 상대가치 결과가 평소와 다를 수 있습니다.")
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
        # 사용자 피어 편집 — 제외는 다운로드 전에 심볼로 거르고, 추가 심볼은 뒤에 붙인다.
        cands = cands[: peer_count + 8]
        added_syms: list[str] = []
        if exclude:
            ex = {str(e).strip().upper() for e in exclude if str(e).strip()}
            cands = [s for s in cands if s == sym or s.upper() not in ex]
        if extra:
            for q2 in extra:
                s2 = str(q2).strip().upper()
                if s2 and s2 != sym and s2 not in cands:
                    cands.append(s2)
                    added_syms.append(s2)
        added = len(added_syms)
        if (exclude or added) and basis:
            basis += f" · 사용자 편집(제외 {len(exclude)}·추가 {added})"

        peers_full = build_peer_table(cands, sym, labels=None)
        # 미국은 `trim_peers` 변경만 적용한다(ADR-0013 결정 넷) — S&P500 표에 시가총액
        # 열이 없어 다운로드 **전에** 규모로 고를 수 없다. 후보를 넓게 받아 다운로드 후
        # 여기서 인접순으로 고른다. 한국처럼 후보 단계까지 고치지는 못한다.
        peers = trim_peers(peers_full, sym, peer_count + added, self_mcap=mcap)
        # 사용자가 추가한 피어는 시총 컷에 잘리지 않게 고정(핀). 심볼 오타 등으로
        # 데이터가 아예 없으면 표에 못 실리므로 경고로 알린다.
        if added:
            back = [s for s in added_syms if s in peers_full.index and s not in peers.index]
            if back:
                peers = pd.concat([peers, peers_full.loc[back]])
            for s in added_syms:
                if s not in peers_full.index:
                    warnings.append(f"피어 추가 실패: '{s}' — 데이터를 찾지 못했습니다(심볼 확인).")
        peers = fill_self_from_financials(peers, sym, financials, mcap,
                                          fx_mixed=bool(fin_ccy and str(fin_ccy).upper() != "USD"))
        if basis:
            warnings.append(f"피어 기준: {basis}, {len(peers)}개 종목")
        if len(peers) < 4:
            warnings.append("피어 표본이 적어 업종 비교의 신뢰도가 낮습니다.")

        # 애널리스트 컨센서스 (야후 recommendationMean은 1=적극매수라 6-x로 통일 척도 변환)
        yf_rec = self_info.get("recomm_mean_yf")
        score = (6.0 - float(yf_rec)) if yf_rec is not None else None
        fwd_eps = self_info.get("forward_eps")
        consensus = Consensus(
            forward_eps=fwd_eps if fwd_eps and fwd_eps > 0 else None,
            forward_per=self_info.get("forward_per"),
            target_mean=self_info.get("target_mean"),
            target_high=self_info.get("target_high"),
            target_low=self_info.get("target_low"),
            n_analysts=self_info.get("n_analysts"),
            recomm_score=score, recomm_label=recomm_label(score),
            source="Yahoo Finance · LSEG I/B/E/S(글로벌 애널리스트 추정치 집계)",
        )
        if not consensus.has_any():
            consensus = None
            warnings.append("애널리스트 컨센서스가 없는 종목입니다 "
                            "(커버리지 없음 — 소형주에 흔함).")

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
            sector_official=sector_official,
            price=price, market_cap=float(mcap), shares_outstanding=float(shares),
            financials=financials, ttm=ttm, prices=prices, prices_raw=prices_raw,
            index_prices=index_prices, benchmark_name="S&P 500",
            peers=peers, official=official, warnings=warnings,
            # 금융업 판별도 **공식 라벨**로 한다. `US_FINANCIAL_SECTORS`는 GICS 문자열
            # 완전일치 검사라, AI가 'Financial Services'처럼 다르게 부르면 은행이
            # 일반기업으로 취급돼 EV/EBITDA·WACC 마스킹이 풀린다. KR은 이미 원본을
            # 쓰고 있었고(`meta["sector"]`), 그 불일치를 여기서 맞춘다(ADR-0044).
            is_financial=detect_financial(sector_official, industry, "US"),
            consensus=consensus, financial_currency=fin_ccy,
        )
