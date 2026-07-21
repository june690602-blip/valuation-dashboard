"""홈 — 깔끔한 랜딩(3영역 카드) + 투자성향 테스트(문항 위저드 → CML 접점 결과).

성향테스트 결과(위험회피계수 A·유형)는 st.session_state["risk_profile"]에 저장되어
포트폴리오 페이지에서 재사용된다.
"""
from __future__ import annotations

import numpy as np
import streamlit as st

from src.analysis.risk_profile import (DIMENSIONS, PROFILE_SCHEMA_VERSION, QUESTIONS,
                                       grade, profile_to_dict, tangency_point)
from src.ui import charts

PLOTLY_CFG = charts.PLOTLY_CFG  # CML 접점은 정적 평면(2D 떠다님 방지)

# 시장 파라미터 기본값 — E(Rm)=Rf+MRP(전방 시각, 주식 페이지 가정과 일관), σ는 지수 실측 폴백
MARKET_DEFAULTS = {
    "KR": {"label": "KOSPI200", "symbol": "^KS200", "rf": 0.035, "mrp": 0.060, "sigma": 0.17},
    "US": {"label": "S&P 500", "symbol": "^GSPC", "rf": 0.045, "mrp": 0.050, "sigma": 0.15},
}


@st.cache_data(ttl=3600, show_spinner=False)
def _market_riskfree(market: str) -> tuple[float, str]:
    """무위험이자율 R_f 기본값 — 채권탭 10년물 국채 금리(라이브), 실패 시 정적 기본값.

    주식 WACC·성향테스트 CML·포트폴리오 CML이 이 한 값을 공유한다(탭 유기적 연결).
    """
    from src.data.bonds import current_riskfree
    rate, label = current_riskfree(market)
    if rate is not None and 0.0 < rate < 0.15:
        return rate, label
    return MARKET_DEFAULTS[market]["rf"], "기본 가정치"


@st.cache_data(ttl=86400, show_spinner=False)
def _market_sigma(symbol: str, fallback: float) -> tuple[float, str]:
    """지수 10년 주간 수익률로 연 변동성 추정(√52 연율화). 실패 시 기본값.

    월간(표본 120개)은 극단월 몇 개에 추정치가 널뛰어 주간을 쓴다 — 베타 회귀(주간)와도 일관.
    마지막 주는 진행 중(부분 구간)이라 제외.
    """
    try:
        from src.data.base import fetch_index_prices
        px = fetch_index_prices(symbol, "10y")
        weekly = px.resample("W-FRI").last().pct_change(fill_method=None).dropna().iloc[:-1]
        if len(weekly) >= 100:
            return float(weekly.std() * np.sqrt(52)), f"주간 {len(weekly)}표본 실측"
    except Exception:
        pass
    return fallback, "기본 가정치"


def _go(page_key: str):
    from src.ui.nav import PAGES
    st.switch_page(PAGES[page_key])


# ── 랜딩 ────────────────────────────────────────────────────────────
def _render_banner():
    """투자성향 테스트 배너 — 완료 전엔 테스트 유도, 완료 후엔 내 유형 요약."""
    prof = st.session_state.get("risk_profile")
    with st.container(border=True):
        c1, c2 = st.columns([5, 2], vertical_alignment="center")
        if prof:
            c1.markdown(
                f"### {prof.get('symbol', prof.get('emoji', ''))} 나의 위험 프로파일: "
                f"**{prof['label']}** — {prof.get('archetype', prof.get('nickname', ''))}\n"
                f"손실 감당 여력과 변동성 수용도를 나누어 본 비공식 자가진단입니다. "
                f"이론 모형의 A 추정치는 **{prof.get('assessed_A', prof.get('A', 0)):.1f}**입니다.")
            if c2.button("결과 다시 보기 / 재검사", use_container_width=True):
                st.session_state["home_view"] = "test"
                st.rerun()
        else:
            c1.markdown(
                "### 내 투자 위험 프로파일 확인하기\n"
                "8개 질문으로 **손실 감당 여력·변동성 수용도·경험·목표**를 나누어 살펴봅니다. "
                "약 2분이 걸리며 정답은 없습니다.")
            if c2.button("테스트 시작", type="primary", use_container_width=True):
                st.session_state["home_view"] = "test"
                st.rerun()


def _render_cards():
    st.markdown("#### 세 가지 도구")
    cols = st.columns(3)
    cards = [
        ("📈 주식 가치평가", "stock",
         "재무·주가·업종 데이터로 적정주가를 4가지 방법으로 삼각측량하고, "
         "업종 비교·자본비용(WACC)·백테스트·AI 평가까지 9개 탭으로 분석합니다."),
        ("🏦 채권", "bond",
         "한국 국고채·미국 국채 수익률곡선을 보고, 금리가 움직일 때 채권 가격이 얼마나 "
         "변하는지 듀레이션·볼록성으로 시나리오 분석합니다."),
        ("🧺 포트폴리오", "portfolio",
         "분석한 주식·채권과 예금·금·리츠를 담아 위험(σ)-기대수익 평면에서 내 포트폴리오의 "
         "위치와 성과지표(샤프·트레이너)를 확인합니다."),
    ]
    for col, (title, key, desc) in zip(cols, cards):
        with col, st.container(border=True):
            st.markdown(f"##### {title}")
            st.caption(desc)
            if st.button("이동 →", key=f"card_{key}", use_container_width=True):
                _go(key)


