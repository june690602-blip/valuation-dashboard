"""DART 회사코드 표를 **트리를 통째로 올리지 않고** 읽는가 (2026-09-24 메모리 조사).

기동 예열의 메모리 고점이 운영에서 **521MB**였다(인스턴스 한도 512MiB). 예열 로그가
`[완료] KR 005930 … 메모리 283MB(최고 521MB)`로, 고점이 **첫 종목 분석**에서 찍히고
그 뒤로는 오르지 않음을 보여 줬다. 첫 분석에만 있는 무거운 일이 이 호출이다 —
7일 캐시라 두 번째부터는 아예 타지 않는다.

`ET.fromstring`은 15MB짜리 XML을 트리로 올리며 **97MB를 더 쓴다**(로컬 실측, 2회 일치).
`iterparse` + `root.clear()`는 **3MB**다.

검사하는 것은 둘 — 값이 그대로 나오는가, 그리고 트리를 통째로 올리는 방식으로
되돌아가지 않았는가.
"""
from __future__ import annotations

import inspect
import io
import unittest
import zipfile
from unittest.mock import patch

from src.data import opendart


def _zip_bytes(entries) -> bytes:
    body = "".join(
        f"<list><corp_code>{c}</corp_code><corp_name>{n}</corp_name>"
        f"<stock_code>{s}</stock_code></list>" for c, n, s in entries)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><result>{body}</result>'.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


class _Resp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class CorpMapTests(unittest.TestCase):
    def _run(self, entries):
        with patch.object(opendart, "get_api_key", return_value="키"), \
             patch.object(opendart.requests, "get", return_value=_Resp(_zip_bytes(entries))):
            return opendart.get_corp_code_map.__wrapped__()

    def test_maps_stock_code_to_corp_code(self):
        df = self._run([("00126380", "삼성전자", "005930"), ("00164779", "SK하이닉스", "000660")])
        self.assertEqual(list(df.index), ["005930", "000660"])
        self.assertEqual(df.loc["005930", "corp_code"], "00126380")
        self.assertEqual(df.loc["000660", "corp_name"], "SK하이닉스")

    def test_skips_unlisted_rows(self):
        """corpCode.xml은 비상장까지 10만 건이고, 그쪽 stock_code는 공백 한 칸이다."""
        df = self._run([("00000001", "비상장회사", " "), ("00126380", "삼성전자", "005930")])
        self.assertEqual(list(df.index), ["005930"])

    def test_pads_short_codes_and_drops_duplicates(self):
        df = self._run([("00000002", "짧은코드", "5930"), ("00000003", "중복", "005930")])
        self.assertEqual(list(df.index), ["005930"])
        self.assertEqual(df.loc["005930", "corp_name"], "짧은코드")   # 먼저 온 것을 남긴다

    def test_does_not_build_the_whole_tree_again(self):
        """되돌아가면 고점이 100MB 가까이 다시 붙는다 — 그래서 방식을 못으로 박는다."""
        src = inspect.getsource(opendart.get_corp_code_map.__wrapped__)
        body = src.split(chr(34) * 3)[-1]          # 독스트링은 빼고 본문만 본다 —
        self.assertIn("iterparse", body)           # 설명하려고 적은 이름에 걸리면 안 된다
        self.assertIn("root.clear()", body)
        self.assertNotIn("fromstring", body)


if __name__ == "__main__":
    unittest.main()
