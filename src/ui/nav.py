"""페이지 등록(싱글턴) — st.switch_page가 동일 st.Page 인스턴스를 참조하도록 한 곳에서 만든다.

페이지 모듈은 순환 임포트를 피하기 위해 nav를 함수 안에서 지연 임포트한다.
"""
from __future__ import annotations

import streamlit as st

from src.ui.pages import bond, home, portfolio, stock

PAGES = {
    "home": st.Page(home.render, title="홈", icon="🏠", url_path="home", default=True),
    "stock": st.Page(stock.render, title="주식 가치평가", icon="📈", url_path="stock"),
    "bond": st.Page(bond.render, title="채권 금리·시나리오", icon="🏦", url_path="bond"),
    "portfolio": st.Page(portfolio.render, title="포트폴리오", icon="🧺", url_path="portfolio"),
}