# ── 성향테스트: 위저드 ──────────────────────────────────────────────
def _reset_test():
    for key in list(st.session_state):
        if key.startswith(("q_", "rt_")):
            st.session_state.pop(key, None)
    st.session_state["test_step"] = 0
    st.session_state["test_answers"] = {}
    st.session_state.pop("risk_profile", None)


def _render_test():
    if st.button("← 홈으로"):
        st.session_state["home_view"] = "main"
        st.rerun()

    if "test_step" not in st.session_state:
        saved = st.session_state.get("risk_profile") or {}
        saved_answers = saved.get("answers")
        if (isinstance(saved_answers, list) and len(saved_answers) == len(QUESTIONS)
                and all(isinstance(answer, int) and not isinstance(answer, bool)
                        and 0 <= answer < len(QUESTIONS[index].options)
                        for index, answer in enumerate(saved_answers))):
            st.session_state["test_step"] = len(QUESTIONS)
            st.session_state["test_answers"] = dict(enumerate(saved_answers))
        else:
            _reset_test()
    step = st.session_state["test_step"]
    answers: dict = st.session_state["test_answers"]
    n = len(QUESTIONS)

    # 모든 문항을 답하고 '결과 보기'를 누르면 step == n
    if step >= n:
        _render_result([answers[i] for i in range(n)])
        return

    st.subheader("투자 위험 프로파일")
    st.caption("좋아 보이는 답보다, 하락장에서 실제로 지킬 수 있는 답을 골라주세요.")
    st.progress((step + 1) / n, text=f"{QUESTIONS[step].chapter} · 질문 {step + 1} / {n}")

    q = QUESTIONS[step]
    labels = [opt[0] for opt in q.options]
    prev = answers.get(step)
    choice = st.radio(f"**Q{step + 1}. {q.text}**", labels,
                      index=prev if prev is not None else None,
                      key=f"q_{step}")
    if q.guide:
        st.caption(q.guide)

    c1, c2, _sp = st.columns([1, 1, 3])
    if step > 0 and c1.button("← 이전", use_container_width=True):
        st.session_state["test_step"] = step - 1
        st.rerun()
    is_last = step == n - 1
    if c2.button("결과 확인" if is_last else "다음 →", type="primary",
                 use_container_width=True, disabled=choice is None):
        answers[step] = labels.index(choice)
        st.session_state["test_step"] = step + 1
        st.rerun()


