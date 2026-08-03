"""후보를 뽑는 단계에서 규모를 본다 (ADR-0013).

창(1/5~5배)이 아니라 그 **위층**을 고친다. 편향된 표본추출 아래에 필터를 달면
정확해지는 것이 아니라 비어 버린다.

실측 기준선 — 피어 표의 자사 대비 시총 배율 중앙값:
    참엔지니어링 108.6배(창 안 1곳/9) · 삼성전자 0.0배(2곳/9) · NAVER 0.0배(2곳/9)
양 끝단이 모두 망가져 있다. 업종 백분위 점수(ADR-0005)가 이 표로 계산된다.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.data.base import trim_peers


class TrimPeersProximityTests(unittest.TestCase):
    def _peers(self):
        # 자사 100억. 위로 크게 벌어진 목록에 규모가 가까운 두 곳이 섞여 있다.
        return pd.DataFrame(
            {"market_cap": [1e13, 5e12, 1e12, 1.2e10, 8e9, 1e10]},
            index=["초대형.KS", "대형.KS", "중형.KS", "인접A.KS", "인접B.KS", "자사.KS"])

    def test_picks_size_neighbours_not_the_biggest(self):
        out = trim_peers(self._peers(), "자사.KS", 3, self_mcap=1e10)
        self.assertIn("자사.KS", out.index)
        self.assertIn("인접A.KS", out.index)     # 1.2배
        self.assertIn("인접B.KS", out.index)     # 0.8배
        self.assertNotIn("초대형.KS", out.index)  # 1000배 — 예전에는 1순위였다

    def test_is_symmetric_in_log_space(self):
        # 2배와 1/2배는 같은 거리다. 선형 차이로 재면 큰 쪽만 뽑힌다.
        peers = pd.DataFrame({"market_cap": [2e10, 5e9, 1e10]},
                             index=["두배.KS", "반.KS", "자사.KS"])
        out = trim_peers(peers, "자사.KS", 3, self_mcap=1e10)
        self.assertEqual(set(out.index), {"두배.KS", "반.KS", "자사.KS"})

    def test_without_self_mcap_keeps_the_old_top_n(self):
        # 규모를 모르는 채로 '인접'을 정할 수는 없다 — 종전 동작으로 남는다.
        out = trim_peers(self._peers(), "자사.KS", 3)
        self.assertIn("초대형.KS", out.index)
        self.assertIn("대형.KS", out.index)

    def test_self_is_kept_even_when_far_from_everyone(self):
        peers = pd.DataFrame({"market_cap": [1e13, 9e12, 8e12, 1e8]},
                             index=["A.KS", "B.KS", "C.KS", "자사.KS"])
        out = trim_peers(peers, "자사.KS", 2, self_mcap=1e8)
        self.assertIn("자사.KS", out.index)
        self.assertEqual(len(out), 2)

    def test_peers_without_market_cap_go_last_but_are_not_dropped(self):
        # 시총 결측 피어를 버리면 표본이 더 얇아진다. 뒤로 밀되 자리는 남긴다.
        peers = pd.DataFrame({"market_cap": [1.1e10, float("nan"), 1e10]},
                             index=["인접.KS", "결측.KS", "자사.KS"])
        out = trim_peers(peers, "자사.KS", 3, self_mcap=1e10)
        self.assertEqual(list(out.index)[:2], ["자사.KS", "인접.KS"])
        self.assertIn("결측.KS", out.index)

    def test_nonpositive_self_mcap_falls_back_to_top_n(self):
        # log를 못 씌운다. 값을 지어내지 않고 종전 동작으로 물러난다(ADR-0011).
        out = trim_peers(self._peers(), "자사.KS", 3, self_mcap=0)
        self.assertIn("초대형.KS", out.index)


class SelectPeersKrProximityTests(unittest.TestCase):
    def _listing(self):
        return pd.DataFrame({
            "Code": ["009310", "BIG1", "BIG2", "BIG3", "NEAR1", "NEAR2"],
            "Name": ["참엔지니어링", "한미반도체", "두산밥캣", "피에스케이",
                     "인접1", "인접2"],
            "Sector": ["기계"] * 6,
            "Marcap": [2.33e10, 2.03e13, 5.78e12, 3.75e12, 2.8e10, 1.9e10],
            "is_common": [True] * 6,
        })

    def test_candidates_are_size_neighbours(self):
        from src.data import universe

        with patch.object(universe, "get_kr_listing", self._listing):
            got = universe.select_peers_kr("009310", n=3)
        self.assertEqual(got[0], "009310")          # 자기 자신이 먼저
        self.assertEqual(set(got), {"009310", "NEAR1", "NEAR2"})

    def test_unknown_code_returns_itself(self):
        from src.data import universe

        with patch.object(universe, "get_kr_listing", self._listing):
            self.assertEqual(universe.select_peers_kr("999999", n=5), ["999999"])


if __name__ == "__main__":
    unittest.main()
