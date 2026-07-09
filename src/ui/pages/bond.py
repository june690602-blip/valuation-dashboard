"""채권 페이지 — 국고채·미 국채 수익률곡선과 '금리 시나리오 분석기'.

주식 페이지가 '적정가 vs 시장가'라면, 이 페이지는 '금리가 움직이면 채권 가격이
얼마나 변하나'를 듀레이션·볼록성·DV01로 답한다. 대상은 국공채만(회사채는 무료로
신뢰할 시세·신용스프레드 소스가 없어 제외).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from src.analysis.bond_math import (bond_metrics, price_yield_points,
                                    rate_scenarios)
from src.ui import charts

PLOTLY_CFG = {"displayModeBar": False}
FACE = 100.0  # 호가 관례에 맞춘 액면 100 기준


@st.cache_data(ttl=3600, show_spinner=False)
def _load_curves():
    from src.data.bonds import fetch_policy_rates, fetch_yield_curve
    return fetch_yield_curve("KR"), fetch_yield_curve("US"), fetch_policy_rates()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_history(market: str, tenor: int):
    from src.data.bonds import fetch_yield_history
    return fetch_yield_history(market, tenor)


def _curve_yield(curve: pd.DataFrame, tenor: float) -> float | None:
    try:
        if curve is not None and not curve.empty and tenor in curve.index:
            return float(curve.loc[tenor, "yield"])
    except Exception:
        pass
    return None


# ── 섹션 1: 지금 금리 ───────────────────────────────────────────────
def _render_curve_section(kr: pd.DataFrame, us: pd.DataFrame, policy: dict):
    st.markdown("### 📉 지금 금리 — 수익률곡선")
    m = st.columns(4)
    m[0].metric("한국은행 기준금리", f"{policy['한국은행']:.2f}%" if "한국은행" in policy else "—")
    m[1].metric("미국 연준 기준금리", f"{policy['미국 연준']:.2f}%" if "미국 연준" in policy else "—")
    kr10, us10 = _curve_yield(kr, 10), _curve_yield(us, 10)
    m[2].metric("한국 국고채 10년", f"{kr10:.3f}%" if kr10 is not None else "—")
    m[3].metric("미국 국채 10년", f"{us10:.3f}%" if us10 is not None else "—")

    fig = charts.yield_curve_chart({"🇰🇷 한국 국고채": kr, "🇺🇸 미국 국채": us})
    if fig:
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)
        notes = []
        kr3 = _curve_yield(kr, 3)
        us2 = _curve_yield(us, 2)
        if kr10 is not None and kr3 is not None:
            sp = (kr10 - kr3) * 100
            notes.append(f"한국 10−3년 스프레드 **{sp:+.0f}bp** ({'정상(우상향)' if sp > 0 else '역전 — 침체 신호로 자주 해석'})")
        if us10 is not None and us2 is not None:
            sp = (us10 - us2) * 100
            notes.append(f"미국 10−2년 스프레드 **{sp:+.0f}bp** ({'정상' if sp > 0 else '역전'})")
        asof = ""
        for df in (kr, us):
            if df is not None and not df.empty and "asof" in df.columns:
                asof = str(df["asof"].iloc[-1])
                break
        st.caption((" · ".join(notes) + f"  |  기준일 {asof}") if notes else f"기준일 {asof}")
    else:
        st.warning("수익률곡선 데이터를 가져오지 못했습니다. 아래 시나리오 분석기는 금리를 "
                   "직접 입력해 계속 쓸 수 있습니다.", icon="⚠️")


# ── 섹션 2: 금리 추이 ───────────────────────────────────────────────
def _render_history_section():
    st.markdown("### 📈 금리 추이")
    c1, c2 = st.columns([1, 2])
    market = c1.radio("국가", ["KR", "US"], horizontal=True, key="bd_hist_mkt",
                      format_func=lambda m: "🇰🇷 한국" if m == "KR" else "🇺🇸 미국")
    tenors = [1, 2, 3, 5, 10, 20, 30] if market == "KR" else [1, 2, 3, 5, 7, 10, 20, 30]
    tenor = c2.select_slider("만기", tenors, value=10, key=f"bd_hist_tenor_{market}")
    with st.spinner("금리 시계열 불러오는 중..."):
        hist = _load_history(market, int(tenor))
    label = f"{'한국 국고채' if market == 'KR' else '미국 국채'} {tenor}년"
    fig = charts.yield_history_chart(hist, label)
    if fig:
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)
        chg = (hist["yield"].iloc[-1] - hist["yield"].iloc[0]) * 100 if len(hist) > 1 else None
        if chg is not None:
            st.caption(f"표시 구간 변화: **{chg:+.0f}bp** · 표본 {len(hist)}일 · "
                       "출처: " + ("네이버 시장지표" if market == "KR" else "FRED"))
    else:
        st.info("이 만기의 시계열을 가져오지 못했습니다.")


# ── 섹션 3: 시나리오 분석기 ─────────────────────────────────────────
def _render_scenario_section(kr: pd.DataFrame, us: pd.DataFrame):
    st.markdown("### 🧮 금리 시나리오 분석기 — 가상 국채로 감 잡기")
    st.caption("선택한 만기의 **현재 금리로 발행된 액면채**(쿠폰=YTM, 가격≈100)를 가정하고, "
               "금리가 움직일 때 가격이 얼마나 변하는지 봅니다. 실제 보유 채권이 있다면 "
               "쿠폰·만기·YTM을 직접 바꿔 넣으세요.")

    c0, c1, c2 = st.columns([1, 1, 1])
    market = c0.radio("국가", ["KR", "US"], horizontal=True, key="bd_sc_mkt",
                      format_func=lambda m: "🇰🇷 한국" if m == "KR" else "🇺🇸 미국")
    curve = kr if market == "KR" else us
    tenor_opts = [1, 2, 3, 5, 10, 20, 30] if market == "KR" else [1, 2, 3, 5, 7, 10, 20, 30]
    tenor = c1.selectbox("만기 (년)", tenor_opts, index=tenor_opts.index(10), key=f"bd_sc_tenor_{market}")
    freq = c2.radio("이표 지급", [2, 1], horizontal=True, key="bd_sc_freq",
                    format_func=lambda f: "반기(연 2회)" if f == 2 else "연 1회")

    cur = _curve_yield(curve, float(tenor))
    default_ytm = cur if cur is not None else (3.5 if market == "KR" else 4.5)
    if cur is None:
        st.warning("현재 금리를 불러오지 못해 기본값을 넣어뒀습니다 — 직접 조정하세요.", icon="⚠️")

    i1, i2, i3 = st.columns(3)
    ytm = i1.number_input("YTM (%)", 0.1, 20.0, round(float(default_ytm), 3), 0.05,
                          key=f"bd_sc_ytm_{market}_{tenor}") / 100
    coupon = i2.number_input("쿠폰율 (%) — 기본값=YTM(액면발행)", 0.0, 20.0,
                             round(float(default_ytm), 3), 0.05,
                             key=f"bd_sc_cpn_{market}_{tenor}") / 100
    years = i3.number_input("잔존만기 (년)", 0.5, 50.0, float(tenor), 0.5,
                            key=f"bd_sc_yrs_{market}_{tenor}")

    m = bond_metrics(FACE, coupon, ytm, years, freq)
    r = st.columns(5)
    r[0].metric("가격 (액면 100)", f"{m['price']:.2f}")
    r[1].metric("맥컬리 듀레이션", f"{m['macaulay']:.2f}년",
                help="현금흐름의 가중평균 회수기간. 만기·쿠폰이 길고 낮을수록 커집니다.")
    r[2].metric("수정 듀레이션", f"{m['modified']:.2f}",
                help="금리 1%p(100bp) 상승 시 가격이 약 −수정듀레이션 % 변한다는 1차 근사.")
    r[3].metric("볼록성", f"{m['convexity']:.1f}",
                help="듀레이션 근사의 오차를 보정하는 2차항. 클수록 금리 하락 이득 > 상승 손실.")
    r[4].metric("DV01", f"{m['dv01'] * 100:.2f}bp상당",
                help="금리 1bp 변화당 가격 변화(액면 100 기준 통화단위).")

    # 시나리오 표 — 근사 vs 정확
    rows = rate_scenarios(FACE, coupon, ytm, years, freq)
    tbl = pd.DataFrame([{
        "금리 충격": f"{r_['shock_bp']:+d}bp",
        "정확 가격": f"{r_['exact_price']:.2f}",
        "정확 변화율": f"{r_['exact_pct'] * 100:+.2f}%",
        "듀레이션 근사": f"{r_['dur_pct'] * 100:+.2f}%",
        "듀레이션+볼록성": f"{r_['durconv_pct'] * 100:+.2f}%",
    } for r_ in rows])
    st.dataframe(tbl, hide_index=True, use_container_width=True)
    st.caption("충격이 클수록(±100bp) **듀레이션 근사가 정확값과 어긋나고**, 볼록성 보정을 더하면 "
               "거의 맞아떨어집니다. 또 같은 ±100bp라도 **하락 이득이 상승 손실보다 큰 비대칭**이 "
               "보이는데, 그것이 볼록성의 가치입니다.")

    # price-yield 곡선
    span = max(0.02, ytm * 0.9)
    grid = np.linspace(max(ytm - span, 0.0005), ytm + span, 80)
    prices = price_yield_points(FACE, coupon, years, freq, grid)
    st.plotly_chart(charts.price_yield_chart(grid, prices, ytm, m["price"], m["modified"]),
                    use_container_width=True, config=PLOTLY_CFG)

    with st.expander("무이표채(제로쿠폰)로 직관 확인하기"):
        st.markdown(
            "쿠폰율을 **0**으로 두면 맥컬리 듀레이션이 **정확히 잔존만기와 같아**집니다 — "
            "현금흐름이 만기 한 번뿐이라 가중평균 회수기간=만기이기 때문입니다. "
            "쿠폰을 올릴수록 중간에 돈을 돌려받아 듀레이션이 짧아지는 것도 확인해 보세요.")


# ── 섹션 4: 금리·중앙은행 뉴스 ──────────────────────────────────────
def _render_news_section():
    st.markdown("### 📰 금리·중앙은행 뉴스")
    try:
        from src.data.news import fetch_topic_news
        items = fetch_topic_news("기준금리 OR 국고채 금리 OR 연준", "KR", limit=8)
    except Exception:
        items = []
    if not items:
        st.info("관련 뉴스를 찾지 못했습니다.")
        return
    for it in items:
        meta = " · ".join(x for x in (it.get("source"), it.get("date")) if x)
        st.markdown(f"- [{it['title']}]({it.get('link') or '#'})  "
                    f"<span style='color:#898781;font-size:0.85em;'>{meta}</span>",
                    unsafe_allow_html=True)


# ── 페이지 엔트리 ───────────────────────────────────────────────────
def render():
    with st.sidebar:
        st.markdown("### 🏦 채권")
        st.caption("국고채·미 국채 **현물 금리** 기준입니다. 회사채·신용스프레드는 무료 신뢰 "
                   "소스가 없어 다루지 않습니다. 개인의 채권 매매차익은 비과세(이자만 과세)라는 "
                   "점도 참고하세요.")
        if st.button("금리 캐시 비우기", use_container_width=True):
            st.cache_data.clear()
            st.toast("캐시를 비웠습니다.")

    st.title("🏦 채권 — 국채 금리·시나리오")

    with st.spinner("국고채·미 국채 금리 불러오는 중..."):
        kr, us, policy = _load_curves()

    _render_curve_section(kr, us, policy)
    st.divider()
    _render_history_section()
    st.divider()
    _render_scenario_section(kr, us)
    st.divider()
    _render_news_section()

    st.divider()
    st.caption("ⓘ 학습·분석 보조 도구입니다. 금리: 네이버 시장지표·FRED(무키) — 고시·체결 기준에 "
               "따라 실제 거래 금리와 다를 수 있습니다. 포트폴리오 페이지의 채권 통계는 "
               "국채 ETF 시세로 계산합니다(개별 채권 일별 시세가 없기 때문).")
