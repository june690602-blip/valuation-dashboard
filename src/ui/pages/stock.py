"""주식 가치평가 페이지 — 종목 하나를 넣으면 판정·9개 분석 탭.

(구 app.py 본문. 멀티페이지 전환으로 이동 — 로직 변경 없음, render()로 감쌌을 뿐.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from src.analysis.backtest import HORIZONS, run_backtest
from src.analysis.capital_cost import compute_capital_cost
from src.analysis.commentary import build_commentary
from src.analysis.indicators import compute_indicators
from src.analysis.scoring import (compute_scores, peer_median,
                                   rank_peers_cheapness, sanitize_peer_frame)
from src.analysis.valuation import compute_valuation
from src.ui import charts
from src.ui.components import (fmt_money, fmt_pct, fmt_price, fmt_value, fmt_x,
                               label, score_bar_html, verdict_badge_html)

PLOTLY_CFG = {"displayModeBar": False}
EXAMPLES = {
    "KR": [("삼성전자", "005930"), ("현대차", "005380"), ("NAVER", "035420"), ("KB금융", "105560")],
    "US": [("Apple", "AAPL"), ("Microsoft", "MSFT"), ("Coca-Cola", "KO"), ("Rivian", "RIVN")],
}


# ── 데이터 로드 (캐시) ───────────────────────────────────────────────
@st.cache_data(ttl=1800, show_spinner=False)
def load_company(market: str, query: str, peer_count: int):
    if market == "KR":
        from src.data.kr_provider import KRProvider
        return KRProvider().load(query, peer_count)
    from src.data.us_provider import USProvider
    return USProvider().load(query, peer_count)


@st.cache_data(ttl=21600, show_spinner=False)
def cached_news_analysis(name: str, headlines: tuple):
    from src.analysis.ai_analysis import analyze_news
    items = [{"date": d, "title": t, "source": s} for (d, t, s) in headlines]
    return analyze_news(name, items)


@st.cache_data(ttl=21600, show_spinner=False)
def cached_opinion(ticker: str, context: str):
    from src.analysis.ai_analysis import investment_opinion
    return investment_opinion(context)


# ── 사이드바 ────────────────────────────────────────────────────────
def _render_sidebar():
    with st.sidebar:
        st.markdown("### 📈 종목 분석")
        market = st.radio("시장", ["KR", "US"], horizontal=True,
                          format_func=lambda m: "🇰🇷 한국" if m == "KR" else "🇺🇸 미국")
        query = st.text_input(
            "종목 (코드 또는 이름)",
            key=f"query_{market}",
            placeholder="예: 005930, 삼성전자" if market == "KR" else "예: AAPL, Apple",
        )
        st.caption("예시: " + " · ".join(f"{n}" for n, _ in EXAMPLES[market]))

        with st.expander("가정 (자본비용·평가)", expanded=False):
            rf = st.slider("무위험이자율 R_f (%)", 0.5, 8.0,
                           3.5 if market == "KR" else 4.5, 0.1, key=f"rf_{market}") / 100
            mrp = st.slider("시장위험프리미엄 MRP (%)", 3.0, 10.0,
                            6.0 if market == "KR" else 5.0, 0.5, key=f"mrp_{market}") / 100
            peer_count = st.slider("업종 피어 수", 5, 15, 10)
            use_custom_r = st.checkbox("RIM 요구수익률 직접 지정", value=False,
                                       help="기본값은 CAPM 자기자본비용(k_e)입니다.")
            custom_r = st.slider("RIM 요구수익률 r (%)", 4.0, 15.0, 9.0, 0.5) / 100 \
                if use_custom_r else None

        if st.button("데이터 캐시 비우기", use_container_width=True):
            st.cache_data.clear()
            st.toast("캐시를 비웠습니다. 다시 분석하면 최신 데이터를 받아옵니다.")
        st.caption("본 도구는 학습·분석 보조용이며 투자 조언이 아닙니다. "
                   "데이터: Yahoo Finance·네이버금융·KRX, 한국 재무는 OpenDART 공시 원본 우선. "
                   "무료 소스 특성상 값이 실제 공시와 다를 수 있습니다.")
    return market, query, rf, mrp, peer_count, custom_r


# ── 랜딩 화면 ───────────────────────────────────────────────────────
def render_landing(market: str):
    st.title("기업 가치평가 대시보드")
    st.markdown(
        "**종목 하나를 입력하면** 재무제표·주가·업종 데이터를 자동 수집해 다음을 보여줍니다.\n"
        "- 5개 카테고리(밸류에이션·수익성·성장성·안정성·현금흐름) **업종 상대 점수**\n"
        "- 3가지 방법(업종 상대가치 · 역사적 밴드 · RIM)으로 삼각측량한 **적정주가와 판정**\n"
        "- 과거 시세로 회귀한 **베타 → 영업위험 자본비용 → WACC** 분해\n"
        "- 낮은 멀티플이 기회인지 밸류트랩인지 가려주는 **자동 해설**"
    )
    st.subheader("예시로 시작하기")
    cols = st.columns(4)
    for i, (name, code) in enumerate(EXAMPLES[market]):
        # on_click 콜백에서 위젯키를 세팅해야 함(위젯 생성 후 직접 수정하면 예외)
        cols[i].button(f"{name}\n({code})", use_container_width=True,
                       key=f"ex_{market}_{code}", on_click=_use_example, args=(market, code))
    st.caption("왼쪽 사이드바에서 시장을 바꾸고 종목 코드나 이름을 입력해도 됩니다.")
    render_help(expanded=True)


def _use_example(m: str, code: str):
    """예시 버튼 콜백 — 종목 입력창(위젯키)에 코드를 채운다."""
    st.session_state[f"query_{m}"] = code


HELP_MD = """
**이 도구를 3분 만에 쓰는 법**

