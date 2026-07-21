"""주식 가치평가 페이지 — 종목 하나를 넣으면 판정·9개 분석 탭.

(구 app.py 본문. 멀티페이지 전환으로 이동 — 로직 변경 없음, render()로 감쌌을 뿐.)
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.backtest import HORIZONS, run_backtest
from src.analysis.capital_cost import compute_capital_cost
from src.analysis.commentary import build_commentary
from src.analysis.indicators import compute_indicators
from src.analysis.scenario import build_scenarios
from src.analysis.scoring import (comparable_peers, compute_scores, peer_median,
                                   rank_peers_cheapness, sanitize_peer_frame)
from src.analysis.valuation import compute_valuation
from src.ui import charts
from src.ui.components import (fmt_money, fmt_pct, fmt_price, fmt_value, fmt_x,
                               label, score_bar_html, section_header_html,
                               verdict_badge_html)

PLOTLY_CFG = charts.PLOTLY_CFG            # 모드바 hover(박스줌·팬·리셋)
PLOTLY_CFG_ZOOM = charts.PLOTLY_CFG_ZOOM  # 시계열 차트: 휠·핀치 줌까지
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

        from src.ui.pages.home import _market_riskfree
        rf_live, rf_src = _market_riskfree(market)
        with st.expander(f"가정 (자본비용·평가) — R_f: {rf_src} {rf_live * 100:.2f}%",
                         expanded=False):
            rf = st.slider("무위험이자율 R_f (%)", 0.5, 8.0, round(rf_live * 100, 1), 0.1,
                           key=f"rf_{market}",
                           help="채권탭의 10년물 국채 금리를 기본값으로 씁니다(WACC 계산에 반영).") / 100
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
        "- 4가지 방법(업종 상대가치 · 역사적 밴드 · RIM · 컨센서스 선행 이익)으로 삼각측량한 **적정주가와 판정**\n"
        "- 증권가 **컨센서스(목표주가·선행 EPS)와 교차검증**, 비관·기준·낙관 **시나리오 분석**\n"
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


def render_help(expanded: bool = False):
    """상세 도움말은 별도 '사용설명서' 페이지(새 탭)로 — 본문을 밀어내지 않게."""
    from src.ui.components import guide_link_html
    st.markdown(guide_link_html("📖 사용법 · 설명서 (새 탭)"), unsafe_allow_html=True)


# ── 탭 렌더러 ───────────────────────────────────────────────────────
def render_summary_tab(d, ind, scores, cc, val):
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown(section_header_html("Fair Value", "적정주가 vs 현재가",
                                        "상대가치 · 역사적 밴드 · RIM · 선행 이익 삼각측량"),
                    unsafe_allow_html=True)
        if val.estimates:
            st.plotly_chart(charts.fair_value_bullet(val.estimates, val.fair_mid,
                                                     d.price, d.currency,
                                                     val.fair_low, val.fair_high),
                            use_container_width=True, config=PLOTLY_CFG)
            # 건너뛴 방법도 번호 자리를 유지해 ①~④가 항상 순서대로 보이게 한다
            canon = ["업종 상대가치", "역사적 밴드", "수익가치(RIM)", "선행 이익(컨센서스)"]
            est_map = {e.method: e for e in val.estimates}
            skip_map = dict(val.skipped)
            order = canon + [e.method for e in val.estimates if e.method not in canon]
            rows = []
            for name in order:
                e = est_map.get(name)
                if e is not None:
                    rows.append({"방법": name,
                                 "가중": f"{val.weights.get(name, 0) * 100:.0f}%",
                                 "적정가 범위": f"{fmt_price(e.low, d.currency)} ~ {fmt_price(e.high, d.currency)}",
                                 "중심": fmt_price(e.mid, d.currency), "근거": e.note})
                elif name in skip_map:
                    rows.append({"방법": name, "가중": "—", "적정가 범위": "건너뜀",
                                 "중심": "—", "근거": skip_map[name]})
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.caption("공식 · ① 피어 중앙값 배수(PER·PBR·EV/EBITDA) × 자사 펀더멘털 · "
                       "② 자기 5년 PER·PBR 25~75분위 × 현재 EPS·BPS · "
                       "③ RIM: V = B + B(ROE−r)·w/(1+r−w), r = CAPM 자기자본비용 · "
                       "④ 컨센서스 12개월 EPS × 자기 5년 PER 중앙값 — "
                       "종합 = 가중평균 ④35·①25·②25·③15% (근거: Liu·Nissim·Thomas 2002, JAR) · "
                       "출처: 재무 OpenDART·Yahoo Finance / 컨센서스 FnGuide(네이버금융)·LSEG I/B/E/S(Yahoo)")
        else:
            st.info("적정주가를 계산할 수 있는 방법이 없습니다 (데이터 부족).")
    with c2:
        st.markdown(section_header_html("Score", "업종 상대 점수",
                                        "백분위 기준 · 50 = 업종 중앙값"),
                    unsafe_allow_html=True)
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
    _render_consensus_summary(d, val)

    st.divider()
    st.markdown(section_header_html("Rationale", "판정 근거", "규칙 기반 자동 해설"),
                unsafe_allow_html=True)
    comments = build_commentary(d, ind, scores, cc, val)
    icons = {"good": "✅", "bad": "🔻", "warn": "⚠️", "info": "ℹ️"}
    funcs = {"good": st.success, "bad": st.error, "warn": st.warning, "info": st.info}
    for cm in comments:
        funcs[cm.kind](cm.text, icon=icons[cm.kind])


def _render_consensus_summary(d, val):
    """요약 탭 — 증권가 컨센서스 vs 우리 모형 교차검증 (목표주가는 판정 계산에 미포함)."""
    st.markdown(section_header_html("Consensus", "시장 컨센서스 교차검증",
                                    "증권가 애널리스트 평균 vs 이 대시보드 모형"),
                unsafe_allow_html=True)
    c = d.consensus
    if c is None or not c.has_any():
        st.caption("애널리스트 컨센서스가 없는 종목입니다 — 증권사 커버리지가 없는 소형주에 흔합니다. "
                   "이 경우 위 적정가 삼각측량만으로 판단 근거를 삼습니다.")
        return
    cols = st.columns(4)
    cols[0].metric("현재가", fmt_price(d.price, d.currency))
    cols[1].metric("모형 종합 적정가", fmt_price(val.fair_mid, d.currency),
                   delta=f"{val.gap * 100:+.1f}%" if val.gap is not None else None)
    tgt_up = c.target_mean / d.price - 1 if c.target_mean and d.price else None
    cols[2].metric("컨센서스 목표주가", fmt_price(c.target_mean, d.currency),
                   delta=f"{tgt_up * 100:+.1f}%" if tgt_up is not None else None)
    rec = f"{c.recomm_label} ({c.recomm_score:.2f}/5)" if c.recomm_label else "—"
    cols[3].metric("투자의견 평균", rec)

    bits = []
    if c.forward_eps:
        g = (f" (TTM 대비 {val.forward_growth * 100:+.0f}%)"
             if val.forward_growth is not None else "")
        bits.append(f"12개월 선행 EPS {fmt_price(c.forward_eps, d.currency)}{g}")
    if c.forward_per:
        bits.append(f"선행 PER {fmt_x(c.forward_per)}")
    if val.fair_mid and c.target_mean:
        diff = val.fair_mid / c.target_mean - 1
        bits.append(f"모형 적정가는 컨센서스 목표가 대비 {diff * 100:+.1f}%")
    # 목표주가 역산 — 증권가가 어떤 멀티플을 깔았는지 되짚어 차이의 원인을 보여준다
    if c.target_mean and c.forward_eps:
        implied = c.target_mean / c.forward_eps
        ours = next((e.mid / c.forward_eps for e in val.estimates
                     if e.method == "선행 이익(컨센서스)" and e.mid), None)
        st.markdown(
            f"**목표주가 역산**: 증권가 목표가({fmt_price(c.target_mean, d.currency)})는 "
            f"선행 EPS × **{implied:.1f}배**를 적용한 셈입니다"
            + (f" — 이 대시보드 ④는 보수 원칙으로 **{ours:.1f}배**를 적용했습니다. "
               "두 값 차이의 대부분은 '정당한 멀티플이 몇 배냐'(성장 프리미엄) 가정에서 나옵니다."
               if ours else ".")
            + " 증권사 리포트의 정성적 근거(수주·신제품·업황 전망)는 무료 데이터에 없어 "
              "이렇게 역산으로만 추정합니다.")
    n = f" · 애널리스트 {c.n_analysts}명" if c.n_analysts else ""
    asof = f" · {c.as_of}" if c.as_of else ""
    st.caption(" · ".join(bits) + f"  \n출처: {c.source}{n}{asof} — 목표주가·추정 EPS는 증권가 "
               "평균이며 매수 편향이 있을 수 있습니다. 판정에는 ④ 선행 이익 방법만 반영하고 "
               "목표주가 자체는 계산에 넣지 않습니다.")


def _render_scenario_section(d, val):
    """밸류에이션 탭 — 비관/기준/낙관 시나리오 + 멀티플×EPS 민감도."""
    st.markdown(section_header_html("Scenario", "시나리오 분석",
                                    "비관·기준·낙관 가정 × 멀티플 민감도 — 예측이 아닌 사고 실험"),
                unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    bear = c1.slider("비관 EPS 조정 (%)", -40, 0, -15, 5, key=f"scn_bear_{d.ticker}") / 100
    bull = c2.slider("낙관 EPS 조정 (%)", 0, 40, 15, 5, key=f"scn_bull_{d.ticker}") / 100
    madj = c3.slider("멀티플 조정 (%)", -30, 30, 0, 5, key=f"scn_mult_{d.ticker}",
                     help="세 케이스의 멀티플에 일괄 적용합니다. 민감도 표는 열 자체가 "
                          "멀티플 축이라 고정입니다.") / 100
    scn = build_scenarios(
        price=d.price,
        eps_fwd=d.consensus.forward_eps if d.consensus else None,
        eps_ttm=d.latest("eps"), per_q=val.per_q,
        peer_per=peer_median(comparable_peers(d.peers, d.market_cap), "per"),
        bear_delta=bear, bull_delta=bull, mult_adjust=madj)
    if scn is None:
        st.info("이익(EPS)이 적자이거나 밴드·피어 데이터가 부족해 이익 기반 시나리오를 "
                "만들 수 없습니다.")
        return
    mc = st.columns(3)
    for col, case in zip(mc, scn.cases):
        up = f"{case.upside * 100:+.1f}%" if case.upside is not None else None
        col.metric(f"{case.name} — EPS {case.eps_delta * 100:+.0f}% × {case.multiple:.1f}배",
                   fmt_price(case.price, d.currency), delta=up)
    st.caption(f"기준 EPS: {fmt_price(scn.eps_base, d.currency)} ({scn.eps_basis}) · "
               f"멀티플: {scn.multiple_basis}")
    if scn.grid is not None:
        price = d.price

        def _bg(v):
            if v is None or not price:
                return ""
            up = v / price - 1
            alpha = min(abs(up) * 0.55, 0.25)
            rgb = "42,120,214" if up >= 0 else "227,73,72"   # 파랑=현재가보다 높음(저평가 방향)
            return f"background-color: rgba({rgb},{alpha:.2f})"

        styled = scn.grid.style.format(lambda v: fmt_price(v, d.currency)).map(_bg)
        st.dataframe(styled, use_container_width=True)
        st.caption("셀 = 해당 EPS 가정 × 멀티플의 이론 가격. 파랑 = 현재가보다 높음(상승여력), "
                   "빨강 = 낮음. 색이 짙을수록 괴리가 큽니다.")
        # 자동 해석 — 표에서 어떤 직관을 얻어야 하는지 한 줄로
        green = int((scn.grid > d.price).values.sum())
        total = int(scn.grid.size)
        bear_up = scn.cases[0].upside
        st.markdown(
            f"**어떻게 읽나** — 이 표는 예측이 아니라 가정 조합의 지도입니다. 지금 가정에서는 "
            f"{total}칸 중 **{green}칸({green / total * 100:.0f}%)**이 현재가 위에 있고, "
            f"비관 케이스는 현재가 대비 **{bear_up * 100:+.1f}%**입니다. "
            "파란 칸이 많다 = 이 가정 범위 안에서 현재가가 낮게 거래된다는 신호이지 상승 보장이 "
            "아니며, 비관 케이스까지 플러스면 하방 완충(안전마진)이 있다고 읽습니다. "
            "출발점이 컨센서스 EPS라서 시장의 이익 전망이 꺾이면 표 전체가 아래로 이동합니다.")
    for note in scn.notes:
        st.caption(f"ⓘ {note}")


def render_valuation_tab(d, ind, val):
    peers = sanitize_peer_frame(d.peers)
    st.markdown(section_header_html("Multiples", "멀티플 비교",
                                    "업종 중앙값 · 자기 5년 밴드"),
                unsafe_allow_html=True)
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
                        use_container_width=True, config=PLOTLY_CFG_ZOOM)
        st.caption("분위선 = 지난 5년 배수 분포(10~90분위)를 현재 펀더멘털에 곱한 가격 수준. "
                   "주가가 짙은 선 위에 있을수록 역사적으로 비싼 영역입니다.")
    else:
        st.info(f"{kind} 밴드를 계산할 수 없습니다 (적자 지속 또는 데이터 부족).")

    st.divider()
    _render_scenario_section(d, val)


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


def render_peers_tab(d, scores, all_peer_names=None):
    basis = next((w for w in d.warnings if w.startswith("피어 기준")), None)
    if basis:
        st.caption(basis)

    # 피어 개별 제외 — 선택 시 적정주가·요약 점수·업종비교·랭킹 전체가 다시 계산됨(재실행)
    if all_peer_names:
        excluded = st.multiselect(
            "🚫 이 업종비교에서 제외할 기업", all_peer_names, key=f"excl_{d.ticker}",
            help="이상치·비교 부적절 기업을 빼면 업종 중앙값·적정주가·판정이 모두 다시 계산됩니다.")
        if excluded:
            st.caption(f"제외 반영 중: {', '.join(excluded)} — 판정 전체에 적용됨. "
                       "위 목록에서 지우면 되돌아갑니다.")

    peers = d.peers.copy()
    if not peers.empty:
        num = lambda col: pd.to_numeric(peers[col], errors="coerce")  # None/문자 섞임 → NaN(— 표기)
        view = pd.DataFrame({
            "종목": peers["name"],
            "시가총액": peers["market_cap"].map(lambda v: fmt_money(v, d.currency)),
            "PER": num("per"), "PBR": num("pbr"),
            "ROE": num("roe") * 100, "영업이익률": num("op_margin") * 100,
            "매출성장": num("rev_growth") * 100,
            "배당수익률": num("div_yield") * 100,
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
        st.markdown(section_header_html("Peer Map", "PER × ROE 지도",
                                        "왼쪽 위(저PER·고ROE)일수록 매력적"),
                    unsafe_allow_html=True)
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)

    st.divider()
    st.markdown(section_header_html("Ranking", "업종 내 저평가·우량 랭킹",
                                    "가치 60% + 수익성 40% · 피어 백분위 종합"),
                unsafe_allow_html=True)
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
                   "⚠️ 저PER이 밸류트랩일 수 있으니 ⑤ 재무·⑧ 백테스트로 교차 확인하세요.")
    else:
        st.info("피어 표본이 적어 랭킹을 만들 수 없습니다.")
    with st.expander("카테고리 점수 상세 (지표별 백분위)"):
        for cat, rows in scores.details.items():
            html = [f"<b>{cat}</b><table style='margin:4px 0 14px;border-collapse:collapse;'>"]
            for key, target, med, sc, _n in rows:
                html.append(
                    "<tr>"
                    f"<td style='padding:2px 14px 2px 0;color:#52514e;'>{label(key)}</td>"
                    f"<td style='padding:2px 14px;text-align:right;'>{fmt_value(key, target, d.currency)}</td>"
                    f"<td style='padding:2px 14px;text-align:right;color:#898781;'>중앙값 {fmt_value(key, med, d.currency)}</td>"
                    f"<td style='padding:2px 0;'>{score_bar_html(sc)}</td></tr>")
            html.append("</table>")
            st.markdown("".join(html), unsafe_allow_html=True)


def render_capital_tab(d, cc, ind):
    st.markdown(section_header_html("Cost of Capital", "자본비용 추정",
                                    "과거 5년 시세 · 베타에서 WACC까지"),
                unsafe_allow_html=True)
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
            st.markdown(section_header_html("Beta", "베타 회귀",
                                            "시장 1% 변동 시 종목의 민감도"),
                        unsafe_allow_html=True)
            st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)
    wf = charts.wacc_waterfall(cc)
    if wf:
        with c2:
            st.markdown(section_header_html("WACC", "WACC 구성",
                                            "자기자본·타인자본 기여"),
                        unsafe_allow_html=True)
            st.plotly_chart(wf, use_container_width=True, config=PLOTLY_CFG)
    rw = charts.roic_wacc_chart(ind.series.get("roic"), cc.roic, cc.wacc)
    if rw:
        st.plotly_chart(rw, use_container_width=True, config=PLOTLY_CFG)
        st.caption("ROIC가 WACC 위에 있어야 성장이 곧 가치 창출입니다. "
                   "WACC 아래라면 성장할수록 가치가 파괴됩니다.")
    for w in cc.warnings:
        st.warning(w, icon="⚠️")


def render_backtest_tab(d):
    st.markdown("### 🎯 우리 툴이 과거에도 맞았나?")
    st.markdown("이 종목이 **우리 기준으로 '크게 저평가'였던 과거 시점**에 샀다면 이후 수익이 "
                "어땠는지 검증합니다. 신호는 복원 가능한 **② 역사적 밴드 + ③ RIM**을 기본가중으로 "
                "합친 종합 저평가율입니다 (① 피어·④ 컨센서스는 과거 시점 복원이 불가/편향이라 제외).")

    c1, c2 = st.columns(2)
    kind = c1.radio("기준 배수", ["PER", "PBR"], horizontal=True, key="bt_kind",
                    help="이익이 안정적이면 PER, 적자·자산주면 PBR을 권합니다.")
    th = c2.slider("저평가 기준 — 적정가 대비 +% 이상 쌀 때 '매수 신호'", 10, 60, 30, 5,
                   key="bt_th",
                   help="앱의 '크게 저평가' 기준은 +30%입니다. 신호가 없으면 낮춰 보세요.") / 100

    bt = run_backtest(d, kind=kind, threshold=th)
    if not bt.ok:
        for w in bt.warnings:
            st.warning(w, icon="⚠️")
        st.info("이 종목은 과거 재무·시세 표본이 부족해 백테스트를 수행할 수 없습니다.")
        return

    ev12 = bt.event_stats.get("12개월", {})
    base12 = bt.baseline_stats.get("12개월", {})
    if bt.signal_days > 0 and ev12.get("mean") is not None:
        m = st.columns(4)
        m[0].metric("비중복 12개월 표본", f"{bt.event_count:,}개",
                    help=f"저평가 신호 자체는 총 {bt.signal_days:,}거래일 관찰됐습니다.")
        m[1].metric("신호 후 12개월 평균수익", fmt_pct(ev12["mean"]))
        m[2].metric("승률(플러스 확률)", fmt_pct(ev12.get("hit")))
        m[3].metric("저평가율↔수익 상관", f"{bt.spearman:+.2f}" if bt.spearman is not None else "—",
                    help="양수면 '쌀수록 이후 수익 높음' = 우리 저평가 신호가 과거에 유효했다는 뜻")
        better = ("높았습니다 ✅" if base12.get("mean") is not None and ev12["mean"] > base12["mean"]
                  else "특별히 높지는 않았습니다")
        st.success(
            f"저평가 신호는 총 **{bt.signal_days:,}거래일** 관찰됐고, 겹치는 보유기간을 제거한 "
            f"**{bt.event_count:,}개 표본**의 12개월 평균 수익률은 **{fmt_pct(ev12['mean'])}** "
            f"(승률 {fmt_pct(ev12.get('hit'))}) — 비중복 전체 표본 평균"
            f"(**{fmt_pct(base12.get('mean'))}**)보다 {better}.", icon="🎯")
    else:
        st.info(f"확보된 기간에 '저평가(+{th * 100:.0f}%↑)' 신호가 없어 이벤트 통계를 낼 수 "
                "없습니다. 위 슬라이더로 기준을 낮춰 보세요.")

    st.caption("⚠️ 단일 종목·짧은 표본이라 과최적화·생존편향에 취약합니다. '경향'으로만 보세요. "
               "거래비용·세금 미반영, 과거 성과가 미래를 보장하지 않습니다.")

    rows = []
    for hz in HORIZONS.keys():
        ev, bs = bt.event_stats.get(hz, {}), bt.baseline_stats.get(hz, {})
        rows.append({"미래 구간": hz,
                     "저평가 매수 후 평균": fmt_pct(ev.get("mean")),
                     "승률": fmt_pct(ev.get("hit")),
                     "신호 표본(일)": ev.get("n", 0),
                     "전체 평균(참고)": fmt_pct(bs.get("mean"))})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    b1, b2 = st.columns(2)
    sc = charts.backtest_scatter(bt.scatter, bt.spearman, bt.threshold)
    if sc:
        with b1:
            st.plotly_chart(sc, use_container_width=True, config=PLOTLY_CFG)
            st.caption("점 하나 = 과거의 하루. 오른쪽(저평가)일수록 이후 12개월 수익이 높으면 "
                       "우리 신호가 유효했다는 뜻입니다.")
    eq = charts.backtest_equity(bt.equity, bt.strategy_never_traded)
    if eq is not None:
        with b2:
            st.plotly_chart(eq, use_container_width=True, config=PLOTLY_CFG_ZOOM)
            if bt.strategy_never_traded:
                st.caption("'저평가' 신호가 없어 전략이 한 번도 투자하지 않았습니다 — 단순 보유·지수만 참고.")
            else:
                parts = [f"{k} {fmt_pct(v)}" for k, v in bt.cagr.items() if v is not None]
                st.caption("연평균수익률(CAGR): " + " · ".join(parts) +
                           " — 전략 = '저평가일 때만 보유, 아니면 현금' 예시.")
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
                        use_container_width=True, config=PLOTLY_CFG_ZOOM)
        st.caption("종가 + 이동평균(20/60/120일) + 거래량. 휠/드래그로 확대(더블클릭 리셋), "
                   "상단 버튼으로 기간을 바꿀 수 있습니다.")
    else:
        st.plotly_chart(
            charts.relative_perf_chart(d.prices, d.index_prices, d.name, d.benchmark_name),
            use_container_width=True, config=PLOTLY_CFG_ZOOM)
        st.caption(f"시작일을 100으로 맞춰 {d.benchmark_name} 지수와 누적 성과를 비교합니다. "
                   "종목 선이 지수 위에 있으면 그만큼 시장을 초과한 것입니다.")


@st.cache_data(ttl=21600, show_spinner=False)
def cached_news_categories(name: str, sector: str, headline_key: tuple, items_json: str):
    """Gemini 분류 캐시 — 실패하면 호출부가 키워드 폴백."""
    import json as _json

    from src.analysis.ai_analysis import classify_news_categories
    return classify_news_categories(name, sector, _json.loads(items_json))


def _gather_news(d) -> list[dict]:
    """기업(종목명) + 산업(업종어) + 거시(금리·환율 등) 헤드라인을 모아 중복 제거."""
    from src.data.news import fetch_news, fetch_topic_news
    items: list[dict] = []
    try:
        items += fetch_news(d.name, d.market, d.yahoo_ticker)
    except Exception:
        pass
    sector_q = (d.sector or d.industry or "").strip()
    if sector_q:
        try:
            items += fetch_topic_news(f"{sector_q} 산업" if d.market == "KR" else f"{sector_q} industry",
                                      d.market, limit=6)
        except Exception:
            pass
    try:
        macro_q = "기준금리 OR 물가 OR 환율" if d.market == "KR" else "Fed OR inflation OR treasury yields"
        items += fetch_topic_news(macro_q, d.market, limit=6)
    except Exception:
        pass
    seen, out = set(), []
    for it in items:
        k = it.get("title", "")[:40]
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


@st.cache_data(ttl=604800, show_spinner=False)
def cached_overview_translation(name: str, text: str) -> str:
    """영문 개요 번역 캐시(7일) — 실패하면 호출부가 영문 원문 폴백."""
    from src.analysis.ai_analysis import translate_overview
    return translate_overview(name, text)


def render_company_intro(d):
    """기업 소개 — KR: 네이버(에프앤가이드) 요약문, US: yfinance 영문(가능하면 AI 번역)."""
    summary, meta = None, []
    if d.market == "KR":
        try:
            from src.data.naver import fetch_company_overview
            ov = fetch_company_overview(d.ticker)
            summary = ov.get("summary")
            if summary and ov.get("source"):
                meta.append(f"출처: {ov['source']}")
        except Exception:
            pass
    else:
        try:
            from src.data.base import fetch_company_profile
            from src.data.gemini import is_available
            prof = fetch_company_profile(d.yahoo_ticker)
            summary = prof.get("summary")
            if summary:
                if is_available():
                    try:
                        summary = cached_overview_translation(d.name, summary)
                        meta.append("출처: Yahoo Finance · AI(Gemini) 번역")
                    except Exception:
                        meta.append("출처: Yahoo Finance (영문 원문)")
                else:
                    meta.append("출처: Yahoo Finance (영문 원문)")
            if prof.get("website"):
                meta.append(prof["website"])
            if prof.get("employees"):
                meta.append(f"직원 {prof['employees']:,}명")
        except Exception:
            pass

    import html as _html

    st.markdown(section_header_html("Company Overview", "기업 소개"),
                unsafe_allow_html=True)
    if summary:
        meta_line = (f"<div class='intro-meta'>{_html.escape(' · '.join(meta))}</div>"
                     if meta else "")
        st.markdown(f"<div class='intro-card'><p>{_html.escape(summary)}</p>{meta_line}</div>",
                    unsafe_allow_html=True)
    else:
        st.info("기업 소개를 불러오지 못했습니다. (무료 데이터 특성상 일부 종목은 개요가 없습니다)")


def render_intro_news_tab(d):
    """① 기업·뉴스 — 이 회사가 뭘 하는 회사인지(소개) + 지금 무슨 일이 있는지(뉴스)."""
    render_company_intro(d)
    st.divider()
    render_news_tab(d)


def render_news_tab(d):
    import json as _json

    from src.analysis.ai_analysis import keyword_classify_news
    from src.data.gemini import is_available

    items = _gather_news(d)
    if not items:
        st.info("관련 뉴스를 찾지 못했습니다. (종목명 검색 결과 없음)")
        return

    # 분류: Gemini(가능하면) → 키워드 폴백. 실패해도 뉴스 자체는 항상 보여준다.
    classified, how = None, "키워드 규칙"
    if is_available():
        try:
            key = tuple(it.get("title", "")[:40] for it in items)
            classified = cached_news_categories(d.name, d.sector or "", key,
                                                _json.dumps(items, ensure_ascii=False))
            how = "AI(Gemini) 분류"
        except Exception:
            classified = None
    if classified is None:
        classified = keyword_classify_news(d.name, d.sector or "", items)

    st.markdown(section_header_html("News", "주요 뉴스",
                                    f"{d.name} · 기업 → 산업 → 거시"),
                unsafe_allow_html=True)
    st.caption(f"출처 Google News · 분류 {how} · 거시 기사에는 PEST(정책·경제·사회·기술) "
               "관점 태그를 붙입니다.")

    from src.ui.components import NEWS_CAT_COLORS, news_badge_html
    sections = [("기업", "이 회사 자체의 소식 — 실적·수주·신제품·지배구조"),
                ("산업", "업종·경쟁사·시장 전반 — 회사의 물길이 되는 흐름"),
                ("거시", "금리·물가·환율·정책 — 모든 자산에 깔리는 바닥 흐름")]
    for cat, desc in sections:
        group = [it for it in classified if it.get("category") == cat]
        if not group:
            continue
        dot = f"<span class='cat-dot' style='background:{NEWS_CAT_COLORS[cat]}'></span>"
        st.markdown(f"##### {dot}{cat} <span class='sec-desc'>{desc}</span>",
                    unsafe_allow_html=True)
        for it in group:
            meta = " · ".join(x for x in (it.get("source"), it.get("date")) if x)
            badges = " ".join(news_badge_html(t) for t in it.get("tags", []))
            # 마크다운 링크는 같은 탭에서 열려 분석 화면을 대체하므로 HTML 앵커(새 탭)로 렌더
            link = it.get("link") or "#"
            st.markdown(
                f"- <a href='{link}' target='_blank' rel='noopener'>{it['title']}</a> {badges} "
                f"<span style='color:#898781;font-size:0.85em;'>{meta}</span>",
                unsafe_allow_html=True)

    st.divider()
    ai_key = f"news_ai_{d.ticker}"
    if is_available():
        if st.button("AI 뉴스 분석", type="primary", key="btn_news_ai"):
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
    st.markdown(section_header_html("AI Opinion", "AI 종합 투자평가",
                                    "대시보드가 계산한 사실 근거 · 투자 매력도"),
                unsafe_allow_html=True)
    if not is_available():
        st.info("💡 이 탭은 **Gemini API 키**가 필요합니다. `.streamlit/secrets.toml`에 "
                "`GEMINI_API_KEY = \"...\"` 를 넣고 새로고침하세요. (무료 키: aistudio.google.com)")
        return

    from src.analysis.ai_analysis import build_opinion_context
    news_sum = st.session_state.get(f"news_ai_{d.ticker}", "")
    prof = st.session_state.get("risk_profile")
    ctx = build_opinion_context(d, ind, val, cc, scores, news_sum, risk_profile=prof)
    hints = []
    if not news_sum:
        hints.append("① 기업·뉴스 탭에서 'AI 뉴스 분석'을 실행하면 뉴스까지 반영됩니다")
    if prof:
        st.caption(f"🧭 투자성향({prof['label']}) 반영 — 성향별 맞춤 조언이 포함됩니다.")
    else:
        hints.append("홈에서 투자성향 테스트를 하면 성향 맞춤 조언까지 받을 수 있습니다")
    if hints:
        st.caption("ℹ️ " + " · ".join(hints) + ".")

    op_key = f"opinion_{d.ticker}"
    if st.button("종합 투자평가 생성", type="primary", key="btn_ai_op"):
        try:
            with st.spinner("AI가 밸류에이션·재무·자본비용·뉴스를 종합하는 중..."):
                st.session_state[op_key] = cached_opinion(d.ticker, ctx)
        except Exception as e:
            st.error(f"AI 평가 실패: {e}")
    if st.session_state.get(op_key):
        st.markdown(st.session_state[op_key])
    with st.expander("AI에 전달된 분석 컨텍스트(사실 요약) 보기"):
        st.code(ctx, language="text")


# ── 포트폴리오 담기 ─────────────────────────────────────────────────
def _render_basket_button(d):
    """분석 중인 종목을 포트폴리오 바스켓(session_state)에 담는다."""
    basket = st.session_state.setdefault("basket", {})
    if d.yahoo_ticker in basket:
        st.caption("포트폴리오에 담겨 있습니다 — 포트폴리오 페이지에서 비중을 정하세요.")
        return
    if st.button("+ 포트폴리오에 담기", key=f"basket_{d.yahoo_ticker}",
                 use_container_width=True):
        basket[d.yahoo_ticker] = {
            "name": d.name, "yahoo": d.yahoo_ticker, "ticker": d.ticker,
            "type": "국내주식" if d.market == "KR" else "해외주식",
            "currency": d.currency,
        }
        st.toast(f"'{d.name}'을(를) 담았습니다 — 🧺 포트폴리오 페이지에서 확인하세요.")
        st.rerun()


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

    # 업종비교 탭에서 제외한 피어를 판정 전체(적정주가·점수·랭킹)에서 뺀다. is_self는 항상 유지.
    # multiselect 옵션이 사라지지 않도록 원본 피어명은 따로 보관해 탭으로 넘긴다.
    all_peer_names = (sorted(d.peers.loc[~d.peers["is_self"], "name"].dropna().unique().tolist())
                      if not d.peers.empty and "is_self" in d.peers.columns else [])
    excluded = st.session_state.get(f"excl_{d.ticker}", [])
    if excluded and not d.peers.empty:
        keep = d.peers["is_self"] | ~d.peers["name"].isin(excluded)
        d.peers = d.peers[keep].copy()

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
        st.markdown(f"<div style='text-align:right;font-weight:700;"
                    f"font-size:clamp(1.2rem, 0.9rem + 0.9vw, 1.7rem);"
                    f"font-variant-numeric:tabular-nums;'>"
                    f"{fmt_price(d.price, d.currency)}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align:right;'>{verdict_badge_html(val.verdict, val.gap, val.confidence)}</div>",
                    unsafe_allow_html=True)
        _render_basket_button(d)

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

    tabs = st.tabs(["① 기업·뉴스", "② 요약·판정", "③ 주가차트", "④ 밸류에이션", "⑤ 재무 분석",
                    "⑥ 업종 비교", "⑦ 자본비용(WACC)", "⑧ 백테스트", "⑨ 종합 투자평가(AI)"])
    with tabs[0]:
        render_intro_news_tab(d)
    with tabs[1]:
        render_summary_tab(d, ind, scores, cc, val)
    with tabs[2]:
        render_price_tab(d)
    with tabs[3]:
        render_valuation_tab(d, ind, val)
    with tabs[4]:
        render_financial_tab(d, ind)
    with tabs[5]:
        render_peers_tab(d, scores, all_peer_names)
    with tabs[6]:
        render_capital_tab(d, cc, ind)
    with tabs[7]:
        render_backtest_tab(d)
    with tabs[8]:
        render_ai_tab(d, ind, val, cc, scores)

    st.divider()
    st.caption("ⓘ 본 대시보드는 공개 데이터를 이용한 학습·분석 보조 도구이며, 특정 종목의 매수·매도 추천이 아닙니다. "
               "재무: OpenDART(한국 공시 원본) · Yahoo Finance · 네이버금융 · KRX(FinanceDataReader) | 지수: KOSPI·KOSDAQ·S&P 500")
