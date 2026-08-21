"""주식·ETF 응답 스키마가 한 변수에 다시 섞이지 않게 막는다(이슈 #80).

`stock.js`는 엔드포인트 둘의 응답을 그린다. 예전에는 전역 `D` 하나에 둘을 번갈아
담았는데, **같은 이름이 서로 다른 것을 뜻했다**:

    이름   주식 `D.meta.name`          / ETF `D.name`
    판정   주식 `D.verdict.verdict`(객체) / ETF `D.verdict`(문자열)
    price  주식 시계열 **객체**(`.error`) / ETF 현재가 **숫자**

사고가 안 난 이유는 타입이 아니라 `setEtfMode()`가 두 뷰를 서로 감췄기 때문이다 —
**화면 표시 상태가 안전장치 노릇**을 하고 있었다. 렌더 순서가 바뀌거나 한쪽 뷰에서
다른 쪽 함수를 부르면 조용히 `undefined`가 난다.

그래서 변수를 `Dstock`·`Detf` 둘로 갈랐고, 이 테스트가 그 경계를 지킨다.
`load()`만 예외다 — 한쪽을 담으며 다른 쪽을 비우는 자리라 둘을 함께 만진다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOCK_JS = ROOT / "web" / "assets" / "stock.js"

FN_RE = re.compile(r"^  function ([A-Za-z_]\w*)")
# 들여쓰기 2칸의 `var`는 IIFE 최상단 — 즉 **함수 밖**이다(함수 본문은 4칸부터).
# 이걸로 함수 소속을 끊지 않으면 `var Dstock = null, Detf = null;` 선언 한 줄이
# 바로 앞 함수의 것으로 잡혀, '두 스키마를 함께 쓴다'는 거짓 경보가 난다.
MODULE_VAR_RE = re.compile(r"^  var ")
# 식별자로 홀로 선 이름 — 앞이 단어·점·달러가 아니어야 한다(`D/E` 같은 리터럴 제외는 마스킹이 한다).
def _ident(name: str) -> re.Pattern:
    return re.compile(rf"(?<![\w.$]){re.escape(name)}(?![\w$])")


BARE_D = _ident("D")
DSTOCK = _ident("Dstock")
DETF = _ident("Detf")

# 두 페이로드를 함께 만지는 것이 **일**인 자리. 늘리려면 이유가 있어야 한다.
BOTH_ALLOWED = {"load", None}


def _code_spans(line: str, in_block: bool):
    """줄에서 (코드 구간, 다음 줄의 블록주석 상태). 문자열·주석은 빼고 본다."""
    spans, i, n, start = [], 0, len(line), 0
    quote = None
    while i < n:
        c = line[i]
        if in_block:
            if line.startswith("*/", i):
                in_block, i = False, i + 2
                start = i
                continue
            i += 1
            continue
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote, i = None, i + 1
                start = i
                continue
            i += 1
            continue
        if c in "'\"`":
            spans.append((start, i))
            quote, i = c, i + 1
            continue
        if line.startswith("//", i):
            spans.append((start, i))
            return spans, False
        if line.startswith("/*", i):
            spans.append((start, i))
            in_block, i = True, i + 2
            continue
        i += 1
    if not quote and not in_block:
        spans.append((start, n))
    return spans, in_block


def _scan():
    """[(줄번호, 함수명, 코드만 남긴 문자열)] — 문자열·주석은 지운 상태."""
    rows, fn, in_block = [], None, False
    for no, line in enumerate(STOCK_JS.read_text(encoding="utf-8").split("\n"), 1):
        m = FN_RE.match(line)
        if m:
            fn = m.group(1)
        elif MODULE_VAR_RE.match(line):
            fn = None
        spans, nxt = _code_spans(line, in_block)
        code = "".join(line[a:b] for a, b in spans)
        in_block = nxt
        rows.append((no, fn, code))
    return rows


class PayloadSchemaSplitTests(unittest.TestCase):
    def setUp(self):
        self.rows = _scan()

    def test_scanner_sees_the_split_variables(self):
        """스캐너가 실제로 뭔가 보고 있는지 먼저 확인한다.

        마스킹이 과해서 전부 지워지면 아래 검사들이 **조용히 아무것도 안 하게** 된다."""
        stock = sum(1 for _, _, c in self.rows if DSTOCK.search(c))
        etf = sum(1 for _, _, c in self.rows if DETF.search(c))
        self.assertGreater(stock, 30, "Dstock 참조가 너무 적다 — 스캐너를 의심하라")
        self.assertGreater(etf, 20, "Detf 참조가 너무 적다 — 스캐너를 의심하라")

    def test_no_bare_global_payload(self):
        """전역 `D` 하나로 되돌아가지 않는다."""
        bad = [(no, c.strip()[:90]) for no, _fn, c in self.rows if BARE_D.search(c)]
        self.assertEqual(bad, [], "전역 `D`가 되살아났다 — Dstock/Detf로 갈라야 한다:\n" +
                         "\n".join(f"  stock.js:{n}: {t}" for n, t in bad))

    def test_no_function_mixes_both_schemas(self):
        """한 함수가 두 스키마를 함께 읽지 않는다 — `load()`만 예외."""
        seen: dict = {}
        for no, fn, code in self.rows:
            if DSTOCK.search(code):
                seen.setdefault(fn, set()).add("Dstock")
            if DETF.search(code):
                seen.setdefault(fn, set()).add("Detf")
        mixed = sorted(fn for fn, names in seen.items()
                       if len(names) > 1 and fn not in BOTH_ALLOWED)
        self.assertEqual(mixed, [], "두 스키마를 함께 쓰는 함수: " + ", ".join(mixed))

    def test_load_clears_the_other_payload(self):
        """`load()`는 한쪽을 담을 때 다른 쪽을 **반드시 비운다**.

        비우지 않으면 주식→ETF로 갈아탄 뒤 `if (Dstock)` 가드가 옛 응답으로 참이 되어
        ETF 화면에서 주식 렌더러가 돈다 — 이 이슈가 막으려던 바로 그 사고다."""
        body = [c for _no, fn, c in self.rows if fn == "load"]
        self.assertTrue(body, "load()를 못 찾았다 — 이 테스트가 무의미해졌다")
        joined = "\n".join(body)
        self.assertIn("Detf = res.j; Dstock = null;", joined,
                      "ETF 분기가 Dstock을 비우지 않는다")
        self.assertIn("Dstock = res.j; Detf = null;", joined,
                      "주식 분기가 Detf를 비우지 않는다")


class HeaderCardSharedTests(unittest.TestCase):
    """헤더 카드가 다시 두 벌이 되지 않게 막는다(이슈 #81).

    R4가 판정 색을 무채 잉크로 바꿀 때 두 자리를 각각 고쳐야 했다 —
    **한 곳을 놓치면 지운 색이 한쪽에만 돌아온다.**"""

    def setUp(self):
        self.src = STOCK_JS.read_text(encoding="utf-8")

    def test_both_headers_use_the_shared_pieces(self):
        for fn in ("hdrIdentity", "hdrZoneLabels", "hdrMarker", "hdrHeadline", "hdrCard"):
            with self.subTest(piece=fn):
                # 정의 1 + 주식 1 + ETF 1 = 최소 3회
                self.assertGreaterEqual(
                    len(re.findall(rf"\b{fn}\b", self.src)), 3,
                    f"{fn}을(를) 두 헤더가 함께 쓰지 않는다")

    def test_verdict_ink_is_declared_once(self):
        """판정 잉크는 상수 하나다 — 뷰마다 리터럴을 적으면 다시 갈라진다."""
        self.assertEqual(len(re.findall(r"var VERDICT_INK\b", self.src)), 1)

    def test_zone_vocabularies_stay_separate(self):
        """어휘는 합치지 않는다 — R3가 일부러 가른 것이다(ETF는 적정가를 계산하지 않는다)."""
        self.assertIn("['저평가', '적정', '고평가']", self.src)
        self.assertIn("['싼 구간', '보통', '비싼 구간']", self.src)

    def test_basket_feedback_is_written_once(self):
        """포트폴리오 담기 되먹임이 다시 두 벌이 되지 않는다(이슈 #81 '같이 움직이는 곳').

        주식·ETF 버튼이 같은 문구를 1.8초 바꿨다 되돌리는 부분을 각자 적고 있었다.
        문구를 고칠 일이 생기면 한 곳만 보면 되게 `basketAdd()`로 모았다."""
        for text in ("✓ 담았어요 — 🧺 포트폴리오에서 확인", "＋ 포트폴리오에 담기'; }, 1800"):
            with self.subTest(text=text[:20]):
                self.assertEqual(
                    self.src.count(text), 1,
                    f"'{text[:20]}…'가 두 번 이상 적혀 있다 — basketAdd()로 모을 것")

    def test_basket_record_shape_stays_at_the_caller(self):
        """레코드 모양(키·type·class)은 부르는 쪽이 만든다.

        주식은 야후 티커를 그대로 키로 쓰고 국내 ETF는 `.KS`를 붙인다 — 그 차이를
        `basketAdd()` 안으로 끌어들이면 공용 함수가 화면을 알아야 해서 다시 갈라진다."""
        self.assertIn("'국내주식'", self.src)
        self.assertIn("'국내기타ETF'", self.src)
        for token in ("'국내주식'", "'국내기타ETF'", "Detf.symbol + '.KS'"):
            with self.subTest(token=token):
                self.assertNotIn(token, self._basket_add_body(),
                                 f"{token}이 basketAdd() 안으로 들어왔다")

    def test_formatters_live_in_one_file(self):
        """포맷터가 stock.js로 되돌아오지 않는다(이슈 #79).

        `stock-format.js`로 뗀 뒤 stock.js는 **이름만 별칭으로 받는다** — 호출부
        60여 곳을 손대지 않으려는 선택이라, 별칭이 아니라 정의가 되살아나면 같은
        이름이 두 곳에서 다른 뜻이 될 수 있다(R5 2번 바구니가 `esc()` 4벌에서 겪은 일).
        """
        fmt_js = (STOCK_JS.parent / "stock-format.js").read_text(encoding="utf-8")
        for fn in ("won", "fmtPrice", "fmtMoney", "fmtPct", "fmtX", "fmtSigned",
                   "fmtMult", "na", "compactWon", "vIdx", "vPos", "vTone"):
            with self.subTest(fn=fn):
                self.assertIn(f"function {fn}(", fmt_js,
                              f"{fn}이 stock-format.js에 없다")
                self.assertNotIn(f"  function {fn}(", self.src,
                                 f"{fn}이 stock.js에 다시 정의됐다 — 별칭만 받아야 한다")

    def test_currency_has_one_owner(self):
        """통화(`CUR`)의 주인은 stock-format.js 하나다.

        예전에는 stock.js의 맨몸 전역이었고 `renderAll()`·`renderEtf()` 두 곳이 각자
        대입했다. 여기 맨몸 `CUR`이 되살아나면 두 주인이 생겨 화면마다 다른 통화로
        포맷될 수 있다."""
        # 뒤의 `:`까지 빼는 것은 **속성 키를 오탐하지 않으려는** 것이다 —
        # `makePriceChart(..., { …, CUR: FMT.currency() })`의 `CUR:`은 그 모듈이
        # 받는 인자 이름이지 여기서 전역을 읽는 자리가 아니다.
        bare_cur = re.compile(r"(?<![\w.$:])CUR(?![\w$:])")
        offenders = []
        for no, _fn, code in _scan():
            if bare_cur.search(code):
                offenders.append((no, code.strip()[:80]))
        self.assertEqual(offenders, [],
                         "stock.js에 맨몸 `CUR`이 되살아났다 — FMT.currency()를 쓸 것:\n" +
                         "\n".join(f"  stock.js:{n}: {t}" for n, t in offenders))

    def _basket_add_body(self) -> str:
        start = self.src.find("function basketAdd(")
        end = self.src.find("function addToBasket(")
        # 없어졌으면 되살아난 중복을 뜻하므로, 예외 대신 이유가 보이는 실패로 낸다.
        self.assertNotEqual(start, -1, "basketAdd()가 없다 — 담기 로직이 다시 갈라졌다")
        self.assertGreater(end, start, "addToBasket()이 basketAdd() 뒤에 있어야 한다")
        return self.src[start:end]


if __name__ == "__main__":
    unittest.main()