1. 왼쪽에서 시장(🇰🇷/🇺🇸)을 고르고 **종목 코드나 이름**을 입력하세요. (예: `005930`, `AAPL`)
2. 상단의 **판정 배지**(크게 저평가 ~ 크게 고평가)와 **신뢰도**를 먼저 봅니다.
3. 탭을 순서대로 읽으면 됩니다.

| 탭 | 무엇을 답해주나 | 핵심만 보면 |
|---|---|---|
| ① 요약·판정 | 지금 주가가 적정한가? 왜? | 불릿차트(현재가 vs 적정가)와 자동 해설 |
| ② 주가차트 | 주가 흐름·이동평균·지수 대비 | 추세, 52주 위치, 시장 대비 성과 |
| ③ 밸류에이션 | 업종·자기역사 대비 싼가 비싼가 | 🔵=업종보다 쌈 / 🔴=비쌈, PER/PBR 밴드 |
| ④ 재무 분석 | 회사 체력(매출·이익·빚·현금) | 매출·이익 우상향? 마진 방향? |
| ⑤ 업종 비교 | 경쟁사 중 어디쯤인가 | PER×ROE 지도, 저평가·우량 랭킹 |
| ⑥ 자본비용 | 요구수익률·WACC, 가치 창출 여부 | ROIC > WACC 면 초과수익 창출 |
| ⑦ 백테스트 | 이 신호가 과거에 통했나 | 쌀 때 이후 수익이 실제로 높았나 |
| ⑧ 주요뉴스 | 최근 뉴스와 AI 해석 | 감성·촉매·리스크 (AI 키 필요) |
| ⑨ 종합 투자평가 | 지금 투자 매력도(AI 종합) | 강세/약세 논거, 스탠스 (AI 키 필요) |

> 업종분류가 부정확하면(예: 삼성전자=통신장비) **Gemini 키**를 넣어 AI가 업종·경쟁사를 다시 잡습니다.

**판정은 세 방법(업종 상대가치·역사적 밴드·RIM)의 평균 괴리율**로 냅니다.
`괴리율 = 적정가 ÷ 현재가 − 1`. +30%↑ 크게 저평가, ±10% 적정, −30%↓ 크게 고평가.
세 방법이 서로 많이 다르면 **신뢰도 '낮음'** 으로 표시되니 그땐 단정하지 마세요.

