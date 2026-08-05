"""EBITDA가 영업이익보다 작게 오는 경우를 걸러내는가 (#116).

EBITDA는 영업이익에 감가상각비를 다시 더한 값이라 정의상 영업이익보다 작을 수 없다.
그런데 무료 데이터가 그런 값을 준다 — 인텔 2026Q2는 영업이익 +1,966M인데 EBITDA가
−7,274M이었고, 그 한 분기가 TTM을 끌어내려 EV/EBITDA가 144배로 튀었다(공시값 29.4배).

실제 종목 전수 측정은 `scripts/check_ebitda_sanity.py`가 한다(네트워크 필요).
여기서는 규칙만 네트워크 없이 지킨다.
"""
import unittest

import numpy as np

from src.data.base import _sane_ebitda


class SaneEbitdaTest(unittest.TestCase):
    def test_정상이면_그대로_둔다(self):
        self.assertEqual(_sane_ebitda(1000.0, 400.0, 600.0), (1000.0, None))

    def test_같으면_정상이다(self):
        """D&A가 0이면 EBITDA = 영업이익 — 어긋난 것이 아니다."""
        self.assertEqual(_sane_ebitda(400.0, 400.0, 0.0), (400.0, None))

    def test_영업이익보다_작으면_되돌린다(self):
        """인텔 실측 형태 — 영업이익 4,308 · EBITDA 3,676 · D&A 12,379."""
        v, act = _sane_ebitda(3676.0, 4308.0, 12379.0)
        self.assertEqual(act, "rebuilt")
        self.assertAlmostEqual(v, 4308.0 + 12379.0)

    def test_되돌릴_감가상각비가_없으면_버린다(self):
        """오염된 값보다 '없음'이 정직하다(ADR-0011)."""
        self.assertEqual(_sane_ebitda(3676.0, 4308.0, None), (None, "dropped"))
        self.assertEqual(_sane_ebitda(3676.0, 4308.0, np.nan), (None, "dropped"))

    def test_감가상각비가_음수로_오면_되돌리지_않고_버린다(self):
        """되돌린 값이 여전히 영업이익보다 작으면 고친 것이 아니다."""
        self.assertEqual(_sane_ebitda(100.0, 400.0, -50.0), (None, "dropped"))

    def test_영업이익이_없으면_판정하지_않는다(self):
        """비교할 기준이 없으면 손대지 않는다 — 없는 근거로 값을 버리지 않는다."""
        self.assertEqual(_sane_ebitda(3676.0, None, 12379.0), (3676.0, None))
        self.assertEqual(_sane_ebitda(3676.0, np.nan, 12379.0), (3676.0, None))

    def test_EBITDA가_없으면_그대로_없다(self):
        self.assertEqual(_sane_ebitda(None, 400.0, 600.0), (None, None))
        self.assertEqual(_sane_ebitda(np.nan, 400.0, 600.0), (None, None))

    def test_적자에서도_규칙은_같다(self):
        """영업이익이 음수여도 EBITDA는 그보다 크거나 같아야 한다(LG화학 실측 형태)."""
        v, act = _sane_ebitda(-769.0, -413.0, 900.0)
        self.assertEqual(act, "rebuilt")
        self.assertAlmostEqual(v, -413.0 + 900.0)


if __name__ == "__main__":
    unittest.main()
