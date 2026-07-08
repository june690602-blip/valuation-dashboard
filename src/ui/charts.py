"""Plotly 차트 모음 — dataviz 검증 팔레트 적용.

원칙: 축은 하나(단위가 다르면 서브플롯 분리), 마크는 얇게, 그리드는 희미하게,
시리즈 2개 이상이면 범례, 판정 색(파랑=저평가·빨강=고평가)은 판정에만 사용.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .components import PALETTE as P

FONT = "system-ui, -apple-system, 'Segoe UI', 'Malgun Gothic', sans-serif"
CATEGORY_ORDER = ["밸류에이션", "수익성", "성장성", "재무 안정성", "현금흐름"]


def _layout(fig: go.Figure, height: int = 340, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        font=dict(family=FONT, size=12.5, color=P["ink2"]),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=16, t=36, b=10),
        hoverlabel=dict(font_family=FONT, font_size=12.5),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(size=12)) if legend else None,
        showlegend=legend,
    )
    fig.update_xaxes(gridcolor=P["grid"], gridwidth=1, zerolinecolor=P["baseline"],
                     linecolor=P["baseline"], tickfont=dict(color=P["muted"]))
    fig.update_yaxes(gridcolor=P["grid"], gridwidth=1, zerolinecolor=P["baseline"],
                     linecolor=P["baseline"], tickfont=dict(color=P["muted"]))
    return fig


def _money_scale(values, currency: str):
    """(나눗수, 단위표기) — 축 눈금을 사람이 읽는 단위로."""
    a = max((abs(v) for v in values if v is not None and not np.isnan(v)), default=0)
    if currency == "KRW":
        return (1e12, "조원") if a >= 2e12 else (1e8, "억원")
    return (1e9, "십억$") if a >= 2e9 else (1e6, "백만$")


# ── ① 요약: 레이더 ──────────────────────────────────────────────────
def radar(scores: dict) -> go.Figure:
    cats = [c for c in CATEGORY_ORDER if scores.get(c) is not None]
    vals = [scores[c] for c in cats]
    fig = go.Figure()
    # 업종 중앙값 기준선 (백분위 50)
    fig.add_trace(go.Scatterpolar(
        r=[50] * (len(cats) + 1), theta=cats + cats[:1], mode="lines",
        line=dict(color=P["muted"], width=1.2, dash="dot"),
        name="업종 중앙값(50)", hoverinfo="skip"))
    fig.add_trace(go.Scatterpolar(
        r=vals + vals[:1], theta=cats + cats[:1], mode="lines+markers",
        fill="toself", fillcolor="rgba(42,120,214,0.15)",
        line=dict(color=P["series1"], width=2), marker=dict(size=7),
        name="이 종목", hovertemplate="%{theta}: %{r:.0f}점<extra></extra>"))
    fig.update_layout(polar=dict(
        bgcolor="rgba(0,0,0,0)",
        radialaxis=dict(range=[0, 100], gridcolor=P["grid"], tickfont=dict(size=10, color=P["muted"])),
        angularaxis=dict(gridcolor=P["grid"], tickfont=dict(size=12, color=P["ink2"])),
    ))
    return _layout(fig, height=330)


# ── ① 요약: 적정주가 불릿 차트 ──────────────────────────────────────
def fair_value_bullet(estimates, fair_mid, price, currency: str) -> go.Figure:
    rows = [(e.method, e.low, e.mid, e.high) for e in estimates]
    if fair_mid is not None and len(rows) >= 2:
        lows = [r[1] for r in rows]
        highs = [r[3] for r in rows]
        rows.append(("종합", float(np.mean(lows)), fair_mid, float(np.mean(highs))))
    names = [r[0] for r in rows][::-1]
    fig = go.Figure()
    for i, (name, lo, mid, hi) in enumerate(rows[::-1]):
        is_total = name == "종합"
        fig.add_trace(go.Bar(
            y=[name], x=[hi - lo], base=[lo], orientation="h",
            marker=dict(color=P["blue_mid"] if is_total else P["blue_soft"],
                        cornerradius=4),
            width=0.55 if is_total else 0.45, showlegend=False,
            hovertemplate=(f"{name}<br>범위 {lo:,.0f} ~ {hi:,.0f}"
                           f"<br>중심 {mid:,.0f}<extra></extra>"),
        ))
        fig.add_trace(go.Scatter(
            y=[name], x=[mid], mode="markers",
            marker=dict(symbol="diamond", size=11, color=P["blue_deep"],
                        line=dict(color="#fff", width=1.5)),
            showlegend=False, hovertemplate=f"적정가 중심 {mid:,.0f}<extra></extra>"))
    fig.add_vline(x=price, line=dict(color=P["ink"], width=1.6, dash="dash"))
    fig.add_annotation(x=price, y=1.06, yref="paper", showarrow=False,
                       text=f"현재가 {price:,.0f}", font=dict(size=12, color=P["ink"]))
    fig.update_yaxes(categoryorder="array", categoryarray=names, showgrid=False)
    tickfmt = ",.0f"
    fig.update_xaxes(tickformat=tickfmt)
    return _layout(fig, height=90 + 62 * len(rows), legend=False)


# ── ② 밸류에이션: 역사적 밴드 ───────────────────────────────────────
def band_chart(band: pd.DataFrame, currency: str, kind: str = "PER") -> go.Figure:
    fig = go.Figure()
    qcols = [c for c in ("q10", "q25", "q50", "q75", "q90") if c in band.columns]
    shades = {"q10": "#cde2fb", "q25": "#9ec5f4", "q50": "#5598e7",
              "q75": "#2a78d6", "q90": "#1c5cab"}
    for i, c in enumerate(qcols):
        fill = "tonexty" if c == "q75" and "q25" in qcols else None
        fig.add_trace(go.Scatter(
            x=band.index, y=band[c], mode="lines",
            line=dict(color=shades[c], width=1.1),
            fill=fill, fillcolor="rgba(158,197,244,0.18)",
            name=f"{kind} {c[1:]}분위", hovertemplate=f"{c[1:]}분위: %{{y:,.0f}}<extra></extra>"))
        # 우측 끝 직접 라벨
        fig.add_annotation(x=band.index[-1], y=float(band[c].iloc[-1]),
                           text=f" {c[1:]}%", showarrow=False, xanchor="left",
                           font=dict(size=10, color=shades[c]))
    fig.add_trace(go.Scatter(
        x=band.index, y=band["price"], mode="lines", name="주가",
        line=dict(color=P["ink"], width=1.9),
        hovertemplate="주가: %{y:,.0f}<extra></extra>"))
    fig.update_layout(hovermode="x unified")
    fig.update_xaxes(showgrid=False)
    return _layout(fig, height=380)


# ── ③ 재무: 성장(매출·이익 + 마진) ──────────────────────────────────
def growth_chart(series: dict, currency: str) -> go.Figure:
    rev, oi, ni = series.get("revenue"), series.get("operating_income"), series.get("net_income")
    div, unit = _money_scale(list(rev.values) if rev is not None else [0], currency)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.14,
                        row_heights=[0.62, 0.38],
                        subplot_titles=(f"매출·이익 ({unit})", "이익률"))
    bars = [("매출", rev, P["series1"]), ("영업이익", oi, P["series2"]),
            ("순이익", ni, P["series3"])]
    for name, s, color in bars:
        if s is None or s.empty:
            continue
        fig.add_trace(go.Bar(x=s.index.astype(str), y=s / div, name=name,
                             marker=dict(color=color, cornerradius=4),
                             hovertemplate=f"{name}: %{{y:,.1f}}{unit}<extra></extra>"),
                      row=1, col=1)
    for name, key, color, dash in (("영업이익률", "op_margin", P["series2"], None),
                                   ("순이익률", "net_margin", P["series3"], "dot")):
        s = series.get(key)
        if s is None or s.empty:
            continue
        fig.add_trace(go.Scatter(x=s.index.astype(str), y=s * 100, name=name,
                                 mode="lines+markers",
                                 line=dict(color=color, width=2, dash=dash),
                                 marker=dict(size=7),
                                 hovertemplate=f"{name}: %{{y:.1f}}%<extra></extra>"),
                      row=2, col=1)
    fig.update_layout(barmode="group", bargap=0.25, hovermode="x unified")
    fig.update_yaxes(ticksuffix="%", row=2, col=1)
    for a in fig.layout.annotations:
        a.font = dict(size=12.5, color=P["ink2"])
        a.x, a.xanchor = 0, "left"
    return _layout(fig, height=470)


# ── ③ 재무: 안정성 ─────────────────────────────────────────────────
def stability_chart(series: dict) -> go.Figure:
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.12,
                        subplot_titles=("부채비율 (부채총계/자본총계)", "유동비율"))
    dr = series.get("debt_ratio")
    if dr is not None and not dr.empty:
        fig.add_trace(go.Bar(x=dr.index.astype(str), y=dr * 100,
                             marker=dict(color=P["series1"], cornerradius=4),
                             name="부채비율",
                             hovertemplate="부채비율: %{y:,.0f}%<extra></extra>"),
                      row=1, col=1)
        fig.add_hline(y=200, line=dict(color=P["critical"], width=1, dash="dot"),
                      annotation_text="200%", annotation_font_size=10, row=1, col=1)
    cr = series.get("current_ratio")
    if cr is not None and not cr.empty:
        fig.add_trace(go.Scatter(x=cr.index.astype(str), y=cr, mode="lines+markers",
                                 line=dict(color=P["series2"], width=2),
                                 marker=dict(size=7), name="유동비율",
                                 hovertemplate="유동비율: %{y:.2f}배<extra></extra>"),
                      row=1, col=2)
        fig.add_hline(y=1.0, line=dict(color=P["muted"], width=1, dash="dot"),
                      annotation_text="1.0배", annotation_font_size=10, row=1, col=2)
    fig.update_yaxes(ticksuffix="%", row=1, col=1)
    fig.update_yaxes(ticksuffix="배", row=1, col=2)
    for a in fig.layout.annotations:
        a.font = dict(size=12.5, color=P["ink2"])
    return _layout(fig, height=300, legend=False)


# ── ③ 재무: 현금흐름 ────────────────────────────────────────────────
def cashflow_chart(series: dict, currency: str) -> go.Figure:
    ocf, fcf = series.get("ocf"), series.get("fcf")
    base = list(ocf.values) if ocf is not None and not ocf.empty else [0]
    div, unit = _money_scale(base, currency)
    fig = go.Figure()
    for name, s, color in (("영업현금흐름", ocf, P["series2"]), ("FCF", fcf, P["series1"])):
        if s is None or s.empty:
            continue
        fig.add_trace(go.Bar(x=s.index.astype(str), y=s / div, name=name,
                             marker=dict(color=color, cornerradius=4),
                             hovertemplate=f"{name}: %{{y:,.1f}}{unit}<extra></extra>"))
    fig.update_layout(barmode="group", bargap=0.3, hovermode="x unified",
                      title=dict(text=f"현금흐름 ({unit})", x=0, font=dict(size=12.5, color=P["ink2"])))
    return _layout(fig, height=300)


# ── ④ 업종 비교: PER-ROE 산점도 ─────────────────────────────────────
def peer_scatter(peers: pd.DataFrame, currency: str) -> go.Figure | None:
    df = peers.copy()
    df = df[df["per"].notna() & df["roe"].notna()]
    if len(df) < 3:
        return None
    mc = df["market_cap"].fillna(df["market_cap"].median())
    size = 12 + 26 * np.sqrt(mc / mc.max())
    me = df[df["is_self"]]
    others = df[~df["is_self"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=others["per"], y=others["roe"] * 100, mode="markers+text",
        text=others["name"], textposition="top center",
        textfont=dict(size=10, color=P["muted"]),
        marker=dict(size=size[others.index], color="rgba(158,197,244,0.65)",
                    line=dict(color=P["blue_mid"], width=1)),
        name="피어", customdata=others["name"],
        hovertemplate="%{customdata}<br>PER %{x:.1f}배 · ROE %{y:.1f}%<extra></extra>"))
    if len(me) > 0:
        fig.add_trace(go.Scatter(
            x=me["per"], y=me["roe"] * 100, mode="markers+text",
            text=me["name"], textposition="top center",
            textfont=dict(size=12, color=P["ink"]),
            marker=dict(size=size[me.index[0]] + 6, color=P["series1"],
                        line=dict(color="#fff", width=2)),
            name="이 종목", customdata=me["name"],
            hovertemplate="%{customdata}<br>PER %{x:.1f}배 · ROE %{y:.1f}%<extra></extra>"))
    fig.add_vline(x=float(df["per"].median()), line=dict(color=P["baseline"], width=1, dash="dot"))
    fig.add_hline(y=float(df["roe"].median() * 100), line=dict(color=P["baseline"], width=1, dash="dot"))
    fig.add_annotation(x=0.01, y=0.98, xref="paper", yref="paper", showarrow=False,
                       text="← 싸고 수익성 높음 (매력 구간)", font=dict(size=11, color=P["muted"]),
                       xanchor="left")
    fig.update_xaxes(title_text="PER (배)", title_font_size=12)
    fig.update_yaxes(title_text="ROE (%)", title_font_size=12)
    return _layout(fig, height=430)


# ── ⑤ 자본비용: 베타 회귀 산점도 ────────────────────────────────────
def beta_scatter(reg: pd.DataFrame, beta: float | None, r2: float | None,
                 benchmark: str) -> go.Figure | None:
    if reg is None or len(reg) < 10:
        return None
    x, y = reg["market"] * 100, reg["stock"] * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="markers", name="주간 수익률",
        marker=dict(size=6, color="rgba(158,197,244,0.55)",
                    line=dict(color=P["blue_mid"], width=0.5)),
        hovertemplate=f"{benchmark} %{{x:.1f}}% → 종목 %{{y:.1f}}%<extra></extra>"))
    if beta is not None:
        alpha = float(y.mean() - beta * x.mean())
        xs = np.linspace(float(x.min()), float(x.max()), 20)
        fig.add_trace(go.Scatter(
            x=xs, y=alpha + beta * xs, mode="lines", name="회귀선",
            line=dict(color=P["series1"], width=2.2), hoverinfo="skip"))
        fig.add_annotation(
            x=0.02, y=0.98, xref="paper", yref="paper", xanchor="left", showarrow=False,
            text=f"<b>β = {beta:.2f}</b>  ·  R² = {r2:.2f}  ·  n = {len(reg)}",
            font=dict(size=13, color=P["ink"]), bgcolor="rgba(252,252,251,0.85)")
    fig.update_xaxes(title_text=f"{benchmark} 주간 수익률 (%)", title_font_size=12,
                     ticksuffix="%")
    fig.update_yaxes(title_text="종목 주간 수익률 (%)", title_font_size=12, ticksuffix="%")
    return _layout(fig, height=400, legend=False)


# ── ⑤ 자본비용: WACC 구성 워터폴 ────────────────────────────────────
def wacc_waterfall(cc) -> go.Figure | None:
    if cc.wacc is None:
        return None
    e_part = cc.we * cc.k_e
    d_part = cc.wd * cc.k_d * (1 - cc.tax_rate)
    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["relative", "relative", "total"],
        x=[f"자기자본 기여<br>k_e {cc.k_e:.1%} × {cc.we:.0%}",
           f"타인자본 기여<br>k_d(1-t) {cc.k_d * (1 - cc.tax_rate):.1%} × {cc.wd:.0%}",
           "<b>WACC</b>"],
        y=[e_part * 100, d_part * 100, 0],
        text=[f"{e_part:.2%}", f"{d_part:.2%}", f"<b>{cc.wacc:.2%}</b>"],
        textposition="outside",
        connector=dict(line=dict(color=P["baseline"], width=1)),
        increasing=dict(marker=dict(color=P["blue_soft"])),
        totals=dict(marker=dict(color=P["blue_deep"])),
    ))
    fig.update_yaxes(ticksuffix="%", rangemode="tozero")
    fig.update_layout(title=dict(text="WACC 구성 (시가 기준 가중)", x=0,
                                 font=dict(size=12.5, color=P["ink2"])))
    return _layout(fig, height=400, legend=False)


# ── ⑤ 자본비용: ROIC vs WACC ────────────────────────────────────────
def roic_wacc_chart(roic_series: pd.Series | None, roic_ttm: float | None,
                    wacc: float | None) -> go.Figure | None:
    if roic_series is None or roic_series.empty or wacc is None:
        return None
    s = roic_series * 100
    x = [str(i) for i in s.index]
    y = list(s.values)
    if roic_ttm is not None:
        x.append("TTM")
        y.append(roic_ttm * 100)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines+markers+text", name="ROIC",
        text=[f"{v:.1f}%" for v in y], textposition="top center",
        textfont=dict(size=10, color=P["ink2"]),
        line=dict(color=P["series1"], width=2.2), marker=dict(size=8),
        hovertemplate="ROIC: %{y:.1f}%<extra></extra>"))
    fig.add_hline(y=wacc * 100, line=dict(color=P["critical"], width=1.6, dash="dash"),
                  annotation_text=f"WACC {wacc:.1%}", annotation_position="top left",
                  annotation_font=dict(size=11, color=P["critical"]))
    fig.update_yaxes(ticksuffix="%", rangemode="tozero")
    fig.update_layout(title=dict(text="ROIC vs WACC — 자본비용을 넘는 수익을 내는가",
                                 x=0, font=dict(size=12.5, color=P["ink2"])))
    return _layout(fig, height=340, legend=False)


# ── ⑥ 백테스트: 구간별 미래수익 막대 ────────────────────────────────
_BUCKET_COLOR = {"저평가 구간": P["series1"], "중립 구간": P["muted"], "고평가 구간": P["red"]}


def backtest_bucket_bar(bucket_returns: pd.DataFrame, bucket_hit: pd.DataFrame,
                        horizon: str) -> go.Figure | None:
    if bucket_returns is None or horizon not in bucket_returns.columns:
        return None
    s = bucket_returns[horizon]
    hit = bucket_hit[horizon] if bucket_hit is not None else None
    buckets = [b for b in s.index if pd.notna(s[b])]
    if not buckets:
        return None
    fig = go.Figure()
    for b in buckets:
        h = f"플러스 확률 {hit[b] * 100:.0f}%" if hit is not None and pd.notna(hit[b]) else ""
        fig.add_trace(go.Bar(
            x=[b], y=[s[b] * 100], marker=dict(color=_BUCKET_COLOR.get(b, P["muted"]),
                                               cornerradius=4),
            text=f"{s[b] * 100:+.1f}%", textposition="outside",
            width=0.55, showlegend=False,
            hovertemplate=f"{b}<br>평균 {horizon} 미래수익 %{{y:.1f}}%<br>{h}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=P["baseline"], width=1))
    fig.update_yaxes(ticksuffix="%")
    fig.update_layout(title=dict(
        text=f"밸류에이션 구간별 이후 {horizon} 평균 수익률",
        x=0, font=dict(size=12.5, color=P["ink2"])))
    return _layout(fig, height=340, legend=False)


# ── ⑥ 백테스트: 백분위 vs 미래수익 산점도 ───────────────────────────
def backtest_scatter(scatter: pd.DataFrame, spearman: float | None,
                     cheap_th: float, rich_th: float) -> go.Figure | None:
    if scatter is None or len(scatter) < 30:
        return None
    x, y = scatter["pct"].values, scatter["fwd_252"].values * 100
    # 저평가(파랑)→고평가(빨강) 색으로 x를 인코딩
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="markers",
        marker=dict(size=5, color=x, colorscale=[[0, P["series1"]], [0.5, P["muted"]],
                                                 [1, P["red"]]],
                    cmin=0, cmax=100, opacity=0.55,
                    colorbar=dict(title="밸류에이션<br>백분위", thickness=10, len=0.7)),
        name="관측", hovertemplate="당시 백분위 %{x:.0f} → 이후 12M %{y:.1f}%<extra></extra>"))
    # 추세선
    if len(x) >= 2 and np.ptp(x) > 0:
        a, b = np.polyfit(x, y, 1)
        xs = np.array([x.min(), x.max()])
        fig.add_trace(go.Scatter(x=xs, y=a * xs + b, mode="lines",
                                 line=dict(color=P["ink"], width=2, dash="dash"),
                                 name="추세", hoverinfo="skip"))
    for th, txt in ((cheap_th, "저평가 기준"), (rich_th, "고평가 기준")):
        fig.add_vline(x=th, line=dict(color=P["baseline"], width=1, dash="dot"))
    fig.add_hline(y=0, line=dict(color=P["baseline"], width=1))
    if spearman is not None:
        fig.add_annotation(x=0.02, y=0.98, xref="paper", yref="paper", xanchor="left",
                           showarrow=False, bgcolor="rgba(252,252,251,0.85)",
                           text=f"<b>순위상관 {spearman:+.2f}</b> "
                                f"({'쌀수록 이후 수익↑ = 평균회귀 성립' if spearman < -0.2 else '뚜렷한 관계 약함' if spearman > -0.2 else ''})",
                           font=dict(size=12, color=P["ink"]))
    fig.update_xaxes(title_text="매수 시점의 밸류에이션 백분위 (낮을수록 쌈)", title_font_size=12)
    fig.update_yaxes(title_text="이후 12개월 수익률 (%)", title_font_size=12, ticksuffix="%")
    return _layout(fig, height=400, legend=False)


# ── ⑥ 백테스트: 누적수익 곡선 ───────────────────────────────────────
def backtest_equity(equity: pd.DataFrame, never_traded: bool = False) -> go.Figure | None:
    if equity is None or equity.empty:
        return None
    colors = [P["series1"], P["ink"], P["muted"]]
    dashes = [None, None, "dot"]
    fig = go.Figure()
    for i, col in enumerate(equity.columns):
        if never_traded and i == 0:
            continue  # 전략 미발동이면 무의미한 평평한 선 생략
        fig.add_trace(go.Scatter(
            x=equity.index, y=equity[col], mode="lines", name=col,
            line=dict(color=colors[i % 3], width=2, dash=dashes[i % 3]),
            hovertemplate=f"{col}: %{{y:.2f}}배<extra></extra>"))
    fig.update_layout(hovermode="x unified", title=dict(
        text="누적수익 (시작=1.0) — 예시 타이밍 전략 vs 단순 보유 vs 지수",
        x=0, font=dict(size=12.5, color=P["ink2"])))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(title_text="누적 배수", title_font_size=12)
    return _layout(fig, height=360)


# ── 주가차트: 가격 + 이동평균 + 거래량 ──────────────────────────────
_RANGE_BUTTONS = dict(
    buttons=[dict(count=1, label="1M", step="month", stepmode="backward"),
             dict(count=6, label="6M", step="month", stepmode="backward"),
             dict(count=1, label="1Y", step="year", stepmode="backward"),
             dict(count=3, label="3Y", step="year", stepmode="backward"),
             dict(step="all", label="5Y")],
    font=dict(size=11), bgcolor="#f0efec", activecolor="#9ec5f4", x=0, y=1.12)


def price_chart(ohlcv: pd.DataFrame, currency: str) -> go.Figure:
    df = ohlcv.copy()
    for w, col in ((20, "MA20"), (60, "MA60"), (120, "MA120")):
        df[col] = df["Close"].rolling(w).mean()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        row_heights=[0.76, 0.24])
    fig.add_trace(go.Scatter(x=df.index, y=df["Close"], mode="lines", name="종가",
                             line=dict(color=P["ink"], width=1.6),
                             hovertemplate="%{y:,.0f}<extra>종가</extra>"), row=1, col=1)
    for col, color in (("MA20", P["series1"]), ("MA60", P["series3"]), ("MA120", P["series4"])):
        fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name=col,
                                 line=dict(color=color, width=1.2),
                                 hovertemplate=f"%{{y:,.0f}}<extra>{col}</extra>"), row=1, col=1)
    # 52주 최고/최저
    last1y = df["Close"].tail(252)
    for val, txt, dash in ((last1y.max(), "52주 최고", "dot"), (last1y.min(), "52주 최저", "dot")):
        fig.add_hline(y=float(val), line=dict(color=P["muted"], width=1, dash=dash),
                      annotation_text=f"{txt} {val:,.0f}", annotation_font_size=10,
                      annotation_position="right", row=1, col=1)
    up = df["Close"] >= df["Close"].shift(1)
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="거래량",
                         marker=dict(color=np.where(up, "rgba(42,120,214,0.45)", "rgba(137,135,129,0.4)")),
                         hovertemplate="%{y:,.0f}<extra>거래량</extra>"), row=2, col=1)
    fig.update_xaxes(rangeselector=_RANGE_BUTTONS, rangeslider_visible=False,
                     showgrid=False, row=1, col=1)
    fig.update_xaxes(showgrid=False, row=2, col=1)
    fig.update_yaxes(title_text=f"주가({'원' if currency == 'KRW' else '$'})",
                     title_font_size=11, row=1, col=1)
    fig.update_yaxes(title_text="거래량", title_font_size=11, row=2, col=1)
    return _layout(fig, height=520)


def relative_perf_chart(prices: pd.Series, index_prices: pd.Series,
                        stock_name: str, benchmark: str) -> go.Figure:
    df = pd.concat([prices.rename("s"), index_prices.rename("i")], axis=1).dropna()
    if df.empty:
        return go.Figure()
    df = df / df.iloc[0] * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["s"], mode="lines", name=stock_name,
                             line=dict(color=P["series1"], width=2),
                             hovertemplate="%{y:.0f}<extra>" + stock_name + "</extra>"))
    fig.add_trace(go.Scatter(x=df.index, y=df["i"], mode="lines", name=benchmark,
                             line=dict(color=P["muted"], width=1.6, dash="dot"),
                             hovertemplate="%{y:.0f}<extra>" + benchmark + "</extra>"))
    fig.add_hline(y=100, line=dict(color=P["baseline"], width=1))
    fig.update_layout(hovermode="x unified", title=dict(
        text=f"상대성과 (시작일=100) — {stock_name} vs {benchmark}",
        x=0, font=dict(size=12.5, color=P["ink2"])))
    fig.update_xaxes(rangeselector=_RANGE_BUTTONS, showgrid=False)
    fig.update_yaxes(title_text="지수화 (시작=100)", title_font_size=11)
    return _layout(fig, height=440)