> ⚠️ 무료 데이터를 쓰는 **학습·분석 보조 도구**입니다. 매수·매도 추천이 아니고,
> 값이 실제 공시와 다를 수 있습니다. 더 자세한 설명은 저장소의 `docs/사용설명서.md`를 보세요.
"""


def render_help(expanded: bool = False):
    with st.expander("❓ 사용법 — 처음이신가요?", expanded=expanded):
        st.markdown(HELP_MD)


# ── 탭 렌더러 ───────────────────────────────────────────────────────
def render_summary_tab(d, ind, scores, cc, val):
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**적정주가 범위 vs 현재가** — 방법별 삼각측량")
        if val.estimates:
            st.plotly_chart(charts.fair_value_bullet(val.estimates, val.fair_mid,
                                                     d.price, d.currency),
                            use_container_width=True, config=PLOTLY_CFG)
            rows = [{"방법": e.method,
                     "적정가 범위": f"{fmt_price(e.low, d.currency)} ~ {fmt_price(e.high, d.currency)}",
                     "중심": fmt_price(e.mid, d.currency), "근거": e.note}
                    for e in val.estimates]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.info("적정주가를 계산할 수 있는 방법이 없습니다 (데이터 부족).")
    with c2:
        st.markdown("**업종 상대 점수** — 50이 업종 중앙값")
        valid = {k: v for k, v in scores.scores.items() if v is not None}
        if len(valid) >= 3:
            st.plotly_chart(charts.radar(scores.scores),
                            use_container_width=True, config=PLOTLY_CFG)
        else:
            st.info("피어 데이터가 부족해 레이더 차트를 그릴 수 없습니다.")
        if scores.overall is not None:
            st.caption(f"종합 점수 **{scores.overall:.0f}/100** · 피어 {scores.n_peers}개 대비 "
                       "백분위 평균 (밸류에이션은 높을수록 '싸다'는 뜻)")

    st.divider()
    st.subheader("왜 이런 판정이 나왔나 — 자동 해설")
    comments = build_commentary(d, ind, scores, cc, val)
    icons = {"good": "✅", "bad": "🔻", "warn": "⚠️", "info": "ℹ️"}
    funcs = {"good": st.success, "bad": st.error, "warn": st.warning, "info": st.info}
    for cm in comments:
        funcs[cm.kind](cm.text, icon=icons[cm.kind])


def render_valuation_tab(d, ind, val):
    peers = sanitize_peer_frame(d.peers)
    st.markdown("**멀티플 비교** — 업종 중앙값·자기 5년 밴드와 함께 보기")
    rows = []
    band_q50 = {"per": (val.per_q or {}).get(50), "pbr": (val.pbr_q or {}).get(50)}
    for key in ("per", "pbr", "psr", "ev_ebitda", "p_fcf", "div_yield", "peg"):
        cur = ind.valuation.get(key)
        med = peer_median(peers, key)
        row = {"지표": label(key),
               "현재": fmt_value(key, cur, d.currency),
               "업종 중앙값": fmt_value(key, med, d.currency),
               "자기 5년 중앙값": fmt_x(band_q50.get(key)) if band_q50.get(key) else "—"}
        if cur is not None and med:
            diff = cur / med - 1
            cheaper = diff < 0 if key != "div_yield" else diff > 0
            row["vs 업종"] = f"{'🔵' if cheaper else '🔴'} {abs(diff) * 100:.0f}% {'낮음' if diff < 0 else '높음'}"
        else:
            row["vs 업종"] = "—"
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if d.official.get("source"):
        ref = " · ".join(f"{k} {fmt_x(v) if isinstance(v, (int, float)) and k not in ('DIV',) else fmt_pct(v) if k == 'DIV' else v}"
                         for k, v in d.official.items()
                         if k in ("PER", "선행PER", "PBR", "DIV") and v is not None)
        st.caption(f"공식 참고치 ({d.official['source']}): {ref} — 계산 기준(TTM) 차이로 위 표와 다를 수 있습니다.")

    st.divider()
    kind = st.radio("역사적 밴드", ["PER", "PBR"], horizontal=True, label_visibility="collapsed")
    band = val.per_band if kind == "PER" else val.pbr_band
    pct = val.per_percentile if kind == "PER" else val.pbr_percentile
    if band is not None:
        st.markdown(f"**5년 {kind} 밴드** — 현재 위치: 하위 {pct:.0f}% "
                    f"({'싼 구간' if pct < 35 else '비싼 구간' if pct > 65 else '중간 구간'})")
        st.plotly_chart(charts.band_chart(band, d.currency, kind),
                        use_container_width=True, config=PLOTLY_CFG)
        st.caption("분위선 = 지난 5년 배수 분포(10~90분위)를 현재 펀더멘털에 곱한 가격 수준. "
                   "주가가 짙은 선 위에 있을수록 역사적으로 비싼 영역입니다.")
    else:
        st.info(f"{kind} 밴드를 계산할 수 없습니다 (적자 지속 또는 데이터 부족).")


def render_financial_tab(d, ind):
    st.plotly_chart(charts.growth_chart(ind.series, d.currency),
                    use_container_width=True, config=PLOTLY_CFG)
    if not d.is_financial:
        st.plotly_chart(charts.stability_chart(ind.series),
                        use_container_width=True, config=PLOTLY_CFG)
        st.plotly_chart(charts.cashflow_chart(ind.series, d.currency),
                        use_container_width=True, config=PLOTLY_CFG)
    else:
        st.info("금융업은 부채·현금흐름 지표가 일반 기업과 다른 의미라 생략합니다.")
    with st.expander("연간 재무제표 (원본 요약)"):
        cols = ["revenue", "gross_profit", "operating_income", "net_income",
                "total_assets", "total_equity", "total_liabilities", "total_debt",
                "cash", "interest_expense", "ocf", "capex", "fcf", "eps"]
        fin = d.financials[[c for c in cols if c in d.financials.columns]].copy()
        show = fin.T
        show.index = [label(i) if i in ("ocf", "fcf") else
                      {"revenue": "매출액", "gross_profit": "매출총이익",
                       "operating_income": "영업이익", "net_income": "당기순이익",
                       "total_assets": "자산총계", "total_equity": "자본총계",
                       "total_liabilities": "부채총계", "total_debt": "이자부차입금",
                       "cash": "현금성자산", "interest_expense": "이자비용",
                       "capex": "CAPEX", "eps": "EPS"}.get(i, i) for i in show.index]
        st.dataframe(show.map(lambda v: fmt_money(v, d.currency)
                              if pd.notna(v) and abs(v) > 1e4 else
                              (f"{v:,.0f}" if pd.notna(v) else "—")),
                     use_container_width=True)


def render_peers_tab(d, scores):
    basis = next((w for w in d.warnings if w.startswith("피어 기준")), None)
    if basis:
        st.caption(basis)
    peers = d.peers.copy()
    if not peers.empty:
        view = pd.DataFrame({
            "종목": peers["name"],
            "시가총액": peers["market_cap"].map(lambda v: fmt_money(v, d.currency)),
            "PER": peers["per"], "PBR": peers["pbr"],
            "ROE": peers["roe"] * 100, "영업이익률": peers["op_margin"] * 100,
            "매출성장": peers["rev_growth"] * 100,
            "배당수익률": peers["div_yield"] * 100,
        })
        self_mask = peers["is_self"].values

        def _hl(row):
            return ["background-color: rgba(42,120,214,0.12)" if self_mask[row.name] else ""
                    for _ in row]

        view = view.reset_index(drop=True)
        st.dataframe(
            view.style.apply(_hl, axis=1).format(
                {"PER": "{:.1f}", "PBR": "{:.2f}", "ROE": "{:.1f}%",
                 "영업이익률": "{:.1f}%", "매출성장": "{:.1f}%", "배당수익률": "{:.2f}%"},
                na_rep="—"),
            hide_index=True, use_container_width=True, height=400)
    fig = charts.peer_scatter(sanitize_peer_frame(d.peers), d.currency)
    if fig:
        st.markdown("**PER × ROE 지도** — 왼쪽 위(저PER·고ROE)일수록 매력적")
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)

    st.divider()
    st.markdown("**업종 내 저평가·우량 랭킹** — 가치 60% + 수익성 40% (피어 백분위 종합)")
    rank = rank_peers_cheapness(d.peers, d.is_financial)
    if len(rank) >= 3:
        rview = pd.DataFrame({
            "순위": range(1, len(rank) + 1),
            "종목": rank["name"].values,
            "종합점수": rank["combined"].values,
            "가치(쌈)": rank["value_score"].values,
            "수익성": rank["quality_score"].values,
            "PER": rank["per"].values, "PBR": rank["pbr"].values,
            "ROE": (rank["roe"] * 100).values,
        })
        self_pos = rank["is_self"].values

        def _hl_rank(row):
            return ["background-color: rgba(42,120,214,0.12)" if self_pos[row.name] else ""
                    for _ in row]

        st.dataframe(
            rview.style.apply(_hl_rank, axis=1).format(
                {"종합점수": "{:.0f}", "가치(쌈)": "{:.0f}", "수익성": "{:.0f}",
                 "PER": "{:.1f}", "PBR": "{:.2f}", "ROE": "{:.1f}%"}, na_rep="—"),
            hide_index=True, use_container_width=True)
        st.caption("점수는 이 업종 피어 안에서의 상대 백분위입니다(100=업종 최고). "
                   "‘가치’는 PER·PBR·PSR·EV/EBITDA가 낮을수록, ‘수익성’은 ROE·영업이익률이 높을수록 높습니다. "
                   "⚠️ 저PER이 밸류트랩일 수 있으니 ④ 재무·⑦ 백테스트로 교차 확인하세요.")
    else:
        st.info("피어 표본이 적어 랭킹을 만들 수 없습니다.")
    with st.expander("카테고리 점수 상세 (지표별 백분위)"):
        for cat, rows in scores.details.items():
            html = [f"<b>{cat}</b><table style='margin:4px 0 14px;border-collapse:collapse;'>"]
            for key, target, med, sc in rows:
                html.append(
                    "<tr>"
                    f"<td style='padding:2px 14px 2px 0;color:#52514e;'>{label(key)}</td>"
                    f"<td style='padding:2px 14px;text-align:right;'>{fmt_value(key, target, d.currency)}</td>"
                    f"<td style='padding:2px 14px;text-align:right;color:#898781;'>중앙값 {fmt_value(key, med, d.currency)}</td>"
                    f"<td style='padding:2px 0;'>{score_bar_html(sc)}</td></tr>")
            html.append("</table>")
            st.markdown("".join(html), unsafe_allow_html=True)


def render_capital_tab(d, cc, ind):
    st.markdown("**과거 5년 시세로 추정한 자본비용** — 베타에서 WACC까지")
    if cc.period_label:
        st.caption(f"회귀 표본: {cc.period_label} (벤치마크 {d.benchmark_name})")

    r1 = st.columns(4)
    r1[0].metric("레버드 베타 β_L", f"{cc.beta_l:.2f}" if cc.beta_l is not None else "—",
                 help="주간 수익률을 시장지수에 회귀한 기울기 — 시장 대비 총위험(영업+재무)")
    r1[1].metric("무부채 베타 β_U", f"{cc.beta_u:.2f}" if cc.beta_u is not None else "—",
                 help="하마다 식으로 재무레버리지를 벗긴 순수 영업위험 베타")
    r1[2].metric("유효세율 t", fmt_pct(cc.tax_rate))
    r1[3].metric("D/E (시가 기준)", fmt_pct(cc.de_ratio))

    r2 = st.columns(4)
    r2[0].metric("영업위험만의 자본비용 k_U", fmt_pct(cc.k_u),
                 help="R_f + β_U × MRP — 부채가 전혀 없다고 가정한 요구수익률")
    r2[1].metric("재무위험 프리미엄", fmt_pct(cc.financial_risk_premium),
                 help="k_e − k_U — 차입 때문에 주주가 추가로 요구하는 수익률")
    r2[2].metric("자기자본비용 k_e", fmt_pct(cc.k_e), help="R_f + β_L × MRP (CAPM)")
    r2[3].metric("타인자본비용 k_d", fmt_pct(cc.k_d), help=cc.k_d_source or "")

    if cc.wacc is not None:
        r3 = st.columns(3)
        r3[0].metric("WACC", fmt_pct(cc.wacc))
        r3[1].metric("ROIC (TTM)", fmt_pct(cc.roic))
        if cc.spread is not None:
            r3[2].metric("ROIC − WACC 스프레드", f"{cc.spread * 100:+.1f}%p",
                         delta=f"{'가치 창출' if cc.spread > 0 else '가치 잠식'}",
                         delta_color="normal" if cc.spread > 0 else "inverse")

    c1, c2 = st.columns(2)
    fig = charts.beta_scatter(cc.reg_points, cc.beta_l_raw or cc.beta_l, cc.r2,
                              d.benchmark_name)
    if fig:
        with c1:
            st.markdown("**베타 회귀** — 시장이 1% 움직일 때 이 종목은?")
            st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)
    wf = charts.wacc_waterfall(cc)
    if wf:
        with c2:
            st.markdown("**WACC 구성** — 자기자본·타인자본 기여")
            st.plotly_chart(wf, use_container_width=True, config=PLOTLY_CFG)
    rw = charts.roic_wacc_chart(ind.series.get("roic"), cc.roic, cc.wacc)
    if rw:
        st.plotly_chart(rw, use_container_width=True, config=PLOTLY_CFG)
        st.caption("ROIC가 WACC 위에 있어야 성장이 곧 가치 창출입니다. "
                   "WACC 아래라면 성장할수록 가치가 파괴됩니다.")
    for w in cc.warnings:
        st.warning(w, icon="⚠️")


def render_backtest_tab(d):
    st.markdown("**밸류에이션 신호가 실제로 통했는지 과거로 검증** — "
                "이 종목의 PER/PBR이 자기 역사에서 쌀 때 샀다면 이후 수익이 좋았을까?")
    st.info(
        "여기서 검증하는 건 ③ **역사적 밴드** 신호 하나입니다. 종합 판정(피어 중앙값·RIM)은 "
        "과거 시점의 업종 데이터를 되살리기 어려워 제외했습니다. **단일 종목·짧은 표본**이라 "
        "과최적화·생존편향에 취약하니 '경향'만 참고하세요. 거래비용·세금은 반영하지 않았습니다.",
        icon="🧪")

    c1, c2 = st.columns([1, 1])
    kind = c1.radio("기준 배수", ["PER", "PBR"], horizontal=True, key="bt_kind",
                    help="이익이 안정적이면 PER, 적자·자산주면 PBR을 권합니다.")
    window_years = c2.slider("롤링 비교 기간(년)", 1.0, 3.0, 1.5, 0.5, key="bt_win",
                             help="'쌀 때/비쌀 때'를 판단할 때 직전 몇 년의 분포와 비교할지")

    bt = run_backtest(d, kind=kind, window_years=window_years)
    if not bt.ok:
        for w in bt.warnings:
            st.warning(w, icon="⚠️")
        st.info("이 종목은 과거 재무·시세 표본이 부족해 백테스트를 수행할 수 없습니다.")
        return

    top = st.columns(3)
    top[0].metric("유효 표본", f"{bt.n_obs:,}일")
    if bt.spearman is not None:
        top[1].metric("순위상관(쌈 ↔ 이후수익)", f"{bt.spearman:+.2f}",
                      help="음수(-)면 '쌀수록 이후 수익이 높음' = 평균회귀가 작동. "
                           "0 근처면 밸류에이션과 이후 수익의 관계가 약함.")
    hz = top[2].radio("미래수익 구간", list(HORIZONS.keys()), index=2,
                      horizontal=True, key="bt_hz")

    b1, b2 = st.columns(2)
    fig = charts.backtest_bucket_bar(bt.bucket_returns, bt.bucket_hit, hz)
    if fig:
        with b1:
            st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)
            cnt = " · ".join(f"{k} {v}일" for k, v in bt.bucket_counts.items())
            st.caption(f"표본 수(12개월 기준): {cnt}")
    sc = charts.backtest_scatter(bt.scatter, bt.spearman, bt.cheap_th, bt.rich_th)
    if sc:
        with b2:
            st.plotly_chart(sc, use_container_width=True, config=PLOTLY_CFG)

    eq = charts.backtest_equity(bt.equity, bt.strategy_never_traded)
    if eq is not None:
        st.plotly_chart(eq, use_container_width=True, config=PLOTLY_CFG)
        if bt.strategy_never_traded:
            st.caption("이 기간엔 '저평가' 신호가 없어 타이밍 전략이 한 번도 투자하지 않았습니다 "
                       "(곡선 생략). 단순 보유·지수만 비교하세요.")
        else:
            parts = [f"{k} {v * 100:.1f}%" for k, v in bt.cagr.items() if v is not None]
            st.caption("연평균수익률(CAGR): " + " · ".join(parts) +
                       " — 타이밍 전략은 '저평가일 때만 보유, 아니면 현금'인 예시입니다.")
    for w in bt.warnings:
        st.warning(w, icon="⚠️")


def render_price_tab(d):
    from src.data.base import fetch_ohlcv
    try:
        ohlcv = fetch_ohlcv(d.yahoo_ticker)
    except Exception:
        st.info("주가 데이터를 불러오지 못했습니다.")
        return
    close = ohlcv["Close"]
    hi52, lo52 = float(close.tail(252).max()), float(close.tail(252).min())
    ret5y = close.iloc[-1] / close.iloc[0] - 1
    ret1y = close.iloc[-1] / close.tail(252).iloc[0] - 1 if len(close) >= 252 else None
    pos52 = (close.iloc[-1] - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else None

    m = st.columns(4)
    m[0].metric("현재가", fmt_price(d.price, d.currency))
    m[1].metric("52주 최고 / 최저", f"{hi52:,.0f} / {lo52:,.0f}",
                help="최근 1년 종가 기준")
    m[2].metric("최근 1년 수익률", fmt_pct(ret1y) if ret1y is not None else "—")
    m[3].metric("52주 밴드 내 위치", f"{pos52:.0f}%" if pos52 is not None else "—",
                help="0%=52주 최저, 100%=52주 최고")

    mode = st.radio("보기", ["절대 주가", "지수 대비 상대성과"], horizontal=True,
                    key="price_mode", label_visibility="collapsed")
    if mode == "절대 주가":
        st.plotly_chart(charts.price_chart(ohlcv, d.currency),
                        use_container_width=True, config=PLOTLY_CFG)
        st.caption("종가 + 이동평균(20/60/120일) + 거래량. 상단 버튼으로 기간을 바꿀 수 있습니다.")
    else:
        st.plotly_chart(
            charts.relative_perf_chart(d.prices, d.index_prices, d.name, d.benchmark_name),
            use_container_width=True, config=PLOTLY_CFG)
        st.caption(f"시작일을 100으로 맞춰 {d.benchmark_name} 지수와 누적 성과를 비교합니다. "
                   "종목 선이 지수 위에 있으면 그만큼 시장을 초과한 것입니다.")


def render_news_tab(d):
    from src.data.gemini import is_available
    from src.data.news import fetch_news
    try:
        items = fetch_news(d.name, d.market, d.yahoo_ticker)
    except Exception:
        items = []
    if not items:
        st.info("관련 뉴스를 찾지 못했습니다. (종목명 검색 결과 없음)")
        return

    st.markdown(f"**{d.name} 최근 뉴스 헤드라인** — 출처: Google News")
    for it in items:
        meta = " · ".join(x for x in (it.get("source"), it.get("date")) if x)
        link = it.get("link") or "#"
        st.markdown(f"- [{it['title']}]({link})  <span style='color:#898781;font-size:0.85em;'>{meta}</span>",
                    unsafe_allow_html=True)

    st.divider()
    ai_key = f"news_ai_{d.ticker}"
    if is_available():
        if st.button("🤖 AI 뉴스 분석 실행", type="primary", key="btn_news_ai"):
            blob = tuple((it.get("date", ""), it.get("title", ""), it.get("source", ""))
                         for it in items)
            try:
                with st.spinner("Gemini가 뉴스를 분석하는 중..."):
                    st.session_state[ai_key] = cached_news_analysis(d.name, blob)
            except Exception as e:
                st.error(f"AI 분석 실패: {e}")
        if st.session_state.get(ai_key):
            st.markdown(st.session_state[ai_key])
            st.caption("이 요약은 ⑨ 종합 투자평가 탭에도 반영됩니다.")
    else:
        st.info("💡 **Gemini API 키**를 설정하면 위 헤드라인을 AI가 감성·핵심이슈·촉매·리스크로 "
                "분석해 줍니다. `.streamlit/secrets.toml`에 `GEMINI_API_KEY`를 넣으세요 (README 참고).")


def render_ai_tab(d, ind, val, cc, scores):
    from src.data.gemini import is_available
    st.markdown("**기본적 분석 + 뉴스를 종합한 AI 투자평가** — "
                "대시보드가 계산한 사실을 근거로 지금 투자 매력도를 평가합니다.")
    if not is_available():
        st.info("💡 이 탭은 **Gemini API 키**가 필요합니다. `.streamlit/secrets.toml`에 "
                "`GEMINI_API_KEY = \"...\"` 를 넣고 새로고침하세요. (무료 키: aistudio.google.com)")
        return

    from src.analysis.ai_analysis import build_opinion_context
    news_sum = st.session_state.get(f"news_ai_{d.ticker}", "")
    ctx = build_opinion_context(d, ind, val, cc, scores, news_sum)
    if not news_sum:
        st.caption("ℹ️ 뉴스까지 반영하려면 먼저 ⑧ 주요뉴스 탭에서 'AI 뉴스 분석'을 실행하세요.")

    op_key = f"opinion_{d.ticker}"
    if st.button("🤖 종합 투자평가 생성", type="primary", key="btn_ai_op"):
        try:
            with st.spinner("AI가 밸류에이션·재무·자본비용·뉴스를 종합하는 중..."):
                st.session_state[op_key] = cached_opinion(d.ticker, ctx)
        except Exception as e:
            st.error(f"AI 평가 실패: {e}")
    if st.session_state.get(op_key):
        st.markdown(st.session_state[op_key])
    with st.expander("AI에 전달된 분석 컨텍스트(사실 요약) 보기"):
        st.code(ctx, language="text")


# ── 페이지 엔트리 ───────────────────────────────────────────────────
def render():
    market, query, rf, mrp, peer_count, custom_r = _render_sidebar()

    if not query or not query.strip():
        render_landing(market)
        st.stop()

    try:
        with st.spinner(f"'{query}' 데이터 수집 중... (첫 조회는 피어 수집으로 수십 초 걸릴 수 있어요)"):
            d = load_company(market, query.strip(), peer_count)
    except Exception as e:
        st.error(f"데이터를 가져오지 못했습니다: {e}")
        st.stop()

    ind = compute_indicators(d)
    scores = compute_scores(d.peers, d.yahoo_ticker, d.is_financial)
    cc = compute_capital_cost(d, rf=rf, mrp=mrp)
    val = compute_valuation(d, ind, r_equity=custom_r or cc.k_e)

    # 헤더
    h1, h2 = st.columns([3, 2])
    with h1:
        st.markdown(f"## {d.name} <span style='color:#898781;font-size:1.1rem;'>"
                    f"{d.ticker} · {d.benchmark_name}</span>", unsafe_allow_html=True)
        sub = " · ".join(x for x in (d.sector, d.industry) if x)
        asof = d.prices.index[-1].strftime("%Y-%m-%d")
        fin_src = d.official.get("재무출처", "")
        badge = f"  |  📄 재무 {fin_src}" if fin_src else ""
        st.caption(f"{sub or '업종 정보 없음'}  |  기준일 {asof}{badge}")
    with h2:
        st.markdown(f"<div style='text-align:right;font-size:1.7rem;font-weight:700;'>"
                    f"{fmt_price(d.price, d.currency)}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align:right;'>{verdict_badge_html(val.verdict, val.gap, val.confidence)}</div>",
                    unsafe_allow_html=True)

    m = st.columns(6)
    m[0].metric("시가총액", fmt_money(d.market_cap, d.currency))
    m[1].metric("PER (TTM)", fmt_x(ind.valuation.get("per")))
    m[2].metric("PBR", fmt_x(ind.valuation.get("pbr")))
    m[3].metric("ROE (TTM)", fmt_pct(ind.profitability.get("roe")))
    m[4].metric("베타", f"{cc.beta_l:.2f}" if cc.beta_l is not None else "—")
    m[5].metric("WACC", fmt_pct(cc.wacc) if cc.wacc else "N/A")

    hcol1, hcol2 = st.columns([3, 1])
    with hcol1:
        quality = [w for w in d.warnings if not w.startswith(("피어 기준", "재무제표:"))]
        if quality:
            with st.expander(f"⚠️ 데이터 품질 참고 {len(quality)}건"):
                for w in quality:
                    st.markdown(f"- {w}")
    with hcol2:
        render_help(expanded=False)

    tabs = st.tabs(["① 요약·판정", "② 주가차트", "③ 밸류에이션", "④ 재무 분석", "⑤ 업종 비교",
                    "⑥ 자본비용(WACC)", "⑦ 백테스트", "⑧ 주요뉴스", "⑨ 종합 투자평가(AI)"])
    with tabs[0]:
        render_summary_tab(d, ind, scores, cc, val)
    with tabs[1]:
        render_price_tab(d)
    with tabs[2]:
        render_valuation_tab(d, ind, val)
    with tabs[3]:
        render_financial_tab(d, ind)
    with tabs[4]:
        render_peers_tab(d, scores)
    with tabs[5]:
        render_capital_tab(d, cc, ind)
    with tabs[6]:
        render_backtest_tab(d)
    with tabs[7]:
        render_news_tab(d)
    with tabs[8]:
        render_ai_tab(d, ind, val, cc, scores)

    st.divider()
    st.caption("ⓘ 본 대시보드는 공개 데이터를 이용한 학습·분석 보조 도구이며, 특정 종목의 매수·매도 추천이 아닙니다. "
               "재무: OpenDART(한국 공시 원본) · Yahoo Finance · 네이버금융 · KRX(FinanceDataReader) | 지수: KOSPI·KOSDAQ·S&P 500")
