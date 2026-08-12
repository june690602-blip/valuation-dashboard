"""방법 번호를 손으로 적은 자리가 또 있는가 — 그리고 '신뢰도'가 되살아나지 않았는가.

**왜 또 필요한가.** 이 저장소는 같은 실패를 세 번 했다.

1. PR #139 — 화면 **아홉 자리**에 `①②③`이 손으로 적혀 있었고, ⑤가 축이 된(ADR-0015) 뒤로
   어느 종목에서도 맞지 않았다. `v.weights`에서 유도하도록 고쳤다.
2. `tests/test_method_marks_parity.py` — 두 언어의 **매핑 표**가 어긋나지 않는지 지킨다.
   그런데 그 관문은 표만 본다. **산문에 박힌 번호는 못 본다.**
3. 그래서 `ai_analysis.build_opinion_context`의 `"①②③ 가중평균"`이 살아남았다.
   화면이 아니라 **Gemini에게 주는 사실 요약**이라 눈에 띄지 않았고, 모델은 받은 번호를
   근거처럼 되받아 쓴다.

여기서는 표가 아니라 **결과 문자열**을 본다 — 판정에 ①⑤만 든 종목의 요약에 `①②③`이
들어 있으면 그것이 곧 손으로 적었다는 증거다.

'신뢰도'도 같은 방식으로 지킨다. ADR-0043이 등급을 뗐는데 페이로드에 값이 남아 있으면
화면이 조용히 배지를 되살린다 — 주식 쪽은 `test_analysis_accuracy.py`가 지키고 있고,
여기서는 **ETF 쪽**을 지킨다(같은 자리의 같은 단어가 화면마다 다른 뜻이 되지 않도록).
"""
from __future__ import annotations

import unittest

from src.analysis.valuation import METHOD_MARKS, marks_of


class _Val:
    """판정에 ①⑤만 든 종목 — ②는 ADR-0035로 빠지고 ③은 장부 왜곡이면 빠진다(AAPL이 그렇다)."""

    weights = {"업종 상대가치": 0.5, "정규화 이익": 0.5}
    fair_low, fair_mid, fair_high = 100.0, 120.0, 140.0
    estimates = ()
    forward_growth = None
    consensus_premium = None


class MarksComeFromWeightsTests(unittest.TestCase):
    def test_marks_of_reflects_the_actual_composition(self):
        self.assertEqual(marks_of(_Val.weights), "①⑤")
        self.assertEqual(marks_of({"수익가치(RIM)": 1.0}), "③")
        self.assertEqual(marks_of({}), "")

    def test_marks_of_sorts_by_number_not_by_dict_order(self):
        # 파이썬 사전은 삽입 순서를 지킨다 — 정렬하지 않으면 '⑤①'이 나온다.
        self.assertEqual(marks_of({"정규화 이익": 0.5, "업종 상대가치": 0.5}), "①⑤")

    def test_ai_prompt_does_not_hardcode_the_old_trio(self):
        """Gemini 사실 요약이 그 종목의 실제 구성을 적어야 한다.

        Red-Green: `f"…(①②③ 가중평균…)"`으로 되돌리면 이 테스트가 깨진다.
        """
        import src.analysis.ai_analysis as ai

        line = (f"펀더멘털 적정주가 범위({marks_of(_Val.weights) + ' '}가중평균, =목표가 근거): "
                f"{_Val.fair_low:,.0f} ~ {_Val.fair_high:,.0f} KRW")
        self.assertIn("①⑤ 가중평균", line)
        self.assertNotIn("①②③", line)
        # 소스에 그 문자열이 되살아나지 않았는지도 본다 — 위 조립은 규칙을 보여줄 뿐이고,
        # 실제 프롬프트를 만드는 곳이 다시 손으로 적으면 여기서 걸린다.
        src = __import__("pathlib").Path(ai.__file__).read_text(encoding="utf-8")
        body = "\n".join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertNotIn("①②③ 가중평균", body,
                         "프롬프트가 방법 번호를 손으로 적었다 — marks_of(val.weights)를 쓸 것")

    def test_every_method_in_the_table_has_a_mark(self):
        # 방법이 늘어나면 표에도 번호가 있어야 한다 — 없으면 marks_of가 조용히 빠뜨린다.
        self.assertEqual(set(METHOD_MARKS.values()), set("①②③④⑤"))


class ConfidenceStaysGoneTests(unittest.TestCase):
    def test_etf_payload_does_not_carry_a_confidence_grade(self):
        """ETF 페이로드에 등급을 실으면 화면이 배지를 되살린다(ADR-0043과 같은 이유).

        Red-Green: `serialize._etf_payload`에 `"confidence": r.confidence`를 되돌리면 깨진다.
        """
        import inspect

        import src.web.serialize as ser

        src = inspect.getsource(ser)
        body = "\n".join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertNotIn('"confidence"', body,
                         "페이로드에 신뢰도 등급이 되살아났다 — 화면이 그것으로 배지를 만든다")

    def test_the_word_is_gone_from_the_etf_header(self):
        """ETF 헤더 오른쪽 끝의 `신뢰도 [등급]`을 뗀 것이 유지되는가."""
        from pathlib import Path

        js = (Path(__file__).resolve().parents[1] / "web" / "assets" / "stock.js")
        body = "\n".join(ln for ln in js.read_text(encoding="utf-8").splitlines()
                         if "//" not in ln.split("'")[0])
        self.assertNotIn("D.confidence", body,
                         "ETF 헤더가 신뢰도 등급을 다시 그린다")


if __name__ == "__main__":
    unittest.main()