# ── 성향테스트: 결과 ────────────────────────────────────────────────
def _render_result(answer_indices: list[int]):
    prof = grade(answer_indices)
    st.markdown(f"## {prof.symbol} **{prof.label}** · {prof.archetype}")
    st.caption(f"자가진단 {prof.score}/100 · 참고 분류: {prof.official_label} · 응답 흐름: {prof.consistency}")
    st.markdown(f"**{prof.summary}**")
    st.write(prof.description)
    if prof.guardrail_note:
        st.info(f"**감내 여력 우선 원칙** — {prof.guardrail_note}.")

    st.markdown("### 판정 근거 — 네 가지 축")
    dcols = st.columns(4)
    for col, (key, meta) in zip(dcols, DIMENSIONS.items()):
        score = prof.dimension_scores[key]
        col.metric(meta["label"], f"{score}/100")
        col.progress(score / 100)
        col.caption(meta["short"])
    for note in prof.behavioral_notes:
        st.warning(note)

    st.markdown("### 실행 원칙")
    pcol, wcol = st.columns(2)
    with pcol:
        st.markdown("**잘 맞는 운용 원칙**")
        for item in prof.principles:
            st.markdown(f"- {item}")
    with wcol:
        st.markdown("**주의해서 볼 행동**")
        for item in prof.watchouts:
            st.markdown(f"- {item}")

    st.markdown("### 교육용 배분 범위")
    acols = st.columns(len(prof.allocation_range))
    for col, (name, bounds) in zip(acols, prof.allocation_range.items()):
        col.metric(name, f"{bounds[0]}–{bounds[1]}%")
    st.caption("각 범위는 예시이며 동시에 최댓값을 선택하라는 뜻이 아닙니다. 소득·부채·세금·사용 시점을 "
               "반영해 합계 100% 안에서 조정해야 합니다.")

    with st.expander("이론 실험실 · 자본시장선(CML) 가정 바꿔보기", expanded=False):
        st.caption("자가진단의 A는 교육용 추정치입니다. 아래 슬라이더는 시나리오만 바꾸며 저장된 검사 결과나 "
                   "포트폴리오 개인화에는 반영되지 않습니다.")
        mcol, acol = st.columns([1, 2])
        market = mcol.radio("기준 시장", ["KR", "US"], horizontal=True,
                            format_func=lambda m: "KOSPI200" if m == "KR" else "S&P 500",
                            key="rt_market")
        scenario_A = acol.slider("시나리오 위험회피계수 A", 1.0, 10.0, float(prof.A), 0.1,
                                 key="rt_scenario_A",
                                 help=f"자가진단 추정치는 A≈{prof.A:.1f}입니다. A가 클수록 모형상 위험자산 비중이 줄어듭니다.")

        md = MARKET_DEFAULTS[market]
        rf_live, rf_src = _market_riskfree(market)
        st.markdown(f"**시장 가정 조정** · R_f 기본값: {rf_src} {rf_live * 100:.2f}%")
        rf = st.slider("무위험이자율 R_f (%)", 0.5, 8.0, round(rf_live * 100, 1), 0.1,
                       key=f"rt_rf_{market}") / 100
        mrp = st.slider("시장위험프리미엄 MRP (%)", 3.0, 10.0, md["mrp"] * 100, 0.5,
                        key=f"rt_mrp_{market}") / 100
        sigma_est, sigma_src = _market_sigma(md["symbol"], md["sigma"])
        sigma_m = st.slider(f"시장 변동성 σm (%) — {sigma_src}", 8.0, 35.0,
                            round(sigma_est * 100, 1), 0.5, key=f"rt_sig_{market}") / 100
        er_m = rf + mrp
        scenario = tangency_point(er_m, rf, sigma_m, scenario_A)
        assessed = tangency_point(er_m, rf, sigma_m, prof.A)

        r = st.columns(4)
        r[0].metric("모형상 위험자산 비중 y*", f"{scenario['y_star'] * 100:.0f}%")
        r[1].metric("가정상 안전자산 비중",
                    f"{max(1 - scenario['y_star'], 0) * 100:.0f}%" if scenario["y_star"] <= 1 else "차입 구간")
        r[2].metric("가정상 기대수익", f"{scenario['er_p'] * 100:.1f}%")
        r[3].metric("가정상 변동성", f"{scenario['sigma_p'] * 100:.1f}%")
        if scenario["y_star"] > 1:
            st.warning("차입을 가정하는 비제약 모형 결과입니다. 실제 배분이나 권장 비중으로 해석하지 마세요.")
        st.plotly_chart(charts.cml_tangency_chart(rf, er_m, sigma_m, scenario_A, md["label"]),
                        use_container_width=True, config=PLOTLY_CFG)
        st.caption(f"접점 검산: MRS {scenario['mrs']:.3f} ≈ 샤프비율 {scenario['sharpe']:.3f}. "
                   "개인의 현금흐름·세금·집중위험은 반영하지 않은 모형입니다.")

    payload = profile_to_dict(prof)
    payload.update({
        "schema_version": PROFILE_SCHEMA_VERSION,
        "answers": list(answer_indices),
        "assessed_A": prof.A,
        "A": prof.A,
        "scenario_A": scenario_A,
        "y_star": assessed["y_star"],
        "market": market,
        "market_label": md["label"],
    })
    st.session_state["risk_profile"] = payload

    b1, b2 = st.columns(2)
    if b1.button("포트폴리오와 비교하기", type="primary", use_container_width=True):
        _go("portfolio")
    if b2.button("다시 검사하기", use_container_width=True):
        _reset_test()
        st.rerun()

    st.info("이 결과는 금융회사의 공식 투자자정보확인서가 아니며 특정 상품의 적합성·매매를 판단하지 "
            "않습니다. 소득, 부채, 비상자금, 세금, 투자기간이 달라지면 결과도 달라질 수 있습니다.")


# ── 페이지 엔트리 ───────────────────────────────────────────────────
def render():
    with st.sidebar:
        st.caption("📊 투자지표 — 기본적 분석 기반 학습·분석 보조 도구")

    if st.session_state.get("home_view") == "test":
        _render_test()
        return

    st.title("📊 투자지표")
    st.markdown("**\"지금 이 가격이 적정한가?\"** — 주식·채권·포트폴리오를 데이터로 검증하는 "
                "기본적 분석 대시보드입니다.")
    st.caption("ⓘ 본 도구는 학습·분석 보조용이며 특정 상품의 매수·매도 추천이 아닙니다.")

    _render_banner()
    _render_cards()

    from src.ui.components import guide_link_html
    gc1, gc2 = st.columns([1, 1])
    with gc1:
        st.markdown(guide_link_html("📖 처음이신가요? 사용설명서 (새 탭으로 열기)", block=True),
                    unsafe_allow_html=True)
    gc2.caption("설명서를 새 탭에 띄워 두고 앱을 함께 보면 편해요.")

    st.divider()
    st.caption("데이터: Yahoo Finance · 네이버금융 · KRX · OpenDART · FRED — 무료 공개 데이터 특성상 "
               "실제 공시·시세와 다를 수 있습니다.")
