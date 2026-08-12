"""저장소에 구워 둔 계수를 읽는 경로 (이슈 #131).

이 경로가 하는 일은 하나다 — **사용자가 전 종목 수집(실측 58초)을 겪지 않게 하는 것**.
그래서 검사할 것도 하나다: *빠른 길이 되, 그 길이 막혔을 때 조용히 원래 길로 물러나는가.*

여기서 네트워크를 실제로 타지 않는다. `urlopen`을 갈아 끼워 응답만 흉내낸다 —
CI가 외부 원천에 매달리면 관문이 원천 장애로 빨간불이 되고, 그러면 아무도 안 믿는다.
"""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from src.analysis.warranted import REQUIRED_COEF_KEYS
from src.data import universe_multiples as UM


def _leg(n=1000):
    """`_coefficients_usable`을 통과하는 최소 계수 한 다리."""
    return {k: ([] if k in ("size_knots", "size_slopes")
                else {} if k in ("sector_coef", "sector_median_mcap",
                                 "sector_median_roe_coef")
                else n if k == "n" else 0.0)
            for k in REQUIRED_COEF_KEYS}


def _coef():
    return {"pbr": _leg(), "per": _leg()}


class _Resp(io.BytesIO):
    """urlopen이 돌려주는 것 흉내 — status와 컨텍스트 매니저만 있으면 된다."""

    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class FromRepoTests(unittest.TestCase):
    def test_valid_file_is_used(self):
        payload = json.dumps(_coef()).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=_Resp(payload)):
            got = UM._from_repo("KR")
        self.assertEqual(sorted(got), ["pbr", "per"])

    def test_broken_file_is_rejected_not_cached(self):
        """브랜치에 깨진 파일이 올라가면 **그것이 24시간 캐시가 된다.**

        그러면 그날 전 종목이 피어 중앙값 폴백을 탄다 — 조용히 나빠지는 종류라
        받아온 것도 같은 스키마 검사를 통과해야 한다.
        """
        for body in (b'{"pbr": {"intercept": 0.0}}',      # 키가 모자람
                     b'{"built_at": "2026-08-12"}',       # 메타를 계수 파일에 얹은 경우
                     b'[]', b'not json'):
            with self.subTest(body=body[:24]):
                with patch("urllib.request.urlopen", return_value=_Resp(body)):
                    self.assertIsNone(UM._from_repo("KR"))

    def test_network_failure_falls_back_quietly(self):
        """깃허브가 죽어도 판정이 멈추면 안 된다 — None을 주고 직접 수집으로 내려간다."""
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertIsNone(UM._from_repo("KR"))

    def test_non_200_is_rejected(self):
        with patch("urllib.request.urlopen", return_value=_Resp(b"{}", status=404)):
            self.assertIsNone(UM._from_repo("KR"))

    def test_can_be_turned_off(self):
        """오프라인 개발·테스트에서 통째로 끌 수 있어야 한다."""
        with patch.object(UM, "COEF_BASE_URL", ""):
            with patch("urllib.request.urlopen", side_effect=AssertionError("불리면 안 된다")):
                self.assertIsNone(UM._from_repo("KR"))

    def test_repo_file_is_preferred_over_scraping(self):
        """**이 테스트가 이슈 #131의 본론이다** — 파일이 있으면 수집을 시작조차 안 한다."""
        payload = json.dumps(_coef()).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=_Resp(payload)), \
                patch.object(UM, "collect_kr",
                             side_effect=AssertionError("수집이 돌면 안 된다")):
            got = UM.get_coefficients.__wrapped__("KR")     # file_cache 우회
        self.assertEqual(sorted(got), ["pbr", "per"])

    def test_scraping_still_runs_when_repo_file_is_missing(self):
        """빠른 길이 막혔을 때 원래 길이 살아 있는가 — 물러날 곳이 없으면 의존성이 된다."""
        called = []

        def fake_collect():
            called.append(True)
            return "SNAP"

        with patch("urllib.request.urlopen", side_effect=OSError("down")), \
                patch.object(UM, "collect_kr", fake_collect), \
                patch.object(UM, "build_coefficients", lambda snap: _coef()):
            got = UM.get_coefficients.__wrapped__("KR")
        self.assertEqual(called, [True], "수집으로 물러나지 않았다")
        self.assertEqual(sorted(got), ["pbr", "per"])


class BuilderIndependenceTests(unittest.TestCase):
    """밤에 도는 빌더가 **자기가 만든 파일을 다시 읽으면** 계수가 영원히 안 바뀐다."""

    def test_builder_does_not_call_get_coefficients(self):
        """호출을 **구문으로** 본다 — 글자로 찾으면 '부르지 마라'는 주석에도 걸린다."""
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "build_coefficients.py").read_text(encoding="utf-8")
        called = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                f = node.func
                name = (f.id if isinstance(f, ast.Name)
                        else f.attr if isinstance(f, ast.Attribute) else None)
                if name:
                    called.add(name)
        self.assertNotIn("get_coefficients", called,
                         "빌더가 get_coefficients를 부르면 어제 파일을 그대로 되쓴다")
        self.assertIn("build_coefficients", called, "적합을 직접 불러야 한다")
        self.assertTrue({"collect_kr", "collect_us"} & called, "수집을 직접 불러야 한다")
