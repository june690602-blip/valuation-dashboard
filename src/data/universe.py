"""종목 유니버스: 상장 목록·섹터 분류·피어 후보 선정.

- KR: FinanceDataReader KRX 목록(시총·시장) + KRX-DESC(섹터/업종) 병합, pykrx로 공식 멀티플
- US: 위키피디아 S&P 500 구성종목 (GICS Sector / Sub-Industry)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .cache import file_cache

# ── 한국 ────────────────────────────────────────────────────────────────
@file_cache("kr_listing", ttl_hours=24)
def get_kr_listing() -> pd.DataFrame:
    """KRX 상장 목록: Code, Name, Market, Sector(업종분류), SubSector, Marcap, Stocks, Close.

    KRX-DESC의 'Industry' 컬럼이 실제 산업분류(96% 채워짐)이므로 이를 Sector로 사용한다.
    (KRX-DESC의 'Sector' 컬럼은 67%만 채워진 보조 분류 → SubSector)
    """
    import FinanceDataReader as fdr

    try:
        base = fdr.StockListing("KRX")      # 공식 종가·시총·주식수
    except Exception as exc:
        # FinanceDataReader 일부 버전은 연결 실패 뒤 내부 지역변수 오류를 다시 내보내
        # 실제 원인을 가린다. 사용자에게 복구 가능한 메시지를 보여주고 원 예외는 보존한다.
        raise RuntimeError(
            "KRX 종목 목록을 가져오지 못했습니다. 인터넷 연결 또는 KRX 서비스 상태를 "
            "확인한 뒤 잠시 후 다시 시도하세요."
        ) from exc
    base = base.rename(columns={"Symbol": "Code"})
    keep = [c for c in ["Code", "Name", "Market", "Marcap", "Stocks", "Close"] if c in base.columns]
    base = base[keep].copy()

    try:
        desc = fdr.StockListing("KRX-DESC")
        desc = desc.rename(columns={"Symbol": "Code",
                                    "Industry": "Sector", "Sector": "SubSector"})
        cols = [c for c in ["Code", "Sector", "SubSector"] if c in desc.columns]
        if "Sector" in cols:
            base = base.merge(desc[cols], on="Code", how="left")
    except Exception:
        pass
    for c in ("Sector", "SubSector"):
        if c not in base.columns:
            base[c] = np.nan

    base = base.dropna(subset=["Code", "Name"])
    base["Code"] = base["Code"].astype(str).str.zfill(6)
    # 우선주·스팩 등 코드 끝자리가 0이 아닌 종목은 피어 후보에서 제외
    base["is_common"] = base["Code"].str.endswith("0") & ~base["Name"].str.contains("스팩", na=False)
    return base.reset_index(drop=True)


def yahoo_ticker_kr(code: str, market: str) -> str:
    return f"{code}.KQ" if str(market).upper().startswith("KOSDAQ") else f"{code}.KS"


def find_kr(query: str) -> pd.DataFrame:
    """6자리 코드 또는 종목명(부분 일치)으로 검색. 정확명 일치 우선."""
    listing = get_kr_listing()
    q = query.strip()
    if q.isdigit() and len(q) <= 6:
        return listing[listing["Code"] == q.zfill(6)]
    exact = listing[listing["Name"] == q]
    if len(exact) > 0:
        return exact
    part = listing[listing["Name"].str.contains(q, case=False, na=False, regex=False)]
    return part.sort_values("Marcap", ascending=False) if "Marcap" in part.columns else part


@file_cache("kr_etf", ttl_hours=24 * 7)
def get_kr_etf() -> pd.DataFrame:
    """국내 ETF 목록: Code, Name, MarCap(억원). 검색 자동완성용. 실패 시 빈 DF.

    주식 밸류에이션 대상은 아니지만(재무제표 없음) 검색에는 노출해, 고르면 포트폴리오로 안내한다.
    """
    import FinanceDataReader as fdr

    try:
        e = fdr.StockListing("ETF/KR")
    except Exception:
        return pd.DataFrame(columns=["Code", "Name", "MarCap"])
    e = e.rename(columns={"Symbol": "Code"})
    keep = [c for c in ["Code", "Name", "MarCap"] if c in e.columns]
    e = e[keep].dropna(subset=["Code", "Name"]).copy()
    e["Code"] = e["Code"].astype(str).str.zfill(6)
    return e.reset_index(drop=True)


def select_peers_kr(code: str, n: int = 10) -> list[str]:
    """같은 섹터에서 시총 상위 n개 (자기 자신 포함, 보통주만)."""
    listing = get_kr_listing()
    me = listing[listing["Code"] == code]
    if me.empty:
        return [code]
    sector = me.iloc[0].get("Sector")
    pool = listing[listing["is_common"]]
    if isinstance(sector, str) and sector.strip():
        pool = pool[pool["Sector"] == sector]
    else:
        return [code]
    if "Marcap" in pool.columns:
        pool = pool.sort_values("Marcap", ascending=False)
    codes = pool["Code"].tolist()
    if code not in codes:
        codes = [code] + codes
    top = codes[:n]
    if code not in top:
        top = [code] + top[: n - 1]
    return top


# ── 미국 ────────────────────────────────────────────────────────────────
@file_cache("sp500", ttl_hours=24 * 7)
def get_sp500() -> pd.DataFrame:
    """S&P 500 구성종목: Symbol, Name, Sector(GICS), SubIndustry."""
    import io

    import requests

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=30,
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
    except Exception as exc:
        raise RuntimeError(
            "S&P 500 종목 목록을 가져오지 못했습니다. 인터넷 연결 또는 데이터 원본 상태를 "
            "확인한 뒤 잠시 후 다시 시도하세요."
        ) from exc
    df = tables[0]
    df = df.rename(columns={
        "Symbol": "Symbol", "Security": "Name",
        "GICS Sector": "Sector", "GICS Sub-Industry": "SubIndustry",
    })
    df = df[["Symbol", "Name", "Sector", "SubIndustry"]].dropna(subset=["Symbol"])
    # 야후 표기 (BRK.B → BRK-B)
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
    return df.reset_index(drop=True)


SP400_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
SP600_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"


def _wiki_index_table(url: str) -> pd.DataFrame:
    """위키백과 지수 구성종목 표 → Symbol·Sector·SubIndustry.

    페이지마다 표가 여러 개다(구성종목·편입편출 이력·각주). 순서에 기대지 않고
    'Symbol'과 'GICS Sector'를 **둘 다** 가진 표를 골라야 구성종목 표를 집는다 —
    S&P 400 페이지는 편입편출 이력 표가 구성종목 표보다 행이 많다.
    """
    import io

    import requests

    resp = requests.get(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=30)
    resp.raise_for_status()
    for t in pd.read_html(io.StringIO(resp.text)):
        cols = [str(c) for c in t.columns]
        if "Symbol" in cols and "GICS Sector" in cols:
            out = t[["Symbol", "GICS Sector", "GICS Sub-Industry"]].copy()
            out.columns = ["Symbol", "Sector", "SubIndustry"]
            # 야후는 클래스 구분에 하이픈을 쓴다(BRK.B → BRK-B)
            out["Symbol"] = out["Symbol"].astype(str).str.replace(".", "-", regex=False)
            return out
    raise RuntimeError(f"구성종목 표를 찾지 못했습니다: {url}")


@file_cache("sp1500", ttl_hours=24 * 7)
def get_sp1500() -> pd.DataFrame:
    """S&P 500 + 400 MidCap + 600 SmallCap = 약 1,500종목.

    ①의 회귀(ADR-0014)가 학습 표본으로 쓴다. S&P 500만 쓰면 시총 하한이 $7.0B이라
    그보다 작은 종목이 전부 외삽이 되는데, 한국 전 종목 실측에서 **대형주만으로 학습한
    계수는 규모 효과를 크게 과소추정**했다(β 0.066(상위 65) → 0.161(상위 500) →
    0.276(전체)). 게다가 그 예측 편향이 전부 양수라, 외삽은 소형주를 체계적으로
    '저평가' 쪽으로 밀어 올린다 — 이 ADR이 없애려는 바로 그 편향이다.
    400·600을 더하면 하한이 $0.54B로 내려가 외삽 구간이 대부분 사라진다.

    세 목록 모두 같은 위키백과 표 스키마라 새 의존성이 없다. 한 지수를 못 받아도
    나머지로 진행한다 — 표본이 조금 줄 뿐 계수는 여전히 설 수 있다.
    """
    frames = [get_sp500()[["Symbol", "Sector", "SubIndustry"]]]
    for url in (SP400_URL, SP600_URL):
        try:
            frames.append(_wiki_index_table(url))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True).drop_duplicates("Symbol")


def find_us(query: str) -> pd.DataFrame:
    """심볼 또는 회사명으로 S&P500 내 검색. 없으면 빈 DF (직접 티커 사용은 허용)."""
    sp = get_sp500()
    q = query.strip()
    exact = sp[sp["Symbol"].str.upper() == q.upper()]
    if len(exact) > 0:
        return exact
    return sp[sp["Name"].str.contains(q, case=False, na=False, regex=False)]


# 대표 미국 ETF(심볼, 이름) — 검색 자동완성용 큐레이션. 전체 목록(fdr ETF/US)은 콜드 수집이
# 수십 초라 라이브에서 자동완성을 막으므로, 거래대금 상위·대표 상품만 정적으로 둔다(네트워크 0).
US_ETFS: tuple = (
    ("SPY", "SPDR S&P 500 ETF"), ("VOO", "Vanguard S&P 500 ETF"), ("IVV", "iShares Core S&P 500 ETF"),
    ("VTI", "Vanguard Total Stock Market ETF"), ("QQQ", "Invesco QQQ Trust (Nasdaq 100)"),
    ("QQQM", "Invesco Nasdaq 100 ETF"), ("DIA", "SPDR Dow Jones Industrial Average ETF"),
    ("IWM", "iShares Russell 2000 ETF"), ("VUG", "Vanguard Growth ETF"), ("VTV", "Vanguard Value ETF"),
    ("SCHD", "Schwab US Dividend Equity ETF"), ("VYM", "Vanguard High Dividend Yield ETF"),
    ("VIG", "Vanguard Dividend Appreciation ETF"), ("DGRO", "iShares Core Dividend Growth ETF"),
    ("DVY", "iShares Select Dividend ETF"), ("HDV", "iShares Core High Dividend ETF"),
    ("JEPI", "JPMorgan Equity Premium Income ETF"), ("JEPQ", "JPMorgan Nasdaq Equity Premium Income ETF"),
    ("SCHG", "Schwab US Large-Cap Growth ETF"), ("SCHX", "Schwab US Large-Cap ETF"),
    ("VGT", "Vanguard Information Technology ETF"), ("XLK", "Technology Select Sector SPDR"),
    ("XLF", "Financial Select Sector SPDR"), ("XLE", "Energy Select Sector SPDR"),
    ("XLV", "Health Care Select Sector SPDR"), ("XLY", "Consumer Discretionary Select Sector SPDR"),
    ("XLP", "Consumer Staples Select Sector SPDR"), ("XLI", "Industrial Select Sector SPDR"),
    ("XLU", "Utilities Select Sector SPDR"), ("XLB", "Materials Select Sector SPDR"),
    ("XLRE", "Real Estate Select Sector SPDR"), ("XLC", "Communication Services Select Sector SPDR"),
    ("SMH", "VanEck Semiconductor ETF"), ("SOXX", "iShares Semiconductor ETF"),
    ("VEA", "Vanguard FTSE Developed Markets ETF"), ("VWO", "Vanguard FTSE Emerging Markets ETF"),
    ("VXUS", "Vanguard Total International Stock ETF"), ("EFA", "iShares MSCI EAFE ETF"),
    ("IEMG", "iShares Core MSCI Emerging Markets ETF"), ("EEM", "iShares MSCI Emerging Markets ETF"),
    ("BND", "Vanguard Total Bond Market ETF"), ("AGG", "iShares Core US Aggregate Bond ETF"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF"), ("IEF", "iShares 7-10 Year Treasury Bond ETF"),
    ("SHY", "iShares 1-3 Year Treasury Bond ETF"), ("LQD", "iShares Investment Grade Corporate Bond ETF"),
    ("HYG", "iShares High Yield Corporate Bond ETF"), ("TIP", "iShares TIPS Bond ETF"),
    ("BIL", "SPDR 1-3 Month T-Bill ETF"), ("GLD", "SPDR Gold Shares"), ("IAU", "iShares Gold Trust"),
    ("SLV", "iShares Silver Trust"), ("USO", "United States Oil Fund"),
    ("ARKK", "ARK Innovation ETF"), ("VNQ", "Vanguard Real Estate ETF"),
    ("SCHF", "Schwab International Equity ETF"), ("SCHB", "Schwab US Broad Market ETF"),
    ("SPLG", "SPDR Portfolio S&P 500 ETF"), ("SPYG", "SPDR Portfolio S&P 500 Growth ETF"),
    ("SPYD", "SPDR Portfolio S&P 500 High Dividend ETF"), ("RSP", "Invesco S&P 500 Equal Weight ETF"),
    ("TQQQ", "ProShares UltraPro QQQ (3x)"), ("SQQQ", "ProShares UltraPro Short QQQ (3x)"),
    ("SOXL", "Direxion Daily Semiconductor Bull 3x"), ("UPRO", "ProShares UltraPro S&P500 (3x)"),
    # 2배도 함께 — 3배만 있으면 "QLD는 왜 안 나오나" 같은 혼란이 생긴다.
    ("QLD", "ProShares Ultra QQQ (2x)"), ("SSO", "ProShares Ultra S&P500 (2x)"),
    ("BITO", "ProShares Bitcoin Strategy ETF"), ("IBIT", "iShares Bitcoin Trust"),
    ("MOAT", "VanEck Morningstar Wide Moat ETF"), ("QUAL", "iShares MSCI USA Quality Factor ETF"),
    ("MTUM", "iShares MSCI USA Momentum Factor ETF"), ("USMV", "iShares MSCI USA Min Vol Factor ETF"),
)


def get_us_etf() -> pd.DataFrame:
    """대표 미국 ETF 목록: Symbol, Name (정적 큐레이션 → 즉시). 검색 자동완성용.

    파일 캐시를 쓰지 않는다 — 소스 상수를 그대로 옮기는 즉시 연산이라 캐시할 게 없고,
    캐시를 걸면 목록에 종목을 추가해도 만료 전까지 반영되지 않아 "분명 넣었는데 검색이
    안 된다"가 된다(실측: QLD 추가 후 7일 캐시 때문에 옛 목록이 계속 나왔다).
    """
    return pd.DataFrame(list(US_ETFS), columns=["Symbol", "Name"])


def select_peers_us(symbol: str, n: int = 10) -> tuple[list[str], str | None]:
    """S&P500 내 같은 GICS Sub-Industry 우선, 부족하면 같은 Sector로 보충.

    반환: (야후 티커 후보 목록, 피어 기준 설명)
    """
    symbol = symbol.upper()
    sp = get_sp500()
    me = sp[sp["Symbol"] == symbol]
    if me.empty:
        return [symbol], None  # S&P500 밖 → provider에서 info 섹터로 재시도
    sub, sec = me.iloc[0]["SubIndustry"], me.iloc[0]["Sector"]
    cands = sp[sp["SubIndustry"] == sub]["Symbol"].tolist()
    basis = f"GICS 세부산업 '{sub}'"
    if len(cands) < max(4, min(n, 6)):
        extra = sp[(sp["Sector"] == sec) & (~sp["Symbol"].isin(cands))]["Symbol"].tolist()
        cands = cands + extra[: max(0, int(n * 1.5) - len(cands))]
        basis = f"GICS 섹터 '{sec}' (세부산업 표본 부족으로 확장)"
    if symbol not in cands:
        cands = [symbol] + cands
    return cands, basis


def peers_us_by_sector(symbol: str, sector: str, n: int = 10) -> tuple[list[str], str | None]:
    """S&P500 밖 종목: info 섹터와 GICS 섹터를 매칭해 후보 구성 (근사)."""
    sp = get_sp500()
    # yahoo 섹터명 → GICS 섹터명 근사 매핑
    m = {
        "Technology": "Information Technology", "Financial Services": "Financials",
        "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples",
        "Healthcare": "Health Care", "Communication Services": "Communication Services",
        "Industrials": "Industrials", "Energy": "Energy", "Utilities": "Utilities",
        "Basic Materials": "Materials", "Real Estate": "Real Estate",
    }
    gics = m.get(sector, sector)
    pool = sp[sp["Sector"] == gics]["Symbol"].tolist()
    if not pool:
        return [symbol], None
    cands = [symbol] + pool[: int(n * 1.5)]
    return cands, f"GICS 섹터 '{gics}' (S&P500 밖 종목이라 섹터 기준 근사)"


# ── 공통 ────────────────────────────────────────────────────────────────
KR_FINANCIAL_KEYWORDS = ["은행", "보험", "금융", "증권", "신탁", "투자", "캐피탈", "카드"]
US_FINANCIAL_SECTORS = ["Financial Services", "Financials"]


def detect_financial(sector: str | None, industry: str | None, market: str) -> bool:
    text = f"{sector or ''} {industry or ''}"
    if market == "KR":
        return any(k in text for k in KR_FINANCIAL_KEYWORDS)
    return (sector or "") in US_FINANCIAL_SECTORS or "Insurance" in text or "Bank" in text
