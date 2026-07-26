"""시장(KR/US) 무관 공통 데이터 모델.

분석 엔진(src/analysis)은 이 모듈의 CompanyData만 입력받는다.
새 시장을 추가할 때는 provider만 구현하면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# financials DataFrame 표준 컬럼 (index=회계연도 int, 과거→최신 순)
# 금액 항목은 해당 시장 통화 절대값 기준 (KR: 원, US: 달러)
FIN_COLUMNS = [
    "revenue",              # 매출액
    "gross_profit",         # 매출총이익
    "operating_income",     # 영업이익
    "net_income",           # 당기순이익
    "ebitda",               # EBITDA
    "pretax_income",        # 법인세차감전이익
    "tax_expense",          # 법인세비용
    "interest_expense",     # 이자비용 (양수)
    "total_assets",         # 자산총계
    "total_equity",         # 자본총계(지배)
    "total_liabilities",    # 부채총계
    "current_assets",       # 유동자산
    "current_liabilities",  # 유동부채
    "total_debt",           # 이자부차입금 (총차입금)
    "cash",                 # 현금및현금성자산(+단기금융)
    "ocf",                  # 영업활동현금흐름
    "capex",                # 설비투자 (양수로 정규화)
    "fcf",                  # 잉여현금흐름 (ocf - capex)
    "da",                   # 감가상각비
    "eps",                  # 주당순이익 (기본)
    "shares_outstanding",   # 기간 평균 유통주식수 (역사적 주당가치 계산용)
    "dividends_paid",       # 배당금 지급 (양수로 정규화)
]

# 피어 비교 테이블 표준 컬럼 (index=티커)
PEER_COLUMNS = [
    "name", "market_cap", "per", "forward_per", "pbr", "psr", "ev_ebitda",
    "div_yield", "roe", "roa", "gross_margin", "op_margin", "net_margin",
    "rev_growth", "earnings_growth", "debt_to_equity", "current_ratio",
    "fcf_yield", "ocf_yield", "beta", "is_self",
]


class IsETFError(ValueError):
    """기업 분석 질의가 사실은 ETF였을 때.

    '못 찾음'과 구분하려고 따로 둔다 — 호출부(서버·프런트)가 이걸 보고 오류를 띄우는 대신
    ETF 분석(3축)으로 자동 전환한다. kind=etf가 안 붙은 옛 링크·직접 입력 URL 대비.
    """


def recomm_label(score: float | None) -> str | None:
    """투자의견 점수(1~5, 5=적극매수 통일 척도) → 한국어 라벨."""
    if score is None:
        return None
    if score >= 4.5:
        return "적극매수"
    if score >= 3.5:
        return "매수"
    if score >= 2.5:
        return "중립"
    if score >= 1.5:
        return "매도"
    return "적극매도"


@dataclass
class Consensus:
    """애널리스트 컨센서스(시장 추정치) — 커버리지가 없으면 필드별 None.

    recomm_score는 시장 무관 1~5 통일 척도(5=적극매수). KR(FnGuide)은 원래
    5=매수 척도라 그대로, US(yfinance)는 1=매수 척도라 provider에서 6-x로 뒤집는다.
    """

    forward_eps: float | None = None    # 12개월 선행 EPS (해당 통화, 주당)
    forward_per: float | None = None    # 선행 PER (참고 표시용)
    target_mean: float | None = None    # 목표주가 평균
    target_high: float | None = None
    target_low: float | None = None
    n_analysts: int | None = None       # 추정 참여 애널리스트 수 (KR은 미제공)
    recomm_score: float | None = None   # 1~5 (5=적극매수)
    recomm_label: str | None = None
    as_of: str = ""                     # 집계 기준일 (있을 때만)
    source: str = ""

    def has_any(self) -> bool:
        return any(v is not None for v in
                   (self.forward_eps, self.target_mean, self.recomm_score))


@dataclass
class CompanyData:
    """한 기업의 분석에 필요한 모든 원천 데이터."""

    ticker: str                 # 사용자 입력 기준 (KR: 6자리 코드, US: 심볼)
    yahoo_ticker: str           # 야후 조회용 (예: 005930.KS, AAPL)
    name: str
    market: str                 # 'KR' | 'US'
    currency: str               # 'KRW' | 'USD'
    sector: str
    industry: str
    price: float                # 현재가(최근 종가)
    market_cap: float
    shares_outstanding: float

    financials: pd.DataFrame    # 연간 재무 (FIN_COLUMNS, 과거→최신)
    ttm: pd.Series | None       # TTM (손익·현금흐름=최근4분기 합, 재무상태=최근분기)
    prices: pd.Series           # 최근 5년 일별 수정종가
    index_prices: pd.Series     # 벤치마크 지수 종가
    benchmark_name: str         # 'KOSPI' | 'KOSDAQ' | 'S&P 500'

    peers: pd.DataFrame         # PEER_COLUMNS, 자기 자신 포함(is_self=True)

    official: dict = field(default_factory=dict)   # 공식/참고 지표 (pykrx PER 등)
    warnings: list = field(default_factory=list)   # 데이터 품질 경고 문구
    is_financial: bool = False                     # 금융업 여부 (지표 마스킹용)
    consensus: Consensus | None = None             # 애널리스트 컨센서스 (없으면 None)

    def latest(self, col: str):
        """TTM 우선, 없으면 최근 연간 값."""
        if self.ttm is not None and col in self.ttm.index and pd.notna(self.ttm[col]):
            return float(self.ttm[col])
        if col in self.financials.columns and len(self.financials) > 0:
            s = self.financials[col].dropna()
            if len(s) > 0:
                return float(s.iloc[-1])
        return None

    def annual(self, col: str) -> pd.Series:
        """연간 시계열 (결측 제거)."""
        if col in self.financials.columns:
            return self.financials[col].dropna()
        return pd.Series(dtype=float)


@dataclass
class ETFData:
    """ETF 하나의 분석에 필요한 원천 데이터 (기업 재무가 없어 CompanyData와 별도).

    기업 밸류에이션(적정가 4방법)이 적용 불가하므로, ETF는 세 축으로 본다 —
    ① 괴리(현재가 vs NAV) ② 바스켓 상대지표(vs 벤치마크) ③ 배당수익률 역사밴드.
    무료 데이터(yfinance) 기준 결측이 흔하므로 대부분 None 허용.
    """

    ticker: str                 # 사용자 입력 (US: 심볼, KR: 6자리)
    yahoo_ticker: str
    name: str
    market: str                 # 'KR' | 'US'
    currency: str               # 'KRW' | 'USD'
    price: float                # 현재가(최근 종가)

    prices: pd.Series           # 최근 5년 일별 종가 (52주·추세·배당수익률밴드용)
    dividends: pd.Series         # 배당 지급 이력 (index=지급일, value=주당배당) — 없으면 빈 시리즈
    index_prices: pd.Series     # 벤치마크 지수/ETF 종가 (상대성과용)
    benchmark_name: str = ""

    nav: float | None = None            # 순자산가치(EOD NAV; US=navPrice, KR=iNAV)
    category: str | None = None         # 예: 'Large Blend' / 'Long Government'
    basket_pe: float | None = None      # 바스켓 PER (trailingPE) — 채권/원자재는 None/무의미
    basket_pb: float | None = None      # 바스켓 PBR (priceToBook)
    div_yield: float | None = None      # 현재 배당수익률 (0~1)
    aum: float | None = None            # 순자산총액(totalAssets)
    expense_ratio: float | None = None  # 총보수 (있을 때만)

    # 벤치마크 ETF의 현재 지표 (횡단면 비교용) — provider가 유형에 맞는 벤치마크로 채움
    bench_pe: float | None = None
    bench_yield: float | None = None
    bench_label: str = ""               # 예: '미국 전체시장(VTI)'

    # 구성·성과 (탭 콘텐츠용, 결측 흔함)
    top_holdings: list = field(default_factory=list)   # [{'symbol','name','weight'}] 비중 내림차순
    sectors: dict = field(default_factory=dict)         # {sector_key: weight}  예: {'technology': 0.385}
    asset_classes: dict = field(default_factory=dict)   # {class_key: weight}  예: {'bondPosition': 0.996}
    ret_ytd: float | None = None                        # 연초대비 수익률
    ret_3y: float | None = None                         # 3년 연율화
    ret_5y: float | None = None                         # 5년 연율화

    # 아래는 한국 ETF(네이버)에서만 채워지는 값 — 미국(yfinance)은 제공하지 않아 None으로 둔다.
    # 미국은 괴리·추적오차를 우리가 시세로 '추정'하지만, 한국은 운용사 공시값이 그대로 나와
    # 더 정확하다. 값이 있으면 분석 계층이 추정치 대신 이 공시값을 우선 쓴다.
    deviation_rate: float | None = None     # 공시 괴리율(비율, 예 -0.0013 = -0.13%)
    tracking_error_pub: float | None = None  # 공시 추적오차율(비율, 예 0.0039 = 0.39%)
    base_index: str | None = None            # 기초지수명 (예: '코스피 200')
    issuer: str | None = None                # 운용사
    listed_date: str | None = None           # 상장일 'YYYYMMDD'
    summary: str | None = None               # 운용사 제공 ETF 설명
    countries: dict = field(default_factory=dict)   # {국가코드: 비중}  예: {'KR': 0.9894}
    net_inflow: dict = field(default_factory=dict)  # 기간별 누적 순유입 {'1m': '5,265억', ...}
    basket_note: str | None = None           # 바스켓 지표 산출 근거(예: 상위10 가중평균) — 추정치일 때 표기

    warnings: list = field(default_factory=list)
