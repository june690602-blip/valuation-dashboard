"""홈 — 깔끔한 랜딩(3영역 카드) + 투자성향 테스트(문항 위저드 → CML 접점 결과).

성향테스트 결과(위험회피계수 A·유형)는 st.session_state["risk_profile"]에 저장되어
포트폴리오 페이지에서 재사용된다.
"""
from __future__ import annotations

import numpy as np
import streamlit as st

from src.analysis.risk_profile import QUESTIONS, grade, tangency_point
from src.ui import charts

PLOTLY_CFG = charts.PLOTLY_CFG_ZOOM  # CML 접점 차트: 휠·핀치 줌

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
                f"### {prof['emoji']} 나의 투자성향: **{prof['label']}** — {prof['nickname']}\n"
                f"위험회피계수 A ≈ **{prof['A']:.1f}** · 권장 위험자산 비중 **{prof['y_star'] * 100:.0f}%** "
                f"({prof['market_label']} 기준) — 결과 화면에서 CML 접점을 다시 볼 수 있어요.")
            if c2.button("결과 다시 보기 / 재검사", use_container_width=True):
                st.session_state["home_view"] = "test"
                st.rerun()
        else:
            c1.markdown(
                "### 🧭 나는 어떤 투자자일까? — 투자성향 테스트\n"
                "8개 질문(심리 질문 포함)으로 **위험회피계수**를 측정하고, 자본시장선(CML) 위에서 "
                "**나의 최적 지점**을 찾아드립니다. 약 2분.")
            if c2.button("테스트 시작", type="primary", use_container_width=True):
                st.session_state["home_view"] = "test"
                st.rerun()


