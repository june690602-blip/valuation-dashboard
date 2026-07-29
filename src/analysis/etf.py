"""ETF 적정가 분석 — 기업 밸류에이션(적정가 4방법)이 안 통하는 ETF를 세 축으로 본다.

입력은 ETFData 하나(순수 함수, 부작용 없음). 무료 데이터라 결측이 흔하므로
값이 없으면 None으로 두고 사유를 notes/masked에 남긴다(절대 크래시 내지 않음).

세 축(시간 프레이밍):
  ① 실시간 · 괴리   — 현재가 vs NAV 프리미엄/디스카운트. '제값(NAV)에 사고 있나'.
  ② 펀더멘털 · 상대 — 바스켓 PER·PBR·배당률을 벤치마크와 횡단면 비교. '자산군이 비싼가'.
  ③ 역사적 · 배당밴드 — 배당수익률의 자기 5년 분위. '역사적으로 싼 구간인가'(높은 수익률=쌈).
     (과거 바스켓 PER 시계열은 무료 데이터에 없어, 실제 계산되는 배당수익률로 역사밴드를 만든다.)

유형(category)별로 '주 신호'를 자동 분기한다:
  주식·가치·배당형 → ③ 배당밴드 주도 / 성장형(저배당) → ② 상대·추세 / 해외·레버리지·저유동 → ① 괴리
  채권형 → ① 괴리 + 수익률(PER 마스킹) / 원자재·기타 → 밸류에이션 스킵(괴리·추세만).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import ETFData, actual_prices


@dataclass
class ETFAxis:
    """UI 표시용 축 하나의 요약.

    pos·lead·weak는 화면이 축을 '글'이 아니라 '게이지'로 그리기 위한 값이다 — 서로 단위가
    다른 축(괴리 %·PER 배수·배당 분위·ERP %p)을 '싼(0)↔비쌈(100)' 한 자로 정규화해,
    네 축을 같은 방향으로 훑어볼 수 있게 한다."""
    key: str                    # 'premium' | 'relative' | 'dividend' | 'trend'
    title: str
    value: str                  # 사람이 읽는 대표값 (예: 'NAV 대비 -0.2%')
    note: str = ""
    available: bool = True
    pos: float | None = None    # 0=싼 구간 · 100=비싼 구간 (게이지 마커 위치)
    lead: str = ""              # 한 줄 결론 (숫자를 읽지 않아도 방향을 알게)
    weak: bool = False          # 신호가 약해 판단에 넣지 말아야 함(저배당·NAV 지연 등)


@dataclass
class ETFResult:
    symbol: str
    name: str
    currency: str
    price: float
    fund_type: str = "equity"          # bond|commodity|foreign_equity|leveraged|growth_equity|equity
    type_label: str = ""

    nav: float | None = None
    premium: float | None = None       # 현재가/NAV - 1  (+면 프리미엄=비싸게)

    div_yield: float | None = None      # 현재 배당수익률
    div_pct: float | None = None        # 현재 수익률의 5년 내 백분위(0~100, 높을수록 고배당=쌈)
    div_median: float | None = None
    div_gap: float | None = None        # 현재수익률/5년중앙값 - 1 (+면 역사 대비 쌈)

    basket_pe: float | None = None
    basket_pb: float | None = None
    bench_pe: float | None = None
    bench_label: str = ""
    pe_vs_bench: float | None = None    # 바스켓PER/벤치PER - 1 (+면 시장보다 비쌈)
    earnings_yield: float | None = None  # 1/바스켓PER (후행 이익수익률)
    erp: float | None = None            # 이익수익률 − 무위험이자율(10년 국채) = 금리 대비 초과 이익보상
    rel_ratio_pct: float | None = None  # (ETF/벤치) 총수익 가격비율의 5년 퍼센타일(높을수록 시장 대비 비쌈)
    stance: str | None = None           # 상대·역사 위치 ('5년 기준 비싼/싼/보통 구간입니다') — 적정가 아님
    stance_pos: float | None = None     # 0~100 (높을수록 비싼 구간) = rel_ratio_pct
    stance_reasons: list = field(default_factory=list)   # 상대 위치 근거 문구들
    signal_counts: dict = field(default_factory=dict)    # {'expensive'|'cheap'|'neutral': 축 개수}

    w52_low: float | None = None
    w52_high: float | None = None
    w52_pos: float | None = None        # 0~100 (52주 밴드 내 현재 위치)
    rel_1y: float | None = None         # 1년 벤치 대비 초과성과

    aum: float | None = None
    expense_ratio: float | None = None
    tracking_error: float | None = None   # 벤치마크 대비 추적오차 (연율화 표준편차)

    primary: str = "trend"             # 판정의 주 신호 축
    gap: float | None = None            # 판정바용 (+면 쌈): 축에 따라 괴리·배당갭
    verdict: str | None = None
    confidence: str = "낮음"
    axes: list = field(default_factory=list)     # [ETFAxis]
    notes: list = field(default_factory=list)
    masked: list = field(default_factory=list)


# ── 유형 분류 ────────────────────────────────────────────────────────
def classify(d: ETFData) -> tuple[str, str]:
    """category·이름으로 ETF 유형과 한국어 라벨을 정한다."""
    cat = (d.category or "").lower()
    name = (d.name or "").lower()
    if any(k in name for k in ("2x", "3x", "ultra", "leveraged", "inverse", "레버리지", "인버스")):
        return "leveraged", "레버리지·인버스"
    if any(k in cat for k in ("bond", "government", "treasury", "muni", "credit", "fixed income")):
        return "bond", "채권형"
    if any(k in cat for k in ("commodit", "precious metals", "gold", "natural resources")):
        return "commodity", "원자재"
    if any(k in cat for k in ("foreign", "world", "emerging", "international", "china", "japan", "europe", "pacific")):
        return "foreign_equity", "해외 주식형"
    if "growth" in cat:
        return "growth_equity", "성장 주식형"
    # 한국 국내주식형 — 무료 소스에 바스켓 PER가 없어 아래 basket_pe 조건에 걸리지 않으므로
    # provider가 넣어 준 category로 직접 판정한다(미국은 yfinance category가 그 역할).
    if "korea equity" in cat:
        return "equity", "국내 주식형"
    if d.basket_pe and d.basket_pe > 0:
        return "equity", "주식형"
    return "other", "기타"


# ── ① 괴리 (현재가 vs NAV) ───────────────────────────────────────────
def _premium(d: ETFData) -> float | None:
    """(현재가 − NAV) / NAV. +면 NAV보다 비싸게(프리미엄) 거래되는 중.

    공시 괴리율이 있으면(한국) 그쪽을 쓴다 — 거래소·운용사가 같은 시점 기준으로 낸 값이라,
    시세와 NAV 스냅샷의 시점이 어긋나 생기는 가짜 괴리가 없다. (실측: 시점이 하루 어긋난
    NAV로 계산하면 KODEX 200이 −6%로 나오지만 실제 공시 괴리율은 −0.13%다.)
    """
    if d.deviation_rate is not None:
        return float(d.deviation_rate)
    if d.nav and d.nav > 0 and d.price and d.price > 0:
        return d.price / d.nav - 1.0
    return None


# ── ③ 배당수익률 역사밴드 ────────────────────────────────────────────
def _dividend_band(d: ETFData):
    """(현재수익률, 백분위0~100, 5년중앙값, gap) — 배당이력·가격으로 TTM 배당수익률 시계열을 만든다.

    수익률이 자기 역사 대비 높으면(백분위↑) 가격이 상대적으로 싸다는 뜻.
    분모는 반드시 미조정 가격 — 수정종가를 쓰면 과거 수익률이 부풀려진다(models.actual_prices 참고).
    """
    div = d.dividends
    px = actual_prices(d)
    if div is None or len(div) == 0 or px is None or len(px) < 250:
        return None, None, None, None
    px = px.copy()
    px.index = pd.to_datetime(px.index)
    if getattr(px.index, "tz", None) is not None:
        px.index = px.index.tz_localize(None)
    px = px[px > 0].sort_index()
    idx = px.index
    # 배당을 거래일 축에 얹고 최근 365일 합(TTM) → 수익률 시계열
    dd = pd.Series(0.0, index=idx)
    for dt, amt in div.items():
        dt = pd.Timestamp(dt)
        if dt.tz is not None:
            dt = dt.tz_localize(None)
        pos = idx.searchsorted(dt)
        if pos < len(idx) and float(amt) > 0:
            dd.iloc[pos] += float(amt)
    ttm = dd.rolling("365D").sum()
    yld = (ttm / px).replace([np.inf, -np.inf], np.nan).dropna()
    # 배당이 실제로 있는 구간만(초기 TTM 미성숙 제거): 최근 4년
    yld = yld[yld > 0].tail(252 * 4)
    if len(yld) < 252:
        return None, None, None, None
    cur = float(yld.iloc[-1])
    pct = float((yld < cur).mean() * 100)
    med = float(yld.median())
    gap = cur / med - 1.0 if med > 0 else None
    return cur, pct, med, gap


# ── 추적오차 (벤치마크 대비) ─────────────────────────────────────────
def _tracking_error(d: ETFData) -> float | None:
    """ETF와 벤치마크의 일간 수익률 차이(액티브 리턴)의 표준편차를 연율화(×√252).

    운용·복제 품질 지표 — 낮을수록 지수를 잘 따라간다. 벤치마크가 자기 자신으로
    폴백된 경우(benchmark_name 없음)는 의미가 없어 None. 인덱스 ETF는 보통 수%
    이내이고, 레버리지·해외·액티브형일수록 커진다.

    공시 추적오차율이 있으면(한국) 그 값을 그대로 쓴다 — 실제 추종지수 대비 공시값이라,
    대용 벤치마크로 우리가 추정한 아래 값보다 정확하다(미국은 추종지수 시세를 못 구해 추정).
    """
    if d.tracking_error_pub is not None:
        return float(d.tracking_error_pub)
    if not d.benchmark_name or d.index_prices is None or d.prices is None:
        return None
    df = pd.concat([d.prices.rename("e"), d.index_prices.rename("b")], axis=1).dropna()
    if len(df) < 60:
        return None
    active = df["e"].pct_change() - df["b"].pct_change()
    active = active.replace([np.inf, -np.inf], np.nan).dropna()
    if len(active) < 60:
        return None
    return float(active.std() * np.sqrt(252))


# ── 추세·52주 ────────────────────────────────────────────────────────
def _trend(d: ETFData):
    """(52주저, 52주고, 밴드 내 위치, 1년 초과성과).

    52주 밴드는 신문·증권사와 같은 관례로 **실제 거래가격** 기준이다(수정종가로 잡으면
    배당이 많은 ETF의 저점이 실제보다 낮게 찍혀 현재가가 밴드 위쪽에 있는 것처럼 보인다
    — 실측: TLT 16% → 3%). 반면 초과성과는 총수익 비교라 수정종가(prices)를 그대로 쓴다.
    """
    band_px = actual_prices(d)
    px = band_px.dropna() if band_px is not None else pd.Series(dtype=float)
    if len(px) < 60:
        return None, None, None, None
    last = float(px.iloc[-1])
    w = px.tail(252)
    lo, hi = float(w.min()), float(w.max())
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else None
    # 초과성과는 총수익 기준이라 여기서만 수정종가(prices)를 쓴다 — 위 52주 밴드의
    # 미조정 가격과 섞으면 배당만큼 수익률이 어긋나므로 시리즈를 따로 잡는다.
    rel = None
    tr = d.prices.dropna() if d.prices is not None else pd.Series(dtype=float)
    ix = d.index_prices.dropna() if d.index_prices is not None else pd.Series(dtype=float)
    # 벤치마크가 자기 자신으로 폴백된 경우(benchmark_name 없음)는 초과성과가 항상 정확히 0이라,
    # '벤치마크를 완벽히 따라갔다'는 반대 뜻으로 읽힌다 — 비교 자체를 하지 않는다(_tracking_error와 동일).
    if d.benchmark_name and len(ix) >= 252 and len(tr) >= 252:
        try:
            r_etf = float(tr.iloc[-1]) / float(tr.iloc[-252]) - 1
            r_ix = float(ix.iloc[-1]) / float(ix.iloc[-252]) - 1
            rel = r_etf - r_ix
        except Exception:
            rel = None
    return lo, hi, pos, rel


# ── 상대 위치 (시장 대비 · 역사) ──────────────────────────────────────
def _relative_ratio_pct(d) -> float | None:
    """현재 (ETF/벤치마크) 총수익 가격비율이 자기 5년 분포에서 몇 퍼센타일인가(0~100).

    높을수록 시장 대비 역사적으로 비싼(많이 벌어진) 구간 — 자기참조라 '역사·상대 위치'로
    쓸 수 있다(적정가는 아님). 벤치마크가 자기 자신으로 폴백된 경우(benchmark_name 없음)는
    비율이 상수라 의미 없어 None(_trend·_tracking_error와 동일 가드)."""
    if not d.benchmark_name or d.prices is None or d.index_prices is None:
        return None
    df = pd.concat([d.prices.rename("e"), d.index_prices.rename("b")], axis=1).dropna()
    if len(df) < 252:
        return None
    ratio = (df["e"] / df["b"]).replace([np.inf, -np.inf], np.nan).dropna()
    if len(ratio) < 252:
        return None
    cur = float(ratio.iloc[-1])
    return float((ratio < cur).mean() * 100)


# ── 축 신호 정규화 ('싼 0 ↔ 비쌈 100') ───────────────────────────────
def _pos(value: float | None, full_scale: float, higher_is_cheap: bool = False) -> float | None:
    """±full_scale을 만폭으로 잡아 0~100 위치로 환산. 기본은 '값이 클수록 비쌈'.

    축마다 단위가 달라(괴리 %·PER 상대 %·ERP %p) 만폭만 다르게 주고 같은 자로 읽는다."""
    if value is None or not full_scale:
        return None
    ratio = value / full_scale
    if higher_is_cheap:
        ratio = -ratio
    return max(0.0, min(100.0, 50.0 + ratio * 50.0))


# ── 판정 라벨 ────────────────────────────────────────────────────────
def _verdict_premium(disc: float) -> str:
    """disc = NAV/현재가 - 1 (+면 NAV보다 싸게 거래=디스카운트).

    NAV는 우리가 추정한 내재가치가 아니라 운용사가 공표한 값이다. 시장가가 NAV보다
    낮다는 건 차익거래가 덜 먹힌 상태를 **관찰**한 것이지 바구니에 담긴 종목들이
    싸다는 뜻이 아니라, '저평가/고평가'가 아니라 '싼/비싼 구간'으로 부른다(R3 용어집)."""
    return ("NAV보다 싼 구간(디스카운트)" if disc >= 0.02 else
            "NAV 소폭 하회" if disc >= 0.005 else
            "NAV 근접" if disc > -0.005 else
            "NAV 소폭 상회" if disc > -0.02 else
            "NAV보다 비싼 구간(프리미엄)")


def _dividend_lead(pct: float) -> str:
    """배당 밴드 위치(0~100 퍼센타일) → 한 줄 결론. 수익률이 높을수록 싼 구간."""
    return ("배당수익률이 역사적으로 높은 구간 — 싼 구간" if pct >= 65 else
            "배당수익률이 역사적으로 낮은 구간 — 비싼 구간" if pct <= 35 else
            "배당수익률이 역사 평균 수준")


def _verdict_dividend(gap: float) -> str:
    """gap = 현재수익률/중앙값 - 1 (+면 역사 대비 수익률↑=쌈).

    자기 5년 배당수익률 분포 안에서의 **위치**이지 적정가 대비 판단이 아니다.
    _dividend_lead와 같은 어휘 계열을 쓴다(R3 용어집)."""
    return ("역사적으로 싼 구간(수익률 상단)" if gap >= 0.15 else
            "다소 싼 구간" if gap >= 0.05 else
            "역사 평균 수준" if gap > -0.05 else
            "다소 비싼 구간" if gap > -0.15 else
            "역사적으로 비싼 구간(수익률 하단)")


# ── 종합 ────────────────────────────────────────────────────────────
def compute_etf(d: ETFData, rf: float | None = None) -> ETFResult:
    fund_type, type_label = classify(d)
    r = ETFResult(symbol=d.ticker, name=d.name, currency=d.currency, price=d.price,
                  fund_type=fund_type, type_label=type_label,
                  nav=d.nav, basket_pe=d.basket_pe, basket_pb=d.basket_pb,
                  div_yield=d.div_yield, bench_pe=d.bench_pe, bench_label=d.bench_label,
                  aum=d.aum, expense_ratio=d.expense_ratio)
    r.notes.extend(d.warnings or [])

    # ① 괴리
    r.premium = _premium(d)
    if r.premium is not None:
        # 계산 괴리(공시값 없음)가 2% 미만이면 NAV 시점 지연 오차 범위 — 신호로 치지 않는다.
        noise = d.deviation_rate is None and abs(r.premium) < 0.02
        lead1 = ("NAV 시점 지연에서 오는 오차 범위 — 사실상 제값에 거래 중" if noise else
                 "NAV보다 비싸게(프리미엄) 거래 중" if r.premium > 0 else
                 "NAV보다 싸게(디스카운트) 거래 중")
        r.axes.append(ETFAxis("premium", "① 실시간 · 괴리",
                              f"NAV 대비 {r.premium:+.2%}",
                              "차익거래로 유동 ETF는 0에 가깝습니다. 해외자산·저유동일수록 벌어집니다.",
                              pos=_pos(r.premium, 0.02), lead=lead1, weak=noise))
    else:
        r.axes.append(ETFAxis("premium", "① 실시간 · 괴리", "N/A",
                              "NAV(순자산가치)를 받지 못했습니다.", available=False))

    # ② 바스켓 상대
    # basket_note는 '이 PER를 어떻게 구했나'(예: 상위 10종목 가중평균 추정)를 밝히는 문구다 —
    # 숫자와 떨어져 있으면 펀드 전체 PER로 오해하므로 축 설명에 항상 붙여 보낸다.
    how = f" {d.basket_note}." if d.basket_note else ""
    if fund_type in ("bond", "commodity"):
        r.masked.append(("바스켓 PER", "채권·원자재는 이익 기반 배수가 무의미해 마스킹"))
    elif d.basket_pe and d.bench_pe and d.bench_pe > 0:
        r.pe_vs_bench = d.basket_pe / d.bench_pe - 1
        ey, bey = 1.0 / d.basket_pe, 1.0 / d.bench_pe
        cmp_word = "비싸게" if r.pe_vs_bench >= 0 else "싸게"
        r.axes.append(ETFAxis("relative", "② 펀더멘털 · 바스켓 상대",
                              f"PER {d.basket_pe:.1f} ({r.bench_label} 대비 {r.pe_vs_bench:+.0%})",
                              f"이익수익률 {ey:.1%} vs 시장 {bey:.1%}. 프리미엄은 바스켓 이익성장이 "
                              "시장을 웃돌 때만 정당화됩니다 — 성장 둔화·금리 상승 때 먼저 깎입니다." + how,
                              pos=_pos(r.pe_vs_bench, 0.40),
                              lead=f"같은 1달러 이익을 시장보다 {abs(r.pe_vs_bench):.0%} {cmp_word} 사는 중"))
    elif d.basket_pe:
        r.axes.append(ETFAxis("relative", "② 펀더멘털 · 바스켓 상대",
                              f"PER {d.basket_pe:.1f}", "벤치마크 지표가 없어 절대값만 표시." + how))
    else:
        # 해외·혼합형은 구성종목이 해외 티커라 무료 소스로 이익 지표를 모을 수 없다.
        # 축을 조용히 빼면 '계산해 봤더니 이상 없음'으로 읽히므로, 사유를 남긴 채 비활성으로 둔다.
        r.axes.append(ETFAxis("relative", "② 펀더멘털 · 바스켓 상대", "N/A",
                              "구성종목의 이익 지표를 무료 데이터로 모으지 못해 바스켓 "
                              "PER·PBR을 계산하지 못했습니다.", available=False))

    # ③ 배당수익률 역사밴드
    cur, pct, med, gap = _dividend_band(d)
    r.div_yield = cur if cur is not None else d.div_yield
    r.div_pct, r.div_median, r.div_gap = pct, med, gap
    if pct is not None:
        band = ("상단(수익률↑·싼 구간)" if pct >= 65 else
                "하단(수익률↓·비싼 구간)" if pct <= 35 else "중간")
        weak3 = cur < 0.015
        lead3 = (f"배당이 {cur:.2%}로 미미해 이 축은 판단에서 빼세요" if weak3
                 else _dividend_lead(pct))
        r.axes.append(ETFAxis("dividend", "③ 역사적 · 배당수익률 밴드",
                              f"배당수익률 {cur:.2%} · 5년 밴드 {band}",
                              "자기 5년 배당수익률 분포에서 지금의 위치 — 수익률이 낮다는 건 "
                              "그만큼 가격이 높다는 뜻입니다.",
                              pos=100.0 - pct, lead=lead3, weak=weak3))
    else:
        r.axes.append(ETFAxis("dividend", "③ 역사적 · 배당수익률 밴드", "N/A",
                              "배당이 적거나 이력이 짧아 밴드를 만들 수 없습니다(성장형에서 흔함).",
                              available=False))

    # ④ 금리 대비 (ERP) — 이익수익률(1/바스켓PER) vs 10년 국채금리. 오늘 값만 쓰므로 룩어헤드 없음.
    # 절대 ERP만으로 단정하지 않는 '참고 축'이다 — 판정(verdict)은 바꾸지 않고, 성장형처럼
    # 밸류에이션을 보류한 유형에서 "금리 대비 이익보상이 얼마나 두꺼운가"를 수치로 보탠다.
    if fund_type not in ("bond", "commodity") and d.basket_pe and d.basket_pe > 0 and rf is not None:
        r.earnings_yield = 1.0 / d.basket_pe
        r.erp = r.earnings_yield - rf
        lead4 = (f"국채보다 이익보상이 {abs(r.erp):.1%}p 두꺼움" if r.erp >= 0 else
                 f"국채보다 이익보상이 {abs(r.erp):.1%}p 얇음 — 성장이 메워야 함")
        r.axes.append(ETFAxis("erp", "④ 금리 대비 · 이익수익률(ERP)",
                              f"이익수익률 {r.earnings_yield:.1%} − 국채 {rf:.1%} = {r.erp:+.1%}p",
                              "이익수익률 = 바스켓 전체 이익 ÷ 주가(배당이 아니라 이익 전부, 후행 "
                              "12개월). +면 채권보다 이익보상이 두껍고, −면 얇습니다 — 음수면 성장이 "
                              "그 격차를 메워야 정당화됩니다(성장 베팅). 후행 기준이라 성장주는 낮게 "
                              "나옵니다.",
                              pos=_pos(r.erp, 0.04, higher_is_cheap=True), lead=lead4))

    # 추세·52주·추적오차
    r.w52_low, r.w52_high, r.w52_pos, r.rel_1y = _trend(d)
    r.tracking_error = _tracking_error(d)

    # ── 주 신호 자동 분기 + 판정 ──
    # 원칙: 유동 ETF는 괴리가 늘 0에 가까워 무의미하다. '괴리'는 실제로 벌어졌을 때만
    # 주 신호로 쓰고(해외·저유동·장외시간), 평소엔 수익률/배당 밴드가 진짜 밸류에이션 신호다.
    # 공시 괴리율(KR, 같은 시점 = 정확)은 0.5%도 유의미하지만, 계산 괴리(US, price/nav)는 NAV가
    # 하루 뒤처져 초유동 ETF도 1~2% 벌어지는 stale-NAV 노이즈가 흔하다 — 계산 괴리는 2% 이상만
    # '실제 신호'로 보고, 그 아래는 판정을 몰지 않는다(그래야 상대 위치 종합으로 넘어간다).
    disclosed = d.deviation_rate is not None
    strong_disc = r.premium is not None and abs(r.premium) >= (0.005 if disclosed else 0.02)
    has_div = r.div_gap is not None

    def _by_premium():
        disc = -r.premium                                              # 디스카운트(+)=쌈
        r.primary, r.gap, r.verdict = "premium", disc, _verdict_premium(disc)
        r.confidence = "높음" if abs(r.premium) >= 0.01 else "중간"

    def _by_dividend():
        r.primary, r.gap, r.verdict = "dividend", r.div_gap, _verdict_dividend(r.div_gap)

    if strong_disc:
        # ① 괴리가 실제로 벌어짐 → 최우선 (해외자산·저유동·레버리지 추적이탈)
        _by_premium()
        if fund_type == "leveraged":
            r.notes.append("레버리지·인버스는 일간 복리로 장기 추적오차가 큽니다 — "
                           "적정가가 아니라 단기 괴리·추세로만 참고하세요.")
    elif fund_type == "bond":
        # 채권형: 수익률(분배율)이 곧 밸류에이션 — 높은 수익률=낮은 가격=쌈. PER 마스킹.
        r.notes.append("채권형은 분배수익률과 듀레이션이 핵심입니다 — PER은 표시하지 않습니다.")
        if has_div:
            _by_dividend()
            r.confidence = "높음" if r.div_pct is not None else "중간"
        elif r.premium is not None:
            _by_premium()
    elif has_div and fund_type in ("equity", "foreign_equity"):
        # ③ 배당·수익률 밴드 주도 (배당 있는 주식형·해외형)
        _by_dividend()
        low_yield = r.div_yield is not None and r.div_yield < 0.015     # 블렌드형 ~1%면 신호 약함
        r.confidence = "중간" if low_yield else "높음"
        if low_yield:
            r.notes.append("배당수익률이 낮아 밴드 신호가 약합니다 — "
                           "52주 추세·시장 대비 PER과 함께 보세요.")
    elif fund_type == "growth_equity" or (d.basket_pe and not has_div):
        # ② 상대 + 추세 (성장형: 배당밴드 약해 밸류에이션 단정하지 않음)
        r.primary = "relative"
        r.verdict = None
        r.confidence = "낮음"
        note = ("성장형은 배당이 적어 역사밴드가 약합니다 — 밸류에이션 단정 대신 "
                "시장 대비 PER 프리미엄과 52주 추세로 참고하세요.")
        if r.erp is not None:
            note += f" 금리 대비 이익보상(ERP)은 {r.erp:+.1%}p입니다."
        r.notes.append(note)
    elif fund_type == "leveraged":
        # 괴리 작지만 레버리지 — 추적오차 경고 위주, 판정 보류
        r.primary = "trend"
        r.confidence = "낮음"
        r.notes.append("레버리지·인버스는 일간 복리로 장기 추적오차가 커 적정가 판정을 하지 않습니다 — "
                       "단기 추세·괴리로만 참고하세요.")
    else:
        # 원자재·기타: 이익·배당 기반 적정가 스킵, 괴리·추세만
        r.primary = "premium" if r.premium is not None else "trend"
        r.verdict = None
        r.confidence = "낮음"
        r.notes.append("이 유형은 이익·배당 기반 적정가 산정이 어려워 괴리와 추세만 참고합니다.")

    # ── 상대·역사 위치 종합 (판정 보류 주식형) ──
    # 펀더멘털 적정가는 못 내도, 자기참조 신호(시장 대비 가격비율의 5년 위치)로 "역사·시장 대비
    # 비싼/싼 구간"까지는 정직하게 말할 수 있다. verdict(펀더멘털)는 그대로 두고 stance를
    # 따로 둔다 — 적정가가 아니라 평균회귀 관점임을 노트에 명시한다. 유형·데이터 기준이라 모든
    # 성장/무배당 주식형 ETF에 자동 적용된다.
    # 주 신호로 채택된 축은 '약함' 표시를 해제한다 — 그 축으로 판정을 내놓고 화면에서 같은 축을
    # 약하다고 깎으면 서로 어긋난다(근거가 약하다는 사실은 confidence·노트가 이미 말한다).
    if r.verdict is not None:
        for a in r.axes:
            if a.key == r.primary and a.weak:
                a.weak = False
                if a.key == "dividend" and r.div_pct is not None:
                    a.lead = _dividend_lead(r.div_pct)

    # 축 신호 집계 — 화면 종합 한 줄("N개가 비싼 구간")용. 신호가 약한 축은 중립으로 센다.
    counts = {"expensive": 0, "cheap": 0, "neutral": 0}
    for a in r.axes:
        if a.pos is None:
            continue
        if a.weak or 35 < a.pos < 65:
            counts["neutral"] += 1
        else:
            counts["expensive" if a.pos >= 65 else "cheap"] += 1
    r.signal_counts = counts

    r.rel_ratio_pct = _relative_ratio_pct(d)
    if (r.verdict is None and fund_type in ("equity", "growth_equity", "foreign_equity")
            and r.rel_ratio_pct is not None):
        p = r.rel_ratio_pct
        r.stance_pos = p
        # 헤드라인은 완결된 문장으로 — "~한 편"처럼 얼버무리면 판단을 유보한 게 아니라
        # 말끝을 흐린 것으로 읽힌다. 무엇과 견줬는지(5년)를 문장 앞에 두어 오해를 막는다.
        # '싸다/비싸다'(관찰된 가격 수준)는 '저평가/고평가'(내재가치 대비 판단)와 다른 말이라,
        # 적정가 판정을 보류한 이 자리에서는 오히려 전자가 정확하다.
        r.stance = ("5년 기준 비싼 구간입니다" if p >= 65 else
                    "5년 기준 싼 구간입니다" if p <= 35 else
                    "5년 기준 보통 구간입니다")
        stance_short = "비싼 구간" if p >= 65 else "싼 구간" if p <= 35 else "보통 구간"
        band = "상단" if p >= 65 else "하단" if p <= 35 else "중간"
        reasons = [f"시장({r.bench_label or '벤치마크'}) 대비 가격비율 5년 {band}(퍼센타일 {p:.0f})"]
        if r.w52_pos is not None:
            reasons.append(f"52주 위치 {r.w52_pos:.0f}%")
        if r.erp is not None:
            reasons.append(f"금리 대비 이익보상(ERP) {r.erp:+.1%}p")
        r.stance_reasons = reasons
        r.notes.append(
            f"종합 · 시장·역사 대비 {stance_short} — {' / '.join(reasons)}. 이건 적정가(펀더멘털)가 "
            "아니라 상대·평균회귀 위치입니다 — 성장 우위가 구조적이면 이 신호는 약할 수 있어 방향 "
            "참고로만 보세요.")

    return r
