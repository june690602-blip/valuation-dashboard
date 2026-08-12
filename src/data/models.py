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
    # ── 아래 셋은 "장부가가 회사의 가치를 담고 있는가"를 판별하려고 받는다(RIM 적용 여부).
    # 장부가가 과소평가되는 경로는 둘이다: ㉠ 브랜드·특허가 장부에 안 잡힘(무형자산)
    # ㉡ 자사주를 사서 자본이 줄어듦. 둘을 각각 재야 PBR이 높은 이유를 가릴 수 있다.
    # 한국은 재무 본체를 DART에서 받지만 이 셋은 yfinance에서 온다 — 총자산·자본이 두 소스에서
    # 일치함을 확인하고 쓴다(실측 4종목 0.00% 차이).
    "goodwill",             # 영업권
    "intangibles",          # 무형자산 (영업권 포함 총액)
    "buyback",              # 자사주 매입 현금유출 (양수로 정규화)
]

# 피어 비교 테이블 표준 컬럼 (index=티커)
PEER_COLUMNS = [
    "name", "market_cap", "per", "forward_per", "pbr", "psr", "ev_ebitda",
    "div_yield", "roe", "roa", "gross_margin", "op_margin", "net_margin",
    "rev_growth", "earnings_growth", "debt_to_equity", "current_ratio",
    "fcf_yield", "ocf_yield", "beta", "is_self",
]


def actual_prices(d) -> pd.Series:
    """'그날 실제로 붙어 있던 가격'이 필요한 계산용 시세 — 미조정, 없으면 수정종가로 폴백.

    CompanyData·ETFData 공용. 어느 필드를 읽을지 고르는 접근 로직이라(CompanyData.latest()와
    같은 성격) 분석 계층이 아니라 여기 둔다.

    prices(수정종가)는 과거 가격을 그 뒤 지급된 배당만큼 낮춰 잡는다. 수익률·베타처럼
    총수익 기준 계산에는 그게 맞지만, '그때 그 가격이면 PER가 몇 배였나 / 배당수익률이
    몇 %였나'를 묻는 계산에 쓰면 과거 값이 배당 누적분만큼 왜곡된다(실측: TLT 5년 18.7%,
    KB금융 26.6%). 방향이 늘 같아서 '현재가가 비싸 보이는' 체계적 편향이 된다.
    """
    raw = getattr(d, "prices_raw", None)
    if raw is not None and len(raw) > 0:
        return raw
    return d.prices


def currency_mismatch(d) -> str | None:
    """재무제표 통화가 주가 통화와 다르면 그 통화 코드, 같거나 알 수 없으면 None.

    ADR(외국 기업의 미국 상장 증서)이 대표적이다 — TSMC는 재무를 대만달러로 공시하는데
    주가는 달러다. 주가·시총을 재무 값으로 나눈 지표(PER·PBR·PSR·EV/EBITDA)는 그대로
    **환율배만큼** 틀어진다(실측: TSMC 자체 PER 0.93 vs 야후 공시 35.60).
    재무끼리의 비율(ROE·마진·성장률)은 통화가 약분돼 영향이 없다.

    환율 시계열로 변환하면 제대로 고칠 수 있지만, ADR 비율(1 증서 = 보통주 몇 주)을
    무료 소스가 주지 않아 주당 단위까지 맞추려면 별도 설계가 필요하다. 그때까지는
    틀린 값을 그럴듯하게 보여주는 대신 N/A로 막는다.
    """
    fin_ccy = str(getattr(d, "financial_currency", None) or "").upper()
    px_ccy = str(getattr(d, "currency", "") or "").upper()
    return fin_ccy if fin_ccy and px_ccy and fin_ccy != px_ccy else None


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
    prices: pd.Series           # 최근 5년 일별 수정종가 (수익률·베타 등 총수익 기준 계산용)
    index_prices: pd.Series     # 벤치마크 지수 종가
    benchmark_name: str         # 'KOSPI' | 'KOSDAQ' | 'S&P 500'

    peers: pd.DataFrame         # PEER_COLUMNS, 자기 자신 포함(is_self=True)

    # 미조정(실제 거래) 종가 — '그날 붙어 있던 가격'이 필요한 계산 전용(역사적 PER·PBR
    # 밴드, 52주 범위). 수정종가는 과거를 배당만큼 낮춰 잡아 과거 배수를 실제보다 낮게
    # 만들고, 그만큼 적정가가 낮아져 현재가가 비싸 보인다. 못 받으면 None → prices로 폴백.
    prices_raw: pd.Series | None = None

    # 거래소가 붙인 **공식 업종 분류** — KR은 KRX 표준산업분류, US는 GICS 섹터.
    #
    # 위 `sector`와 나뉘어 있는 이유가 이 필드의 전부다. `sector`는 **화면에 보여 주고
    # 피어를 고르는 데 쓰는 라벨**이라 Gemini가 더 그럴듯한 이름으로 덮어쓴다
    # (KRX가 삼성전자를 '통신 및 방송 장비 제조업'이라고 부르는 것이 그 동기였다).
    # 그런데 ①⑤의 적정 배수 회귀는 **KRX 라벨로 적합된 계수표**를 문자열로 조회한다
    # (ADR-0014: "KRX 업종 중 10곳 이상인 것만 더미"). AI가 지은 이름은 그 표에 없어
    # 전부 '기타'로 떨어졌고, 아무 예외도 나지 않아 조용했다 — ADR-0044.
    #
    # **회귀에 넣을 라벨은 언제나 이 필드다.** 표시·피어·프롬프트는 `sector`를 쓴다.
    # 비어 있으면(백테스트 패널처럼 애초에 AI를 안 타는 경로) 호출부가 `sector`로
    # 폴백하므로 기존 동작이 그대로 유지된다.
    sector_official: str = ""

    official: dict = field(default_factory=dict)   # 공식/참고 지표 (pykrx PER 등)
    warnings: list = field(default_factory=list)   # 데이터 품질 경고 문구
    is_financial: bool = False                     # 금융업 여부 (지표 마스킹용)
    consensus: Consensus | None = None             # 애널리스트 컨센서스 (없으면 None)

    # 재무제표가 공시된 통화. 위 currency(주가 통화)와 다르면 ADR 등 통화 혼재 상황이라
    # 주가÷재무 지표를 막아야 한다 — currency_mismatch() 참고. 모르면 None(=검사 안 함).
    financial_currency: str | None = None

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

    prices: pd.Series           # 최근 5년 일별 수정종가 (추세·상대성과·추적오차 = 총수익 기준)
    dividends: pd.Series         # 배당 지급 이력 (index=지급일, value=주당배당) — 없으면 빈 시리즈
    index_prices: pd.Series     # 벤치마크 지수/ETF 종가 (상대성과용)
    benchmark_name: str = ""

    # 미조정(실제 거래) 종가 — '그날 붙어 있던 가격'이 필요한 계산(배당수익률 밴드·52주
    # 밴드) 전용. 수정종가는 과거를 배당만큼 낮춰 잡아 그 계산을 왜곡한다. 못 받으면
    # None으로 두고 분석 계층이 prices로 폴백한다(정확도만 떨어지고 동작은 유지).
    prices_raw: pd.Series | None = None

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
