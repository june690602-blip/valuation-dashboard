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
