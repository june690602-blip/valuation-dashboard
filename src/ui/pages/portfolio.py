"""포트폴리오 페이지 — 담은 자산으로 σ-기대수익 평면·성과지표·세금 참고.

- 주식 페이지에서 "🧺 담기"한 종목 + 국채 ETF·금·리츠·달러·예금 프리셋으로 구성
- 모든 통계는 **원화 환산 월간 수익률**(달러 자산은 환노출 포함) 기준
- CML(KOSPI200+국고채 / S&P500+미국채)은 판정선이 아니라 **참고선**
- 세금은 참고용 표기(분석 지표는 전부 세전) — 프로젝트 톤: 근거는 보여주되 단정하지 않기
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src.analysis.portfolio import (after_tax_row, annualize, monthly_returns_krw,
                                    performance, portfolio_point, portfolio_series)
from src.ui import charts
from src.ui.pages.home import MARKET_DEFAULTS, _market_riskfree, _market_sigma

PLOTLY_CFG = charts.PLOTLY_CFG            # 히트맵 등: 모드바 hover
PLOTLY_CFG_ZOOM = charts.PLOTLY_CFG_ZOOM  # σ-E(r) 평면: 휠·핀치 줌

# 프리셋 자산 (이름, 야후티커, 세금유형, 표시통화, 분류) — KRW=X는 이미 '1달러의 원화 가격'
PRESETS = [
    ("KODEX 국고채3년", "114260.KS", "국내ETF", "KRW", "채권"),
    ("KOSEF 국고채10년", "148070.KS", "국내ETF", "KRW", "채권"),
    ("iShares 미국채 7-10년 (IEF)", "IEF", "해외ETF", "USD", "채권"),
    ("iShares 미국채 20년+ (TLT)", "TLT", "해외ETF", "USD", "채권"),
    ("ACE KRX금현물", "411060.KS", "국내ETF", "KRW", "금"),
    ("TIGER 리츠부동산인프라", "329200.KS", "국내ETF", "KRW", "리츠(부동산 대용)"),
    ("달러 현금 (USD/KRW)", "KRW=X", "달러현금", "KRW", "외화"),
]
DEFAULT_AMOUNT = 500  # 새 자산 기본 금액(만원)


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_px(yahoo: str, period: str = "5y") -> pd.Series | None:
    try:
        from src.data.base import fetch_prices
        return fetch_prices(yahoo, period)
    except Exception:
        return None


# 유명 투자자 전략 σ·E(r) 계산용 자산군 대표 프록시
ASSET_CLASS_PROXY = {"주식": "^KS200", "채권": "148070.KS", "금": "411060.KS", "리츠": "329200.KS"}


@st.cache_data(ttl=3600, show_spinner=False)
def _asset_class_stats(months: int, rf: float):
    """자산군(주식/채권/금/리츠/현금) 연율화 μ·σ·공분산 — 전략 원형 점 계산용. 실패 시 None."""
    prices, cur = {}, {}
    for cls, yt in ASSET_CLASS_PROXY.items():
        px = _fetch_px(yt)
        if px is not None:
            prices[cls], cur[cls] = px, "KRW"
    if not prices:
        return None
    fx = _fetch_px("KRW=X")
    m = monthly_returns_krw(prices, fx, cur, months=months, cash_rates={"현금": rf})
    return annualize(m) if not m.empty else None


def _basket() -> dict:
    return st.session_state.setdefault("basket", {})


def _amounts() -> dict:
    return st.session_state.setdefault("basket_amounts", {})


# ── 자산 추가/구성 UI ───────────────────────────────────────────────
def _render_add_assets():
    basket = _basket()
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 2], vertical_alignment="bottom")
        opts = [p for p in PRESETS if p[1] not in basket]
        label = c1.selectbox("프리셋 자산 추가 (국채 ETF·금·리츠·달러)",
                             [p[0] for p in opts] or ["(모두 추가됨)"], key="pf_preset")
        if c2.button("➕ 추가", use_container_width=True, disabled=not opts):
            p = next(p for p in opts if p[0] == label)
            basket[p[1]] = {"name": p[0], "yahoo": p[1], "ticker": p[1],
                            "type": p[2], "currency": p[3], "class": p[4]}
            st.rerun()
        if c3.button("💰 예금·적금 추가", use_container_width=True,
                     disabled="CASH" in basket):
            basket["CASH"] = {"name": "예금(무위험)", "yahoo": None, "ticker": "CASH",
                              "type": "예금", "currency": "KRW", "class": "무위험"}
            st.rerun()
        st.caption("주식은 📈 주식 가치평가 페이지에서 분석 후 **🧺 담기** 버튼으로 추가하세요. "
                   "부동산 직접투자는 일별 시세가 없어 **리츠 ETF로 대용**합니다.")


def _render_composition() -> tuple[pd.Series, dict]:
    """자산별 금액 입력 → (비중 Series, 예금 금리 dict). 삭제 버튼 포함."""
    basket, amounts = _basket(), _amounts()
    st.markdown("#### 구성 자산과 금액")
    header = st.columns([3, 2, 2, 2, 1])
    for col, txt in zip(header, ["자산", "구분", "금액(만원)", "비중", ""]):
        col.markdown(f"**{txt}**")

    total = sum(float(amounts.get(k, DEFAULT_AMOUNT)) for k in basket)
    cash_rates = {}
    for key in list(basket.keys()):
        a = basket[key]
        c = st.columns([3, 2, 2, 2, 1], vertical_alignment="center")
        c[0].markdown(f"{a['name']}")
        c[1].caption(a.get("class") or a["type"])
        amt = c[2].number_input("금액", 0.0, 1e7, float(amounts.get(key, DEFAULT_AMOUNT)),
                                50.0, key=f"amt_{key}", label_visibility="collapsed")
        amounts[key] = amt
        c[3].markdown(f"{amt / total * 100:.0f}%" if total > 0 else "—")
        if c[4].button("🗑", key=f"del_{key}", help="이 자산 빼기"):
            basket.pop(key, None)
            amounts.pop(key, None)
            st.rerun()
        if a["type"] == "예금":
            cash_rates[key] = st.slider("예금 금리 (연 %)", 0.5, 8.0, 3.0, 0.1,
                                        key="pf_cash_rate") / 100

    total = sum(float(amounts.get(k, DEFAULT_AMOUNT)) for k in basket)
    st.caption(f"합계 **{total:,.0f}만원** — 금액은 비중 계산에만 쓰이며 저장하지 않는 한 "
               "세션 종료 시 사라집니다.")
    weights = pd.Series({k: float(amounts.get(k, DEFAULT_AMOUNT)) / total
                         for k in basket}) if total > 0 else pd.Series(dtype=float)
    return weights, cash_rates


def _render_save_load():
    basket, amounts = _basket(), _amounts()
    with st.sidebar.expander("💾 저장 / 불러오기", expanded=False):
        payload = json.dumps({"basket": basket, "amounts": amounts},
                             ensure_ascii=False, indent=1)
        st.download_button("구성 내려받기(JSON)", payload, "portfolio.json",
                           "application/json", use_container_width=True)
        up = st.file_uploader("구성 불러오기", type="json", key="pf_upload")
        if up is not None and st.button("업로드 적용", use_container_width=True):
            try:
                data = json.loads(up.getvalue().decode("utf-8"))
                st.session_state["basket"] = dict(data.get("basket", {}))
                st.session_state["basket_amounts"] = dict(data.get("amounts", {}))
                st.rerun()
            except Exception as e:
                st.error(f"불러오기 실패: {e}")


# ── 통계·차트 ───────────────────────────────────────────────────────
def _asset_prices(basket: dict) -> tuple[dict, dict]:
    prices, currencies = {}, {}
    for key, a in basket.items():
        if a["type"] == "예금":
            continue
        px = _fetch_px(a["yahoo"])
        if px is not None:
            prices[key] = px
            currencies[key] = a["currency"]
    return prices, currencies


def _cml_params(market: str) -> tuple[float, float, float]:
    md = MARKET_DEFAULTS[market]
    rf = _market_riskfree(market)[0]  # 채권탭 라이브 국채 금리 공유
    sigma = _market_sigma(md["symbol"], md["sigma"])[0]
    return rf, rf + md["mrp"], sigma


def render():
    with st.sidebar:
        st.markdown("### 🧺 포트폴리오")
        months = st.select_slider("통계 기간(개월)", [36, 48, 60], value=60,
                                  help="월간 수익률 표본 길이 — 길수록 안정적, 짧을수록 최근 반영")
        bench_market = st.radio("성과 벤치마크", ["KR", "US"], horizontal=True,
                                format_func=lambda m: "🇰🇷 KOSPI200" if m == "KR" else "🇺🇸 S&P 500")
        rf_live, rf_src = _market_riskfree(bench_market)
        rf = st.slider("무위험이자율 R_f (%)", 0.5, 8.0, round(rf_live * 100, 1), 0.1,
                       key=f"pf_rf_{bench_market}",
                       help=f"기본값 = {rf_src} {rf_live * 100:.2f}% (채권탭 연동)") / 100
    _render_save_load()

    st.title("🧺 포트폴리오")
    basket = _basket()

    _render_add_assets()
    if not basket:
        st.info("아직 담은 자산이 없습니다. 위에서 프리셋을 추가하거나, 📈 주식 가치평가 "
                "페이지에서 종목을 분석한 뒤 **🧺 포트폴리오에 담기**를 눌러 시작하세요.", icon="🧺")
        return

    weights, cash_rates = _render_composition()
    if weights.empty:
        st.warning("금액이 전부 0입니다 — 금액을 입력해야 비중을 계산할 수 있어요.")
        return

    # ── 수익률 통계 (원화 환산) ──
    with st.spinner("자산 시세 수집·통계 계산 중..."):
        prices, currencies = _asset_prices(basket)
        fx = _fetch_px("KRW=X")
        monthly = monthly_returns_krw(prices, fx, currencies, months=months,
                                      cash_rates=cash_rates)
    if monthly.empty or len(monthly.columns) == 0:
        st.error("자산 시세를 가져오지 못해 통계를 계산할 수 없습니다.")
        return

    excluded = [basket[k]["name"] for k in basket
                if k not in monthly.columns and basket[k]["type"] != "예금"]
    if excluded:
        st.warning("시세 이력이 부족해 통계에서 제외: " + ", ".join(excluded), icon="⚠️")
    weights = weights.reindex(monthly.columns).fillna(0.0)
    if weights.sum() > 0:
        weights = weights / weights.sum()

    stats = annualize(monthly)
    name_of = {k: basket[k]["name"] for k in monthly.columns}

    st.divider()
    st.markdown("### 📊 자산 통계 — 원화 환산 월간 수익률 기준")
    st.caption(f"표본 {stats['n_months']}개월 · 달러 자산은 환율 변화 포함(환노출) · "
               "기대수익은 과거 실측 연율화라 **추정 오차가 큽니다** — 아래에서 직접 조정 가능.")

    # 기대수익 오버라이드 (과거치 노이즈 대응)
    mu_used = stats["mu"].copy()
    with st.expander("기대수익 E(r) 직접 조정 (기본값=과거 실측)", expanded=False):
        for k in monthly.columns:
            mu_used[k] = st.number_input(
                f"{name_of[k]} (연 %)", -30.0, 50.0, round(float(stats["mu"][k]) * 100, 2),
                0.25, key=f"mu_ov_{k}") / 100

    view = pd.DataFrame({
        "자산": [name_of[k] for k in monthly.columns],
        "비중": [f"{weights[k] * 100:.0f}%" for k in monthly.columns],
        "기대수익(연)": [f"{mu_used[k] * 100:+.1f}%" for k in monthly.columns],
        "변동성 σ(연)": [f"{stats['sigma'][k] * 100:.1f}%" for k in monthly.columns],
    })
    st.dataframe(view, hide_index=True, use_container_width=True)

    # 상관/공분산
    heat_mode = st.radio("행렬 보기", ["상관계수", "공분산"], horizontal=True,
                         key="pf_heat", label_visibility="collapsed")
    mat = (stats["corr"] if heat_mode == "상관계수" else stats["cov"]).copy()
    mat.index = [name_of[k] for k in mat.index]
    mat.columns = [name_of[k] for k in mat.columns]
    st.plotly_chart(charts.corr_heatmap(mat, heat_mode == "상관계수"),
                    use_container_width=True, config=PLOTLY_CFG)
    st.caption("상관이 낮은(0 근처·음수) 자산을 섞을수록 같은 기대수익에서 포트폴리오 σ가 "
               "줄어듭니다 — 분산투자의 수학적 근거입니다.")

    # ── σ-E(r) 평면 ──
    st.divider()
    st.markdown("### 🗺️ σ-기대수익 평면 — 내 포트폴리오는 어디에 있나")
    port = portfolio_point(weights, mu_used, stats["cov"])

    assets_df = pd.DataFrame({
        "sigma": stats["sigma"], "er": mu_used, "weight": weights,
    })
    assets_df.index = [name_of[k] for k in assets_df.index]

    cmls = {}
    for mkt in ("KR", "US"):
        rf_m, er_m, sig_m = _cml_params(mkt)
        cmls[MARKET_DEFAULTS[mkt]["label"]] = (rf_m, er_m, sig_m)

    optimal = None
    prof = st.session_state.get("risk_profile")
    if prof:
        from src.analysis.risk_profile import tangency_point
        rf_m, er_m, sig_m = _cml_params(prof.get("market", "KR"))
        t = tangency_point(er_m, rf_m, sig_m, float(prof["A"]))
        optimal = {"sigma": t["sigma_p"], "er": t["er_p"],
                   "label": f"성향 모형 참고점 ({prof['label']})"}

    # 유명 투자자 전략 원형 (선택 시 평면에 함께 표시)
    from src.analysis.famous import STRATEGIES, strategy_weights
    acs = _asset_class_stats(months, rf)
    strat_df, strat_rows = None, []
    with st.expander("🏛️ 유명 투자자 전략과 비교 (재미로 겹쳐보기)", expanded=False):
        if acs is None:
            st.caption("자산군 프록시 시세를 불러오지 못해 전략 비교를 건너뜁니다.")
            picks = []
        else:
            picks = st.multiselect(
                "평면에 겹쳐 볼 전략", list(STRATEGIES.keys()),
                help="대표 전략을 우리 자산군(주식/채권/금/리츠/현금) 비중으로 근사한 예시입니다. "
                     "실제 보유종목·수익률이 아닙니다.")
            for name in picks:
                w = pd.Series(strategy_weights(name)).reindex(acs["mu"].index).fillna(0.0)
                pt = portfolio_point(w, acs["mu"], acs["cov"])
                sharpe = (pt["er"] - rf) / pt["sigma"] if pt["sigma"] > 0 else None
                strat_rows.append({"전략": name, "sigma": pt["sigma"], "er": pt["er"],
                                   "sharpe": sharpe, "note": STRATEGIES[name][1]})
            if strat_rows:
                strat_df = pd.DataFrame(strat_rows).set_index("전략")[["sigma", "er"]]

    st.plotly_chart(charts.risk_return_plane(assets_df, port, cmls, optimal, strat_df),
                    use_container_width=True, config=PLOTLY_CFG)

    p1, p2 = st.columns(2)
    p1.metric("내 포트폴리오 기대수익", f"{port['er'] * 100:+.1f}%(연)")
    p2.metric("내 포트폴리오 변동성 σ", f"{port['sigma'] * 100:.1f}%(연)")
    st.caption("CML은 '지수+무위험 단순 혼합'이 만드는 참고선입니다. 통화·시장이 다른 자산이 "
               "섞인 포트폴리오라 **위/아래 자체가 우열 판정은 아니며**, 같은 σ에서 기대수익이 "
               "어느 정도인지 가늠하는 눈금으로 쓰세요.")
    if prof and port["sigma"] > 0:
        diff = port["sigma"] - (optimal["sigma"] if optimal else 0)
        if optimal:
            if diff > 0.03:
                st.warning(f"현재 포트폴리오 σ({port['sigma']*100:.1f}%)가 성향 모형 참고점"
                           f"({optimal['sigma']*100:.1f}%)보다 **높습니다** — {prof['label']} "
                           "자가진단과 모형 가정에 비해 변동성이 큰 편입니다.", icon="⚖️")
            elif diff < -0.03:
                st.info(f"현재 포트폴리오 σ({port['sigma']*100:.1f}%)가 성향 모형 참고점"
                        f"({optimal['sigma']*100:.1f}%)보다 **낮습니다**. 이것만으로 위험을 더 "
                        "늘려야 한다는 뜻은 아닙니다.", icon="⚖️")
            else:
                st.success("현재 포트폴리오 변동성이 성향 모형 참고점과 비슷한 수준입니다.", icon="⚖️")

    # 유명 투자자 전략 비교 표 (내 포트폴리오와 나란히)
    if strat_rows:
        me = {"전략": "★ 내 포트폴리오", "sigma": port["sigma"], "er": port["er"],
              "sharpe": (port["er"] - rf) / port["sigma"] if port["sigma"] > 0 else None, "note": ""}
        tv = pd.DataFrame([me] + strat_rows)
        st.dataframe(pd.DataFrame({
            "": tv["전략"],
            "기대수익(연)": tv["er"].map(lambda v: f"{v * 100:+.1f}%"),
            "변동성 σ(연)": tv["sigma"].map(lambda v: f"{v * 100:.1f}%"),
            "샤프": tv["sharpe"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—"),
            "설명": tv["note"],
        }), hide_index=True, use_container_width=True)
        st.caption("전략 원형은 대표 자산배분을 우리 자산군 프록시로 근사한 **예시**입니다"
                   "(실제 보유종목·수익률 아님). 세모(▲)가 각 전략, 별(★)이 내 포트폴리오입니다.")

    # 성향테스트 기반 추천 배분 (내 성향이 있으면)
    if prof:
        from src.analysis.risk_profile import LEVELS
        level = LEVELS[prof["level"] - 1]
        alloc_range = dict(level.allocation_range)
        y = prof.get("y_star")
        with st.container(border=True):
            st.markdown(f"##### 내 위험 프로파일({prof['label']})의 교육용 배분 범위")
            ac = st.columns(len(alloc_range) + 1)
            for i, (k, bounds) in enumerate(alloc_range.items()):
                ac[i].metric(k, f"{bounds[0]}–{bounds[1]}%")
            if isinstance(y, (int, float)):
                ac[-1].metric("모형상 y*", f"{y * 100:.0f}%",
                              help="비제약 평균-분산 모형의 가정 기반 참고치이며 권장 비중이나 상한이 아닙니다.")
            st.caption("범위와 y*는 교육용 참고 자료입니다. 현금흐름·세금·집중위험을 반영한 권장안이 아닙니다.")

    # ── 성과지표 ──
    st.divider()
    st.markdown("### 🏁 성과지표 — 벤치마크 대비 (과거 실측)")
    md = MARKET_DEFAULTS[bench_market]
    bench_px = _fetch_px(md["symbol"], "10y")
    bench_m = None
    if bench_px is not None:
        b = bench_px if bench_market == "KR" else bench_px * fx.reindex(bench_px.index).ffill()
        bench_m = b.resample("ME").last().pct_change(fill_method=None).dropna()
    if bench_m is not None:
        port_m = portfolio_series(weights, monthly)
        perf = performance(port_m, bench_m, rf)
        if perf["sharpe"] is not None:
            r = st.columns(5)
            r[0].metric("샤프비율", f"{perf['sharpe']:.2f}",
                        help="(Rp−Rf)/σp — 총위험 1단위당 초과수익. 분산 안 된 포트폴리오까지 공정 비교.")
            r[1].metric("베타 β", f"{perf['beta']:.2f}",
                        help=f"{md['label']} 대비 민감도(원화 환산 월간 회귀)")
            r[2].metric("트레이너비율", f"{perf['treynor'] * 100:.1f}%" if perf["treynor"] else "—",
                        help="(Rp−Rf)/β — 체계적 위험 1단위당 초과수익. 잘 분산된 포트폴리오에 적합.")
            r[3].metric("젠센 알파", f"{perf['jensen'] * 100:+.1f}%p" if perf["jensen"] is not None else "—",
                        help="CAPM 기대 대비 초과성과 — Rp − [Rf + β(Rm−Rf)]")
            r[4].metric("M²", f"{perf['m2'] * 100:.1f}%" if perf["m2"] is not None else "—",
                        help="내 샤프비율로 벤치마크 σ만큼 위험을 졌다면 얻었을 수익률 — 수익률 단위라 직관적")
            st.caption(f"표본 {perf['n']}개월 · 포트폴리오 실측 {perf['er_p']*100:+.1f}%/σ {perf['sigma_p']*100:.1f}% · "
                       f"{md['label']}(원화 환산) {perf['er_b']*100:+.1f}%/σ {perf['sigma_b']*100:.1f}% · R_f {rf*100:.1f}%")
        else:
            st.info("겹치는 표본이 12개월 미만이라 성과지표를 계산하지 않았습니다.")
    else:
        st.info("벤치마크 시세를 가져오지 못했습니다.")

    # ── 세금 참고 ──
    st.divider()
    st.markdown("### 🧾 세금 참고 — 분석은 세전, 세후는 어림값")
    rows, taxed_mu = [], 0.0
    for k in monthly.columns:
        a = basket.get(k, {"type": "국내ETF", "name": name_of[k]})
        tr = after_tax_row(a["type"], float(mu_used[k]))
        taxed_mu += weights[k] * tr["mu_after"]
        rows.append({"자산": name_of[k], "과세 방식": tr["rule"],
                     "세전 기대수익": f"{mu_used[k] * 100:+.1f}%",
                     "예상 실효세율": f"{tr['eff_rate'] * 100:.1f}%",
                     "세후 기대수익": f"{tr['mu_after'] * 100:+.1f}%"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    t1, t2 = st.columns(2)
    t1.metric("포트폴리오 세전 기대수익", f"{port['er'] * 100:+.1f}%(연)")
    t2.metric("포트폴리오 세후 기대수익(어림)", f"{taxed_mu * 100:+.1f}%(연)")
    st.caption("⚠️ 어림 규칙: 2026년 개인 기준(금투세 폐지 반영). 해외주식 연 250만원 양도 "
               "기본공제·거래 시점·손익통산은 반영하지 않았습니다. 인컴(배당·분배·이자) 비중은 "
               "유형별 기본 가정치를 씁니다. **금융소득(이자+배당) 연 2천만원 초과 시 종합과세** "
               "대상이 될 수 있습니다 — 정확한 세액은 세무 전문가와 확인하세요.")

    st.divider()
    st.caption("ⓘ 학습·분석 보조 도구입니다. 기대수익·σ는 과거 실측 기반 추정으로 미래를 보장하지 "
               "않으며, 특정 상품 매수·매도 추천이 아닙니다.")
