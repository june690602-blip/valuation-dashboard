"""예열 로그가 남기는 메모리 한 줄 (2026-09-24 진단).

이 예열은 기동 중 **한 번에** 고점을 찍는다 — 운영 실측으로 가동 3분에 최고 515MB였고
인스턴스 한도가 512MiB다. 어느 단계에서 치솟는지는 밖에서 볼 수 없었다(대시보드 그래프는
분 단위인데 예열은 100초에 끝난다). 그래서 단계마다 한 줄 남긴다.

검사하는 것은 둘 — 읽히면 값을 적는가, `/proc`이 없으면 조용한가.
"""
from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from src.web import prewarm


class MemNoteTests(unittest.TestCase):
    def test_reports_current_and_peak(self):
        status = "Name:\tpython\nVmHWM:\t  527552 kB\nVmRSS:\t  419480 kB\nThreads:\t33\n"
        with patch("builtins.open", return_value=io.StringIO(status)):
            note = prewarm.mem_note()
        self.assertIn("409MB", note)        # 419480 kB
        self.assertIn("515MB", note)        # 527552 kB — 한도 512MiB를 스친 그 값
        self.assertTrue(note.startswith(" · "), note)

    def test_is_silent_where_proc_does_not_exist(self):
        """윈도우 로컬에서 예열 로그 모양이 바뀌면 안 된다 — 조용히 빠진다."""
        with patch("builtins.open", side_effect=OSError("no /proc")):
            self.assertEqual(prewarm.mem_note(), "")

    def test_is_silent_when_the_field_is_missing(self):
        with patch("builtins.open", return_value=io.StringIO("Name:\tpython\n")):
            self.assertEqual(prewarm.mem_note(), "")

    def test_the_note_survives_a_cp949_console(self):
        """`대시보드실행.bat`의 콘솔이 cp949다 — 예전에 여기서 예열이 통째로 죽었다."""
        status = "VmHWM:\t  527552 kB\nVmRSS:\t  419480 kB\n"
        with patch("builtins.open", return_value=io.StringIO(status)):
            note = prewarm.mem_note()
        note.encode("cp949")               # 던지면 실패다


if __name__ == "__main__":
    unittest.main()
