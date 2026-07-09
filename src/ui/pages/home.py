"""홈 — 깔끔한 랜딩(3영역 카드) + 투자성향 테스트 진입.

성향테스트 결과(위험회피계수 A·유형)는 st.session_state["risk_profile"]에 저장되어
포트폴리오 페이지에서 재사용된다.
"""
from __future__ import annotations

import streamlit as st


def _go(page_key: str):
    from src.ui.nav import PAGES
    st.switch_page(PAGES[page_key])


def _render_banner():
    """투자성향 테스트 배너 — 완료 전엔 테스트 유도, 완료 후엔 내 유형 요약."""
    prof = st.session_state.get("risk_profile")
    with st.container(border=True):
        c1, c2 = st.columns([5, 2], vertical_alignment="center")
        if prof:
            c1.markdown(
                f"### {prof['emoji']} 나의 투자성향: **{prof['label']}**\n"
                f"위험회피계수 A ≈ **{prof['A']:.1f}** · 권장 위험자산 비중 **{prof['y_star'] * 100:.0f}%** — "
                "결과 화면에서 CML 접점을 다시 볼 수 있어요.")
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


def _render_test():
    """성향테스트 화면 (다음 단계에서 문항·결과 구현)."""
    if st.button("← 홈으로"):
        st.session_state["home_view"] = "main"
        st.rerun()
    st.subheader("🧭 투자성향 테스트")
    st.info("문항을 준비하고 있습니다 — 곧 제공됩니다.")


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

    st.divider()
    st.caption("데이터: Yahoo Finance · 네이버금융 · KRX · OpenDART · FRED — 무료 공개 데이터 특성상 "
               "실제 공시·시세와 다를 수 있습니다.")
