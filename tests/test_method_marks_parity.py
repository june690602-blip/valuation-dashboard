"""방법 → 번호(①②③④⑤) 표가 두 언어에서 같은가.

**왜 필요한가.** 이 표는 지금 두 벌이다 — `valuation.py`의 `METHOD_MARKS`와
`stock.js`의 `METHOD_TAB`. 계산이 아니라 매핑이라 `check_client_math.py`(Node로 수식을
돌려 대조하는 관문)의 대상이 아니지만, **같은 개념이 두 곳에 사는 것은 똑같다.**

이 저장소에는 그 일이 실제로 벌어진 기록이 있다 — `peerMedian`이 브라우저에서는 자사를
포함하고 서버에서는 제외해 삼성전자에 12.77×와 11.66×가 함께 떴다. 그리고 방금
"①②③"이 화면 아홉 자리에 손으로 적힌 채 실제 판정 구성과 어긋나 있었다(PR #139·#140).

그래서 **파일에서 직접 읽어 대조한다.** JS를 실행하지 않고 정규식으로 뽑는 이유는 이
값이 IIFE 안의 `var`라 밖에서 부를 수 없고, 표가 리터럴이라 파싱으로 충분하기 때문이다.
표를 리터럴이 아닌 형태로 바꾸면 이 테스트가 먼저 깨진다 — 그때는 서버가 `mark`를
페이로드에 실어 보내고 이 표를 한 벌로 줄일 때다(`check_structure.py`의 등록 참조).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from src.analysis.valuation import METHOD_MARKS

ROOT = Path(__file__).resolve().parents[1]
STOCK_JS = ROOT / "web" / "assets" / "stock.js"


def _method_tab_from_js() -> dict[str, str]:
    """stock.js의 `METHOD_TAB` 리터럴에서 (방법 → 번호)만 뽑는다."""
    src = STOCK_JS.read_text(encoding="utf-8")
    m = re.search(r"var METHOD_TAB = \{(.*?)\};", src, re.S)
    if not m:
        raise AssertionError(
            "stock.js에서 METHOD_TAB 리터럴을 못 찾았다. 표의 모양이 바뀌었으면 "
            "이 테스트와 check_structure.py의 등록을 함께 고칠 것.")
    # '업종 상대가치': ['①', 'peers']  →  ('업종 상대가치', '①')
    pairs = re.findall(r"'([^']+)'\s*:\s*\[\s*'([^']+)'", m.group(1))
    if not pairs:
        raise AssertionError("METHOD_TAB은 찾았는데 항목을 못 읽었다 — 모양이 바뀌었다.")
    return dict(pairs)


class MethodMarksParityTests(unittest.TestCase):
    def test_python_and_js_agree_on_every_method_number(self):
        js = _method_tab_from_js()
        self.assertEqual(
            js, dict(METHOD_MARKS),
            "방법 번호 표가 파이썬과 JS에서 갈렸다 — 한쪽만 고친 것이다.\n"
            f"  파이썬: {dict(METHOD_MARKS)}\n"
            f"  JS    : {js}")

    def test_numbers_are_unique(self):
        """번호가 겹치면 화면이 다른 방법을 같은 번호로 부른다."""
        marks = list(METHOD_MARKS.values())
        self.assertEqual(len(marks), len(set(marks)), f"번호가 겹친다: {marks}")

    def test_every_verdict_method_has_a_number(self):
        """판정에 드는 방법에 번호가 없으면 화면 문장이 빈칸으로 선다."""
        from src.analysis.valuation import FUNDAMENTAL_METHODS

        missing = [m for m in FUNDAMENTAL_METHODS if m not in METHOD_MARKS]
        self.assertEqual(missing, [], f"번호가 없는 판정 축: {missing}")


if __name__ == "__main__":
    unittest.main()
