"""비ASCII 경로 인증서 우회 (ADR-0027).

**이 우회가 언제 움직이고 언제 가만히 있는지**를 고정한다. 환경에 손을 대는 코드라
'필요할 때만'이 지켜지지 않으면 그 자체가 사고다.

## 테스트 기반은 **반드시 ASCII 경로**여야 한다

이 파일은 `tempfile.TemporaryDirectory()`의 기본 위치를 쓰다가 **저자의 PC에서 처음부터
빨간색이었다.** 사용자명이 한글이면 `%TEMP%`가 `C:\\Users\\한글\\AppData\\Local\\Temp`가
되고, 그러면 *복사해 놓을 자리*까지 비ASCII라 `mirror_ca_bundle`이 **정상적으로**
물러선다(ADR-0027 한계: *"임시 폴더가 비ASCII면 못 고친다"*). CI(리눅스 `/tmp`)에서는
통과하므로 **초록이면서 로컬은 빨간** 상태가 유지됐다.

더 나쁜 것이 하나 있었다 — `test_does_nothing_when_the_path_is_already_ascii`가
그 PC에서 **틀린 이유로 통과**했다. 기반이 비ASCII라 원본도 비ASCII가 되어,
*"원본이 이미 ASCII라 안 움직였다"*가 아니라 *"목적지가 비ASCII라 못 움직였다"*로
None이 나왔다. 그래서 지금은 그 테스트가 **물러선 이유까지** 확인한다.

그래서 기반을 ASCII로 못 박고, 비ASCII 경로는 **그 아래에 한글 하위 폴더를 만들어**
쓴다. 그러면 두 축을 따로 통제할 수 있다: 원본이 비ASCII인가 · 목적지가 ASCII인가.
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


def _ascii_tmp_base() -> Path | None:
    """쓰기 가능한 **ASCII** 임시 기반. 못 찾으면 None.

    리눅스·CI는 `/tmp`가 이미 ASCII라 기본 위치를 그대로 쓴다 — **CI 동작을 바꾸지
    않는다.** 한글 사용자명 Windows에서만 같은 드라이브의 ASCII 자리로 물러선다.
    """
    default = Path(tempfile.gettempdir())
    if str(default).isascii():
        return default
    # `.drive`("C:")로 만들면 **드라이브 상대 경로**가 되어 현재 작업 디렉터리 아래로
    # 붙는다(실측: 저장소 안에 Temp/가 생겼다). 구분자를 포함하는 `.anchor`("C:\\")를 쓴다.
    fallback = Path(default.anchor or "C:\\") / "Temp"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        probe = fallback / ".ca_bundle_probe"
        probe.mkdir(exist_ok=True)
        probe.rmdir()
    except OSError:
        return None
    return fallback if str(fallback).isascii() else None


class CaBundleTests(unittest.TestCase):
    def setUp(self):
        base = _ascii_tmp_base()
        if base is None:
            self.skipTest("쓰기 가능한 ASCII 임시 경로가 없다 — 이 환경에서는 우회 자체가 "
                          "불가능하다(ADR-0027 한계)")
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        # 이 전제가 깨지면 아래 테스트들이 조용히 다른 것을 재게 된다
        self.assertTrue(str(self.root).isascii(), f"테스트 기반이 비ASCII다: {self.root}")

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
        # **물러선 이유까지 확인한다.** 같은 root에 비ASCII 원본을 주면 실제로 복사되므로,
        # 위 None은 '목적지가 비ASCII'가 아니라 '원본이 이미 ASCII'에서 나온 것이다.
        # 이 확인이 없을 때 한글 사용자명 PC에서 이 테스트가 틀린 이유로 통과했다(파일 머리말).
        other = _write(self.root / "한글" / "cacert.pem")
        self.assertIsNotNone(ca_bundle.mirror_ca_bundle(other, self.root))

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
