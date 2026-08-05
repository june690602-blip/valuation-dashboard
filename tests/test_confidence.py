"""신뢰도 산식 — 유효 축 수 보정 (ADR-0021) 순수 함수 테스트.

여기서 지키는 성질은 넷이다.

1. **상관을 안 재 본 시장은 보정하지 않는다** — 한국 값을 대신 쓰지 않는다(ADR-0017).
2. **독립이면 현행과 같다** — 상관이 0이면 배율이 1이라 아무것도 안 바뀐다.
3. **유효 축이 하나면 낮음** — 겉으로 두셋이어도 같은 재료를 읽었으면 한 축이다.
4. **신뢰도를 절대 올리지 않는다** — 실측 교차표가 하삼각이었고, 그 성질을 못박는다.
"""
from __future__ import annotations

import math
import unittest

from src.analysis.valuation import (AXIS_CORR, METHOD_WEIGHTS,  # noqa: F401
                                    MIN_EFFECTIVE_AXES, effective_axes)

REL, BAND, RIM, NORM = ("업종 상대가치", "역사적 밴드",
                        "수익가치(RIM)", "정규화 이익")


class EffectiveAxesTests(unittest.TestCase):
    def test_unmeasured_market_returns_none(self):
        # 상관표에 없는 시장은 보정하지 않는다 — 값을 지어내느니 현행 산식으로 간다
        self.assertIsNone(effective_axes({REL: 0.5, BAND: 0.5}, "JP"))
        self.assertIsNone(effective_axes({REL: 0.5, BAND: 0.5}, None))
        self.assertIsNone(effective_axes({REL: 0.5, BAND: 0.5}, ""))

    def test_market_is_case_insensitive(self):
        self.assertIsNotNone(effective_axes({REL: 0.5, BAND: 0.5}, "kr"))

    def test_single_axis_is_one(self):
        self.assertAlmostEqual(effective_axes({REL: 1.0}, "KR"), 1.0, places=6)

    def test_two_independent_axes_are_two(self):
        # ①②는 실측 상관이 −0.009라 사실상 독립이다 — 유효 축이 2에 가까워야 한다
        n = effective_axes({REL: 0.5, BAND: 0.5}, "KR")
        self.assertAlmostEqual(n, 2.0, delta=0.05)

    def test_correlated_axes_collapse(self):
        # ③⑤는 실측 상관이 +0.788이다. 둘을 세면 2가 아니라 1.1 근처여야 한다
        n = effective_axes({RIM: 0.5, NORM: 0.5}, "KR")
        self.assertLess(n, 1.3)
        self.assertGreater(n, 1.0)

    def test_three_correlated_axes_are_fewer_than_two_independent(self):
        # 이 한 줄이 이 ADR의 요지다 — 독립성이 개수를 이긴다.
        three = effective_axes({REL: 1 / 3, RIM: 1 / 3, NORM: 1 / 3}, "KR")
        two = effective_axes({REL: 0.5, BAND: 0.5}, "KR")
        self.assertLess(three, two)
        self.assertLess(three, MIN_EFFECTIVE_AXES)   # 겉으로 셋인데 '사실상 한 축'
        self.assertGreater(two, MIN_EFFECTIVE_AXES)

    def test_all_four_axes_stay_near_two(self):
        # 네 축을 다 써도 유효 축은 2 근처다. "삼각측량"이 실은 이중측량이라는 실측.
        w = {m: METHOD_WEIGHTS[m] for m in (REL, BAND, RIM, NORM)}
        s = sum(w.values())
        w = {k: v / s for k, v in w.items()}
        for market in ("KR", "US"):
            with self.subTest(market=market):
                n = effective_axes(w, market)
                self.assertGreater(n, MIN_EFFECTIVE_AXES)
                self.assertLess(n, 2.5)

    def test_identity_matrix_recovers_plain_count(self):
        # 상관이 전부 0이면 동일가중 n개에서 정확히 n이 나와야 한다(정의 확인).
        import src.analysis.valuation as V
        saved = V.AXIS_CORR["KR"]
        V.AXIS_CORR["KR"] = {k: 0.0 for k in saved}
        try:
            n = V.effective_axes({REL: 0.25, BAND: 0.25, RIM: 0.25, NORM: 0.25}, "KR")
            self.assertAlmostEqual(n, 4.0, places=6)
        finally:
            V.AXIS_CORR["KR"] = saved

    def test_every_pair_is_measured_in_every_market(self):
        # 쌍이 하나라도 비면 effective_axes가 통째로 None을 내 보정이 조용히 꺼진다.
        axes = [REL, BAND, RIM, NORM]
        for market, table in AXIS_CORR.items():
            for i, a in enumerate(axes):
                for b in axes[i + 1:]:
                    with self.subTest(market=market, pair=(a, b)):
                        self.assertIsNotNone(table.get((a, b), table.get((b, a))))


class AdjustmentTests(unittest.TestCase):
    """산포 보정의 성질 — `disp_adj = disp × √(n / N_eff)`."""

    @staticmethod
    def _adj(disp: float, weights: dict, market: str) -> float:
        """`compute_valuation`이 쓰는 것과 **같은 식**이어야 한다 — 1에서 자르는 것 포함."""
        n = effective_axes(weights, market)
        return disp * max(1.0, math.sqrt(len(weights) / n))

    def test_independent_axes_leave_dispersion_alone(self):
        # ①②는 거의 독립이라 보정이 거의 없어야 한다 — 상관이 있을 때만 움직인다
        self.assertAlmostEqual(self._adj(0.20, {REL: 0.5, BAND: 0.5}, "KR"),
                               0.20, delta=0.01)

    def test_correlated_axes_inflate_dispersion(self):
        # 같은 재료를 읽어 값이 가까운 것을 '확실하다'로 읽지 않는다
        self.assertGreater(self._adj(0.20, {RIM: 0.5, NORM: 0.5}, "KR"), 0.25)

    def test_adjustment_never_shrinks_dispersion(self):
        # 신뢰도를 **올리는** 방향으로는 절대 움직이지 않는다. 실측 상관 중 ①②·②⑤가
        # 음수라 자르지 않으면 N_eff가 n을 넘어 이 성질이 깨진다 — 잡음 수준의 음수
        # 상관을 "더 확실하다"의 근거로 쓰지 않겠다는 결정이 이 테스트로 고정된다.
        cases = [{REL: 0.5, BAND: 0.5}, {BAND: 0.5, NORM: 0.5},
                 {REL: 0.5, RIM: 0.5}, {RIM: 0.5, NORM: 0.5},
                 {REL: 1 / 3, BAND: 1 / 3, NORM: 1 / 3},
                 {REL: 0.25, BAND: 0.25, RIM: 0.25, NORM: 0.25}]
        for market in AXIS_CORR:
            for w in cases:
                with self.subTest(market=market, axes=tuple(w)):
                    self.assertGreaterEqual(self._adj(0.20, w, market), 0.20 - 1e-9)


if __name__ == "__main__":
    unittest.main()
