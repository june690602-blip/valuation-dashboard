"""표시용 포맷터·라벨·HTML 컴포넌트 (Streamlit)."""
from __future__ import annotations

import numpy as np

# ── 팔레트 (dataviz 검증 팔레트, 라이트 모드) ─────────────────────────
PALETTE = {
    "series1": "#2a78d6",   # blue  — 주 시리즈/타깃
    "series2": "#1baf7a",   # aqua
    "series3": "#eda100",   # yellow
    "series4": "#4a3aa7",   # violet
    "blue_soft": "#9ec5f4",
    "blue_mid": "#5598e7",
    "blue_deep": "#1c5cab",
    "red": "#e34948",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "baseline": "#c3c2b7",
    "surface": "#fcfcfb",
    "good": "#0ca30c",
    "warning": "#fab219",
    "critical": "#d03b3b",
    "neutral_bg": "#f0efec",
}

# 판정 → 배지 색 (저평가=파랑 극, 고평가=빨강 극, 중립=회색)
VERDICT_COLORS = {
    "크게 저평가": "#1c5cab",
    "저평가": "#2a78d6",
    "적정 수준": "#6a6963",
    "고평가": "#e34948",
    "크게 고평가": "#d03b3b",
}

LABELS = {
    "per": "PER", "forward_per": "선행 PER", "pbr": "PBR", "psr": "PSR",
    "ev_ebitda": "EV/EBITDA", "p_fcf": "P/FCF", "div_yield": "배당수익률",
    "peg": "PEG", "roe": "ROE", "roa": "ROA", "gross_margin": "매출총이익률",
    "op_margin": "영업이익률", "net_margin": "순이익률",
    "rev_yoy": "매출 성장률(YoY)", "rev_cagr3": "매출 3년 CAGR",
    "op_cagr3": "영업이익 3년 CAGR", "eps_cagr3": "EPS 3년 CAGR",
    "rev_growth": "매출 성장률(최근분기)", "earnings_growth": "이익 성장률(최근분기)",
    "debt_ratio": "부채비율", "debt_to_equity": "차입금/자본",
    "current_ratio": "유동비율", "interest_coverage": "이자보상배율",
    "net_debt_ebitda": "순차입금/EBITDA", "ocf": "영업현금흐름",
    "fcf": "잉여현금흐름(FCF)", "fcf_yield": "FCF 수익률",
    "ocf_yield": "OCF 수익률", "ocf_ni": "OCF/순이익", "beta": "베타",
    "market_cap": "시가총액", "name": "종목명",
}

# 지표 표시 형식: pct(%) | x(배) | money
FORMATS = {
    "per": "x", "forward_per": "x", "pbr": "x", "psr": "x", "ev_ebitda": "x",
    "p_fcf": "x", "peg": "x", "current_ratio": "x", "interest_coverage": "x",
    "net_debt_ebitda": "x", "beta": "x",
    "div_yield": "pct", "roe": "pct", "roa": "pct", "gross_margin": "pct",
    "op_margin": "pct", "net_margin": "pct", "rev_yoy": "pct", "rev_cagr3": "pct",
    "op_cagr3": "pct", "eps_cagr3": "pct", "rev_growth": "pct",
    "earnings_growth": "pct", "debt_ratio": "pct", "fcf_yield": "pct",
    "ocf_yield": "pct", "ocf_ni": "pct",
    "ocf": "money", "fcf": "money", "market_cap": "money",
}


def _is_na(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v))


def fmt_money(v, currency: str = "KRW") -> str:
    """단일 단위 + 유효숫자로 짧게. 5.003e14 → '500.3조', 8.5e11 → '8,500억', 4.5e12 → '$4.50T'.

    (좁은 메트릭 칸에서 줄바꿈/잘림을 막기 위해 '조 억' 2단 표기 대신 한 단위로 반올림)
    """
    if _is_na(v):
        return "—"
    v = float(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if currency == "KRW":
        if a >= 1e14:            # 100조 이상은 소수 없이
            return f"{sign}{a / 1e12:,.0f}조"
        if a >= 1e12:            # 1조~100조는 소수 1자리
            return f"{sign}{a / 1e12:,.1f}조"
        if a >= 1e8:
            return f"{sign}{a / 1e8:,.0f}억"
        return f"{sign}{a:,.0f}원"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if a >= div:
            return f"{sign}${a / div:,.2f}{unit}"
    return f"{sign}${a:,.0f}"


def fmt_price(v, currency: str = "KRW") -> str:
    if _is_na(v):
        return "—"
    return f"{v:,.0f}원" if currency == "KRW" else f"${v:,.2f}"


def fmt_pct(v, digits: int = 1) -> str:
    return "—" if _is_na(v) else f"{v * 100:.{digits}f}%"


def fmt_x(v, digits: int = 2) -> str:
    return "—" if _is_na(v) else f"{v:.{digits}f}배"


def fmt_value(key: str, v, currency: str = "KRW") -> str:
    f = FORMATS.get(key, "x")
    if f == "pct":
        return fmt_pct(v)
    if f == "money":
        return fmt_money(v, currency)
    return fmt_x(v)


def label(key: str) -> str:
    return LABELS.get(key, key)


def verdict_badge_html(verdict: str | None, gap: float | None,
                       confidence: str | None) -> str:
    if not verdict:
        return ""
    color = VERDICT_COLORS.get(verdict, "#6a6963")
    gap_txt = f" · 적정가 대비 {gap * +100:+.0f}%" if gap is not None else ""
    conf_txt = f" · 신뢰도 {confidence}" if confidence else ""
    return f"""
    <div style="display:inline-flex;align-items:center;gap:10px;">
      <span style="background:{color};color:#fff;padding:6px 16px;border-radius:20px;
                   font-size:1.05rem;font-weight:700;">{verdict}</span>
      <span style="color:#52514e;font-size:0.9rem;">{gap_txt.lstrip(" ·")}{conf_txt}</span>
    </div>"""


# 뉴스 카테고리·태그 배지 색 (카테고리는 팔레트 시리즈, 태그는 중립)
NEWS_CAT_COLORS = {"기업": "#2a78d6", "산업": "#1baf7a", "거시": "#4a3aa7"}


def news_badge_html(text: str, kind: str = "tag") -> str:
    """뉴스 카테고리('기업'·'산업'·'거시')/태그 미니 배지."""
    if kind == "category":
        bg, fg = NEWS_CAT_COLORS.get(text, "#898781"), "#fff"
    else:
        bg, fg = "#f0efec", "#52514e"
    return (f"<span style='background:{bg};color:{fg};padding:1px 8px;border-radius:10px;"
            f"font-size:0.78rem;white-space:nowrap;'>{text}</span>")


def score_bar_html(score: float | None, width: int = 90) -> str:
    """0~100 백분위 미니 바."""
    if score is None:
        return "<span style='color:#898781'>—</span>"
    pct = max(0, min(100, score))
    color = "#2a78d6" if pct >= 50 else "#e34948" if pct < 30 else "#eda100"
    return f"""
    <div style="display:inline-flex;align-items:center;gap:6px;">
      <div style="width:{width}px;height:8px;background:#f0efec;border-radius:4px;overflow:hidden;">
        <div style="width:{pct}%;height:100%;background:{color};"></div>
      </div>
      <span style="font-size:0.85rem;color:#52514e;">{pct:.0f}</span>
    </div>"""
