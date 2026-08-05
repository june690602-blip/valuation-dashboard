"""비ASCII 경로 인증서 우회 (ADR-0027).

**이 우회가 언제 움직이고 언제 가만히 있는지**를 고정한다. 환경에 손을 대는 코드라
'필요할 때만'이 지켜지지 않으면 그 자체가 사고다.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.data import ca_bundle


def _write(path: Path, text: str = "-----BEGIN CERTIFICATE-----\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class CaBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    # ── 가만히 있어야 하는 경우 셋 ────────────────────────────────
    def test_does_nothing_when_user_already_set_the_variable(self):
        env = {ca_bundle.ENV_VAR: "/somewhere/mine.pem"}
        src = _write(self.root / "한글" / "cacert.pem")
        self.assertIsNone(ca_bundle.install(env, src, self.root))
        self.assertEqual(env[ca_bundle.ENV_VAR], "/somewhere/mine.pem")

    def test_does_nothing_when_the_path_is_already_ascii(self):
        env: dict = {}
        src = _write(self.root / "plain" / "cacert.pem")
        self.assertIsNone(ca_bundle.install(env, src, self.root))
        self.assertNotIn(ca_bundle.ENV_VAR, env)

    def test_does_nothing_when_the_cache_root_is_also_non_ascii(self):
        """놓을 자리까지 한글이면 우회할 곳이 없다 — 조용히 물러선다."""
        env: dict = {}
        src = _write(self.root / "한글" / "cacert.pem")
        self.assertIsNone(ca_bundle.install(env, src, self.root / "받는곳"))
        self.assertNotIn(ca_bundle.ENV_VAR, env)

    # ── 움직여야 하는 경우 ───────────────────────────────────────
    def test_mirrors_to_an_ascii_path_and_sets_the_variable(self):
        env: dict = {}
        src = _write(self.root / "투자지표" / "cacert.pem", "CERT-BODY\n")
        got = ca_bundle.install(env, src, self.root)
        self.assertIsNotNone(got)
        self.assertTrue(str(got).isascii(), f"복사본 경로가 여전히 비ASCII다: {got}")
        self.assertEqual(env[ca_bundle.ENV_VAR], got)
        self.assertEqual(Path(got).read_text(encoding="utf-8"), "CERT-BODY\n")

    def test_refreshes_the_copy_when_certifi_is_newer(self):
        """certifi가 갱신되면 복사본도 따라가야 한다 — 옛 인증서를 붙들면 검증이 틀어진다."""
        src = _write(self.root / "투자지표" / "cacert.pem", "OLD\n")
        first = ca_bundle.mirror_ca_bundle(src, self.root)
        self.assertIsNotNone(first)
        import os
        import time
        time.sleep(0.01)
        src.write_text("NEW\n", encoding="utf-8")
        os.utime(src, None)
        second = ca_bundle.mirror_ca_bundle(src, self.root)
        self.assertEqual(Path(second).read_text(encoding="utf-8"), "NEW\n")

    def test_missing_source_is_not_an_error(self):
        """인증서가 없어도 예외를 내지 않는다 — 우회 실패로 앱이 죽으면 본말전도다."""
        self.assertIsNone(
            ca_bundle.mirror_ca_bundle(self.root / "투자지표" / "없다.pem", self.root))


if __name__ == "__main__":
    unittest.main()
