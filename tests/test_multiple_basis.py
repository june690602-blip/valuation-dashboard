"""화면의 배수가 업종 중앙값과 같은 자를 쓰는가 (#86 · ADR-0020).

한 회사의 같은 배수가 두 출처로 존재했다 — 우리가 계산한 값(indicators)과 제공자
공시값(피어 프레임)이다. 밸류에이션 탭이 그 둘을 나란히 놓고 비교해서, 실측 5종목 중
2종목의 '싸다/비싸다'가 정의 차이만으로 뒤집혔다.

여기서는 네트워크 없이 **선택 규칙**만 검증한다. 실제 종목의 값 대조는
`scripts/check_multiple_definition.py`가 한다(수동 계열).
"""
import unittest

import numpy as np
import pandas as pd

from src.analysis.scoring import (BASIS_DISCLOSED, BASIS_OWN, peer_median,
                                  screen_multiple, self_multiple)


def frame(self_per, peer_pers, extra=None):
    rows = [{"per": self_per, "is_self": True}]
    rows += [{"per": p, "is_self": False} for p in peer_pers]
    df = pd.DataFrame(rows)
    if extra:
        for k, v in extra.items():
            df[k] = v
    return df


class SelfMultipleTest(unittest.TestCase):
    def test_자사_행에서_꺼낸다(self):
        self.assertAlmostEqual(self_multiple(frame(16.73, [10, 11, 12]), "per"), 16.73)

    def test_자사_값이_결측이면_None(self):
        self.assertIsNone(self_multiple(frame(np.nan, [10, 11, 12]), "per"))

    def test_열이_없으면_None(self):
        self.assertIsNone(self_multiple(frame(16.73, [10, 11]), "p_fcf"))

    def test_자사_행이_없으면_None(self):
        df = pd.DataFrame([{"per": 10.0, "is_self": False}])
        self.assertIsNone(self_multiple(df, "per"))


class ScreenMultipleTest(unittest.TestCase):
    def test_공시값이_있으면_공시값을_쓴다(self):
        """자체계산값이 함께 있어도 공시값이 이긴다 — 중앙값과 같은 자여야 하기 때문."""
        v, basis = screen_multiple(frame(16.73, [10, 11, 12]), "per", own_calc=14.52)
        self.assertAlmostEqual(v, 16.73)
        self.assertEqual(basis, BASIS_DISCLOSED)

    def test_공시값이_없으면_자체계산으로_내려간다(self):
        v, basis = screen_multiple(frame(np.nan, [10, 11, 12]), "per", own_calc=14.52)
        self.assertAlmostEqual(v, 14.52)
        self.assertEqual(basis, BASIS_OWN)

    def test_둘_다_없으면_값도_근거도_없다(self):
        self.assertEqual(screen_multiple(frame(np.nan, [10, 11]), "per", None), (None, None))

    def test_비교의_양변이_같은_프레임에서_나온다(self):
        """이 테스트가 이 파일의 이유다 — cur와 med가 같은 출처인지만 본다."""
        df = frame(16.73, [10.0, 11.0, 12.0])
        cur, basis = screen_multiple(df, "per", own_calc=14.52)
        med = peer_median(df, "per")
        self.assertEqual(basis, BASIS_DISCLOSED)
        self.assertAlmostEqual(med, 11.0)
        # 자사를 뺀 피어 중앙값 11.0과 자사 공시값 16.73 — 둘 다 피어 프레임의 값이다.
        self.assertAlmostEqual(cur, self_multiple(df, "per"))

    def test_자체계산_폴백이면_판정을_만들지_않는다(self):
        """부르는 쪽 규약 — basis가 공시가 아니면 vs/cheaper를 내지 않는다.

        자체계산 14.52는 중앙값 11.0보다 '높음'이지만, 공시값 기준으로는 그렇지 않을 수
        있다. 실제로 그 차이만으로 판정이 뒤집힌 종목이 있었다.
        """
        df = frame(np.nan, [10.0, 11.0, 12.0])
        cur, basis = screen_multiple(df, "per", own_calc=14.52)
        self.assertEqual(basis, BASIS_OWN)
        self.assertNotEqual(basis, BASIS_DISCLOSED)   # 이 조건으로 호출부가 비교를 막는다


if __name__ == "__main__":
    unittest.main()
