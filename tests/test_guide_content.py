"""사용설명서의 핵심 내용과 새 탭 동작이 두 UI에서 유지되는지 확인한다."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_GUIDE = ROOT / "web" / "guide.html"
STREAMLIT_GUIDE = ROOT / "src" / "ui" / "pages" / "guide.py"


class GuideContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static = STATIC_GUIDE.read_text(encoding="utf-8")
        cls.streamlit = STREAMLIT_GUIDE.read_text(encoding="utf-8")

    def test_no_placeholder_copy_remains(self) -> None:
        for text in (self.static, self.streamlit):
            self.assertNotIn("작성 예정", text)
            self.assertNotIn("지금은 뼈대", text)
            self.assertNotIn("웹 버전 준비 중", text)

    def test_core_guidance_exists_in_both_surfaces(self) -> None:
        phrases = (
            "5분 빠른 시작",
            "결론 → 근거 → 반대 근거",
            "데이터 정확도나 수익 가능성을 보증",
            "AI는 선택 기능",
            "결론을 내리기 전",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.static)
                self.assertIn(phrase, self.streamlit)

    def test_static_guide_has_accessible_landmarks(self) -> None:
        self.assertIn('class="skip-link"', self.static)
        self.assertIn('id="main-content"', self.static)
        self.assertIn('aria-current="page"', self.static)
        self.assertIn('aria-label="사용설명서 목차"', self.static)
        self.assertIn('<caption>', self.static)
        self.assertIn('scope="col"', self.static)

    def test_static_app_guide_links_open_safely_in_new_tab(self) -> None:
        pages = ("home.html", "stock.html", "bond.html", "portfolio.html", "test.html")
        link_pattern = re.compile(r'<a\b(?=[^>]*href="guide\.html")[^>]*>', re.IGNORECASE)
        for filename in pages:
            html = (ROOT / "web" / filename).read_text(encoding="utf-8")
            links = link_pattern.findall(html)
            self.assertTrue(links, f"{filename}에 사용설명서 링크가 없습니다.")
            for link in links:
                self.assertIn('target="_blank"', link, f"{filename}: {link}")
                self.assertIn('rel="noopener"', link, f"{filename}: {link}")

    def test_streamlit_tool_links_open_in_new_tab(self) -> None:
        self.assertIn('target="_blank"', self.streamlit)
        self.assertIn('rel="noopener"', self.streamlit)
        for route in ("stock", "bond", "portfolio", "home"):
            self.assertIn(f'"{route}"', self.streamlit)
            self.assertNotIn(f'"/{route}"', self.streamlit)

    def test_high_risk_explanations_match_current_scope(self) -> None:
        docs = (ROOT / "docs" / "사용설명서.md").read_text(encoding="utf-8")
        for text in (self.static, self.streamlit):
            self.assertNotIn("최대낙폭", text)
            self.assertNotIn("문장으로 정리할 때만", text)
            self.assertIn("역사적 밴드 신호 하나", text)
            self.assertIn("목표가·손절선", text)
        self.assertIn("순위상관**이 양수(+)", docs)

    def test_streamlit_guide_renders_without_errors(self) -> None:
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string("from src.ui.pages.guide import render\nrender()")
        app.run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.tabs), 4)
        self.assertGreaterEqual(len(app.expander), 10)


if __name__ == "__main__":
    unittest.main()
