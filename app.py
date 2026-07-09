"""투자지표 — 멀티페이지 엔트리(라우터).

실행: streamlit run app.py
페이지 본문: src/ui/pages/ (홈·주식·채권·포트폴리오), 분석 로직: src/analysis/ (순수 함수).
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="투자지표 — 가치평가 대시보드", page_icon="📊",
                   layout="wide", initial_sidebar_state="expanded")

# 우상단 기본 "Running" 상태 위젯(스포츠 픽토그램 애니메이션)을 숨긴다.
# 로딩 안내는 각 페이지의 st.spinner 메시지로만 깔끔하게 노출한다.
st.markdown(
    """
    <style>
    [data-testid="stStatusWidget"] { display: none !important; }
    /* 메트릭 숫자가 좁은 칸에서 줄바꿈/잘리지 않도록: 한 줄 유지 + 살짝 축소 */
    [data-testid="stMetricValue"] {
        white-space: nowrap;
        font-size: 1.55rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

from src.ui.nav import PAGES  # noqa: E402  (set_page_config 이후에 임포트해야 함)

st.navigation(list(PAGES.values())).run()