def _render_cards():
    st.markdown("#### 세 가지 도구")
    cols = st.columns(3)
    cards = [
        ("📈 주식 가치평가", "stock",
         "재무·주가·업종 데이터로 적정주가를 3가지 방법으로 삼각측량하고, "
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
    st.session_state["test_step"] = 0
    st.session_state["test_answers"] = {}


def _render_test():
    if st.button("← 홈으로"):
        st.session_state["home_view"] = "main"
        st.rerun()

    if "test_step" not in st.session_state:
        _reset_test()
    step = st.session_state["test_step"]
    answers: dict = st.session_state["test_answers"]
    n = len(QUESTIONS)

    # 모든 문항을 답하고 '결과 보기'를 누르면 step == n
    if step >= n:
        _render_result([answers[i] for i in range(n)])
        return

    st.subheader("🧭 투자성향 테스트")
    st.progress(step / n, text=f"질문 {step + 1} / {n}")

    q = QUESTIONS[step]
    labels = [opt[0] for opt in q.options]
    prev = answers.get(step)
    choice = st.radio(f"**Q{step + 1}. {q.text}**", labels,
                      index=prev if prev is not None else None,
                      key=f"q_{step}")

    c1, c2, _sp = st.columns([1, 1, 3])
    if step > 0 and c1.button("← 이전", use_container_width=True):
        st.session_state["test_step"] = step - 1
        st.rerun()
    is_last = step == n - 1
    if c2.button("결과 보기 🎉" if is_last else "다음 →", type="primary",
                 use_container_width=True, disabled=choice is None):
        answers[step] = labels.index(choice)
        st.session_state["test_step"] = step + 1
        st.rerun()


# ── 성향테스트: 결과 ────────────────────────────────────────────────
def _render_result(answer_indices: list[int]):
    prof = grade(answer_indices)

    st.markdown(f"## {prof.emoji} 당신은 **{prof.label}** — \"{prof.nickname}\"")
    st.caption(f"총점 {prof.score}점 ({len(QUESTIONS)}문항) · 5단계 중 {prof.level}단계 · "
               "분류 체계: 표준투자권유준칙 5단계")
    st.markdown(prof.description)
    for note in prof.behavioral_notes:
        st.warning(note, icon="🧠")

    st.divider()
    st.markdown("### 📐 자본시장선(CML) 위 나의 위치")
    st.markdown(
        "위험회피계수 **A**가 정해지면, 무위험자산과 시장포트폴리오(M)를 잇는 **CML** 위에서 "
        "나의 효용을 최대화하는 지점이 하나 정해집니다 — **나의 무차별곡선이 CML에 접하는 곳**입니다. "
        "접점에서는 무차별곡선의 기울기(MRS = A·σ\\*)가 CML의 기울기(**샤프비율**)와 같아집니다.")

    mcol, acol = st.columns([1, 2])
    market = mcol.radio("기준 시장", ["KR", "US"], horizontal=True,
                        format_func=lambda m: "🇰🇷 KOSPI200" if m == "KR" else "🇺🇸 S&P 500",
                        key="rt_market")
    A = acol.slider("위험회피계수 A — 움직여 보세요, 접점이 미끄러집니다", 1.0, 10.0,
                    float(prof.A), 0.1, key="rt_A",
                    help="테스트 결과가 기본값입니다. A가 클수록(위험회피↑) 접점이 무위험자산 쪽으로 이동합니다.")

    md = MARKET_DEFAULTS[market]
    rf_live, rf_src = _market_riskfree(market)
    with st.expander(f"가정 조정 (R_f · MRP · σm) — R_f 기본값: {rf_src} {rf_live * 100:.2f}%",
                     expanded=False):
        rf = st.slider("무위험이자율 R_f (%)", 0.5, 8.0, round(rf_live * 100, 1), 0.1,
                       key=f"rt_rf_{market}", help="채권탭의 10년물 국채 금리를 기본값으로 씁니다.") / 100
        mrp = st.slider("시장위험프리미엄 MRP (%)", 3.0, 10.0, md["mrp"] * 100, 0.5, key=f"rt_mrp_{market}") / 100
        sigma_est, sigma_src = _market_sigma(md["symbol"], md["sigma"])
        sigma_m = st.slider(f"시장 변동성 σm (%) — {sigma_src}", 8.0, 35.0,
                            round(sigma_est * 100, 1), 0.5, key=f"rt_sig_{market}") / 100
    er_m = rf + mrp

    t = tangency_point(er_m, rf, sigma_m, A)
    y = t["y_star"]

    r = st.columns(4)
    r[0].metric("최적 위험자산 비중 y*", f"{y * 100:.0f}%",
                help="y* = (E(Rm) − R_f) / (A·σm²)  [머튼 비율]")
    r[1].metric("무위험자산", f"{max(1 - y, 0) * 100:.0f}%" if y <= 1 else "차입 구간",
                help="1 − y*. y*가 100%를 넘으면 이론상 무위험이자율로 빌려 투자하는 구간입니다.")
    r[2].metric("나의 기대수익 E(Rp)", f"{t['er_p'] * 100:.1f}%")
    r[3].metric("나의 변동성 σp", f"{t['sigma_p'] * 100:.1f}%")

    st.plotly_chart(charts.cml_tangency_chart(rf, er_m, sigma_m, A, md["label"]),
                    use_container_width=True, config=PLOTLY_CFG)
    st.caption(f"접점 검산: MRS = A·σ\\* = {t['mrs']:.3f} ≈ 샤프비율 = (E(Rm)−R_f)/σm = {t['sharpe']:.3f} ✓  "
               f"— E(Rm)은 R_f+MRP(전방 가정)로, σm은 지수 10년 주간 수익률로 추정했습니다.")

    st.divider()
    st.markdown("### 🧺 유형별 참고 배분 예시")
    a1, a2 = st.columns([2, 3])
    with a1:
        for k, v in prof.allocation.items():
            st.markdown(f"- **{k}** {v}%")
        st.caption("교과서적 예시일 뿐, 정답이 아닙니다. 위 CML의 y*와 함께 참고만 하세요.")
    with a2:
        st.markdown(
            "**다음 단계로 이어가기**\n"
            "- 📈 *주식 가치평가*에서 후보 종목의 적정가를 확인하고\n"
            "- 🏦 *채권*에서 금리 시나리오를 본 뒤\n"
            "- 🧺 *포트폴리오*에 담으면, 이 성향(A)과 **내 실제 포트폴리오**를 같은 평면에서 비교해 드립니다.")
        b1, b2 = st.columns(2)
        if b1.button("🧺 포트폴리오로 이동", use_container_width=True):
            _go("portfolio")
        if b2.button("다시 검사하기", use_container_width=True):
            _reset_test()
            st.rerun()

    # 포트폴리오 페이지에서 재사용할 프로필 저장
    st.session_state["risk_profile"] = {
        "label": prof.label, "nickname": prof.nickname, "emoji": prof.emoji,
        "level": prof.level, "score": prof.score, "A": A,
        "y_star": y, "market": market, "market_label": md["label"],
    }

    st.info("본 테스트와 배분 수치는 평균-분산 이론에 기초한 **학습용 참고 자료**이며, "
            "투자권유·자문이 아닙니다. 실제 투자성향 평가는 금융회사의 공식 절차를 따르세요.", icon="ℹ️")


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
