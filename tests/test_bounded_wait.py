"""방문자의 대기를 묶고, 느린 원천을 남긴다 (ADR-0052).

2026-08-13에 첫 조회가 50초 걸린 일이 있었는데 **원인을 특정하지 못했다.** 자는 서버·
예열 경합·코드 경로를 하나씩 재서 배제했지만(전부 아니었다), 로그에도 화면에도 아무
흔적이 없어 마지막은 추정이었다. 여기서 지키는 것은 둘이다:

  ㉠ **묶는다** — 로드 경로의 Gemini 호출은 총 예산을 넘기지 않는다.
     `generate_text`가 모델 후보를 순서대로 시도하므로, 요청별 타임아웃만으로는
     최악이 `60초 × 후보 수`(보통 5개 이상)다.
  ㉡ **남긴다** — 느린 *실패*는 예외로 남지만 느린 *성공*은 아무것도 안 남긴다.
"""
from __future__ import annotations

import io
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from src.data import cache as cachemod
from src.data import gemini


class _Resp:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self._text = text
        self.text = text

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}


class GenerateTextBudgetTests(unittest.TestCase):
    """모델 후보를 도는 동안 총 시간이 예산을 넘지 않는다."""

    def setUp(self):
        self.models = ("m1", "m2", "m3", "m4", "m5")

    def _run(self, budget, per_call_sleep, status=500):
        seen = []

        def slow_post(url, **kw):
            seen.append(kw.get("timeout"))
            time.sleep(per_call_sleep)
            return _Resp(status=status, text="upstream boom")

        with patch.object(gemini, "resolve_candidates", lambda: self.models), \
             patch.object(gemini, "get_api_key", lambda: "k"), \
             patch.object(gemini.requests, "post", slow_post):
            t0 = time.perf_counter()
            with self.assertRaises(RuntimeError):
                gemini.generate_text("p", budget=budget)
            return time.perf_counter() - t0, seen

    def test_the_budget_stops_the_model_loop(self):
        """예산 0.3초 · 후보 5개 · 각 0.15초 → 다섯 번 다 돌면 0.75초다."""
        took, seen = self._run(budget=0.3, per_call_sleep=0.15)
        self.assertLess(took, 0.6, f"{took:.2f}초 — 예산이 루프를 멈추지 못했다")
        self.assertLess(len(seen), len(self.models),
                        f"후보를 {len(seen)}개나 시도했다 — 예산이 무시됐다")

    def test_each_request_timeout_is_capped_by_what_is_left(self):
        """남은 예산보다 긴 요청 타임아웃을 주면 예산이 의미가 없다."""
        _, seen = self._run(budget=0.4, per_call_sleep=0.05)
        self.assertTrue(seen, "요청이 한 번도 안 나갔다")
        for t in seen:
            self.assertLessEqual(t, 0.4 + 1e-6, f"요청 타임아웃 {t}가 예산보다 크다")

    def test_without_a_budget_nothing_changes(self):
        """온디맨드 해설 버튼은 종전대로 — 긴 답을 중간에 자르면 그게 더 나쁘다."""
        seen = []

        def post(url, **kw):
            seen.append(kw.get("timeout"))
            return _Resp(text="답변")

        with patch.object(gemini, "resolve_candidates", lambda: self.models), \
             patch.object(gemini, "get_api_key", lambda: "k"), \
             patch.object(gemini.requests, "post", post):
            self.assertEqual(gemini.generate_text("p"), "답변")
        self.assertEqual(seen, [gemini.REQUEST_TIMEOUT])

    def test_the_load_path_call_actually_passes_a_budget(self):
        """데코레이터만 맞고 호출부가 안 주면 소용없다."""
        from src.analysis import ai_analysis

        got = {}

        def fake(prompt, **kw):
            got.update(kw)
            return '{"sector":"반도체","industry":"메모리","peers":[{"name":"A","ticker":"1"}]}'

        with patch.object(ai_analysis, "generate_text", fake):
            ai_analysis.classify_peers.__wrapped__("삼성전자", "KR")   # 캐시 우회
        self.assertEqual(got.get("budget"), ai_analysis.CLASSIFY_BUDGET_SEC)


class SlowSourceLogTests(unittest.TestCase):
    """느린 성공도 흔적을 남긴다 — 그것이 없어서 50초의 원인을 못 찾았다."""

    def _capture(self, seconds, name="느린원천"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cachemod._note_if_slow(name, time.time() - seconds)
        return buf.getvalue()

    def test_a_slow_source_is_named_with_its_seconds(self):
        out = self._capture(cachemod.SLOW_SOURCE_SEC + 2, "ai_peers")
        self.assertIn("[느림]", out)
        self.assertIn("ai_peers", out)

    def test_a_normal_source_stays_quiet(self):
        """정상 조회에서 시끄러우면 아무도 안 읽는다."""
        self.assertEqual(self._capture(0.5, "naver_fund_v2"), "")

    def test_the_diagnostic_never_breaks_the_lookup(self):
        """진단 장치가 본 기능을 무너뜨리면 없는 것보다 나쁘다."""
        with patch.object(cachemod, "SLOW_SOURCE_SEC", object()):   # 비교 자체가 터진다
            cachemod._note_if_slow("x", time.time() - 99)            # 예외가 새면 실패


if __name__ == "__main__":
    unittest.main()
