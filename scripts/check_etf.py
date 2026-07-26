"""ETF 적정가 분석 헤드리스 검증: python scripts/check_etf.py [SYMBOL ...]

기본값(인자 없으면): SPY SCHD QQQ TLT VEA GLD — 자산군별 대표 ETF 6종.
analyze_etf("US", 심볼)의 결과 dict를 그대로 json.dumps해 출력한다(브라우저 없이 확인용).

Windows 콘솔(cp949)은 한글·특수문자 출력이 실패할 수 있어, 표준출력이 실패하면
scripts/check_etf_output.txt(UTF-8)로 대신 저장한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_SYMBOLS = ["SPY", "SCHD", "QQQ", "TLT", "VEA", "GLD"]


def main(symbols: list[str]) -> str:
    from src.web.serialize import analyze_etf

    lines = []
    for sym in symbols:
        lines.append(f"=== {sym} ===")
        try:
            data = analyze_etf("US", sym)
        except Exception as e:  # noqa: BLE001 — 검증 스크립트라 예외도 결과로 보여준다
            lines.append(f"  예외 발생: {e!r}")
            continue
        lines.append(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    syms = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_SYMBOLS
    text = main(syms)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text)
    except Exception:
        out_path = Path(__file__).resolve().parent / "check_etf_output.txt"
        out_path.write_text(text, encoding="utf-8")
        print(f"(콘솔 출력 실패 - {out_path} 에 UTF-8로 저장했습니다)")
