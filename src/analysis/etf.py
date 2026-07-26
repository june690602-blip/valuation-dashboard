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

from ..data.models import ETFData


@dataclass
class ETFAxis:
    """UI 표시용 축 하나의 요약."""
    key: str                    # 'premium' | 'relative' | 'dividend' | 'trend'
    title: str
    value: str                  # 사람이 읽는 대표값 (예: 'NAV 대비 -0.2%')
    note: str = ""
    available: bool = True


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
    if d.basket_pe and d.basket_pe > 0:
        return "equity", "주식형"
    return "other", "기타"


# ── ① 괴리 (현재가 vs NAV) ───────────────────────────────────────────
def _premium(d: ETFData) -> float | None:
    if d.nav and d.nav > 0 and d.price and d.price > 0:
        return d.price / d.nav - 1.0
    return None


# ── ③ 배당수익률 역사밴드 ────────────────────────────────────────────
def _dividend_band(d: ETFData):
    """(현재수익률, 백분위0~100, 5년중앙값, gap) — 배당이력·가격으로 TTM 배당수익률 시계열을 만든다.

    수익률이 자기 역사 대비 높으면(백분위↑) 가격이 상대적으로 싸다는 뜻.
    """
    div = d.dividends
    px = d.prices
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
    """
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
    px = d.prices.dropna() if d.prices is not None else pd.Series(dtype=float)
    if len(px) < 60:
        return None, None, None, None
    last = float(px.iloc[-1])
    w = px.tail(252)
    lo, hi = float(w.min()), float(w.max())
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else None
    rel = None
    ix = d.index_prices.dropna() if d.index_prices is not None else pd.Series(dtype=float)
    if len(ix) >= 252 and len(px) >= 252:
        try:
            r_etf = last / float(px.iloc[-252]) - 1
            r_ix = float(ix.iloc[-1]) / float(ix.iloc[-252]) - 1
            rel = r_etf - r_ix
        except Exception:
            rel = None
    return lo, hi, pos, rel


# ── 판정 라벨 ────────────────────────────────────────────────────────
def _verdict_premium(disc: float) -> str:
    """disc = NAV/현재가 - 1 (+면 NAV보다 싸게 거래=디스카운트)."""
    return ("NAV 대비 저평가(디스카운트)" if disc >= 0.02 else
            "NAV 소폭 하회" if disc >= 0.005 else
            "NAV 근접 · 적정" if disc > -0.005 else
            "NAV 소폭 상회(프리미엄)" if disc > -0.02 else
            "NAV 대비 고평가(프리미엄)")


def _verdict_dividend(gap: float) -> str:
    """gap = 현재수익률/중앙값 - 1 (+면 역사 대비 수익률↑=쌈)."""
    return ("역사적 저평가 구간(수익률 상단)" if gap >= 0.15 else
            "다소 저평가" if gap >= 0.05 else
            "역사 평균 수준 · 적정" if gap > -0.05 else
            "다소 고평가" if gap > -0.15 else
            "역사적 고평가 구간(수익률 하단)")


# ── 종합 ────────────────────────────────────────────────────────────
def compute_etf(d: ETFData) -> ETFResult:
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
        r.axes.append(ETFAxis("premium", "① 실시간 · 괴리",
                              f"NAV 대비 {r.premium:+.2%}",
                              "차익거래로 유동 ETF는 0에 가깝습니다. 해외자산·저유동일수록 벌어집니다."))
    else:
        r.axes.append(ETFAxis("premium", "① 실시간 · 괴리", "N/A",
                              "NAV(순자산가치)를 받지 못했습니다.", available=False))

    # ② 바스켓 상대
    if fund_type in ("bond", "commodity"):
        r.masked.append(("바스켓 PER", "채권·원자재는 이익 기반 배수가 무의미해 마스킹"))
    elif d.basket_pe and d.bench_pe and d.bench_pe > 0:
        r.pe_vs_bench = d.basket_pe / d.bench_pe - 1
        r.axes.append(ETFAxis("relative", "② 펀더멘털 · 바스켓 상대",
                              f"PER {d.basket_pe:.1f} ({r.bench_label} 대비 {r.pe_vs_bench:+.0%})",
                              "성장형은 시장보다 높은 PER가 정상입니다(프리미엄)."))
    elif d.basket_pe:
        r.axes.append(ETFAxis("relative", "② 펀더멘털 · 바스켓 상대",
                              f"PER {d.basket_pe:.1f}", "벤치마크 지표가 없어 절대값만 표시."))

    # ③ 배당수익률 역사밴드
    cur, pct, med, gap = _dividend_band(d)
    r.div_yield = cur if cur is not None else d.div_yield
    r.div_pct, r.div_median, r.div_gap = pct, med, gap
    if pct is not None:
        r.axes.append(ETFAxis("dividend", "③ 역사적 · 배당수익률 밴드",
                              f"배당수익률 {cur:.2%} (5년 내 상위 {100 - pct:.0f}%)",
                              "수익률이 역사적으로 높을수록 가격이 싼 구간입니다."))
    else:
        r.axes.append(ETFAxis("dividend", "③ 역사적 · 배당수익률 밴드", "N/A",
                              "배당이 적거나 이력이 짧아 밴드를 만들 수 없습니다(성장형에서 흔함).",
                              available=False))

    # 추세·52주·추적오차
    r.w52_low, r.w52_high, r.w52_pos, r.rel_1y = _trend(d)
    r.tracking_error = _tracking_error(d)

    # ── 주 신호 자동 분기 + 판정 ──
    # 원칙: 유동 ETF는 괴리가 늘 0에 가까워 무의미하다. '괴리'는 실제로 벌어졌을 때만
    # 주 신호로 쓰고(해외·저유동·장외시간), 평소엔 수익률/배당 밴드가 진짜 밸류에이션 신호다.
    strong_disc = r.premium is not None and abs(r.premium) >= 0.005     # 괴리가 유의미하게 벌어짐
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
            r.notes.append("배당수익률이 낮은 편이라 밴드 신호가 약합니다 — "
                           "52주 추세·시장 대비 PER과 함께 보세요.")
    elif fund_type == "growth_equity" or (d.basket_pe and not has_div):
        # ② 상대 + 추세 (성장형: 배당밴드 약해 밸류에이션 단정하지 않음)
        r.primary = "relative"
        r.verdict = None
        r.confidence = "낮음"
        r.notes.append("성장형은 배당이 적어 역사밴드가 약합니다 — 밸류에이션 단정 대신 "
                       "시장 대비 PER 프리미엄과 52주 추세로 참고하세요.")
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

    return r
