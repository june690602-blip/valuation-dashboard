"""ETF 적정가 분석 헤드리스 검증: python scripts/check_etf.py [KR|US] [SYMBOL ...] [--json]

  python scripts/check_etf.py                 # 기본 6종(미국)
  python scripts/check_etf.py KR              # 기본 4종(한국)
  python scripts/check_etf.py KR 069500 148070
  python scripts/check_etf.py US SPY --json   # 프런트가 받는 JSON 원본 전체

기본은 사람이 읽을 요약(판정·세 축·지표)이고, --json을 붙이면 analyze_etf()가 돌려주는
dict를 그대로 덤프한다. 브라우저 없이 확인용이라 API 키가 없어도 동작한다.

Windows 콘솔(cp949)은 한글·특수문자 출력이 실패할 수 있어, 표준출력이 실패하면
scripts/check_etf_output.txt(UTF-8)로 대신 저장한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 자산군별 대표 ETF — 유형 분기(주식·성장·채권·해외·원자재)가 모두 한 번씩 걸리게 골랐다.
DEFAULTS = {
    "US": ["SPY", "SCHD", "QQQ", "TLT", "VEA", "GLD"],
    "KR": ["069500", "360750", "148070", "091160"],
}


def _pct(v, digits=2, signed=True):
    """비율(0.0015) → '0.15%'. 총보수·배당률처럼 부호가 의미 없는 값은 signed=False."""
    if v is None:
        return "—"
    return f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"


def summarize(market: str, sym: str) -> str:
    from src.web.serialize import analyze_etf

    try:
        d = analyze_etf(market, sym)
    except Exception as e:  # noqa: BLE001 — 검증 스크립트라 예외도 결과로 보여준다
        return f"=== {market} {sym} ===\n  예외 발생: {e!r}\n"
    if d.get("error"):
        return f"=== {market} {sym} ===\n  오류: {d['error']}\n"

    m, t, fi = d["metrics"], d["trend"], d.get("fundInfo") or {}
    out = [f"=== {market} {sym}  {d['name']}  [{d['type_label']}] ==="]
    out.append(f"  판정      {d['verdict'] or '보류'}  (신뢰도 {d['confidence']} · 주 신호 {d['primary']})")
    out.append(f"  현재가    {d['price']:,.2f} {d['currency']}   NAV {d['nav']}   괴리 {_pct(d['premium'])}")
    for a in d["axes"]:
        mark = " " if a["available"] else "×"
        out.append(f"  {mark}{a['title']}: {a['value']}")
    pe = f"{m['basket_pe']:.1f}" if m["basket_pe"] is not None else "—"
    pos = f"{t['w52_pos']:.0f}%" if t["w52_pos"] is not None else "—"
    out.append(f"  지표      총보수 {_pct(m['expense_ratio'], 4, signed=False)} · "
               f"배당률 {_pct(m['div_yield'], signed=False)} · 바스켓PER {pe} · "
               f"추적오차 {_pct(d['trackingError'], signed=False)}")
    out.append(f"  추세      52주 위치 {pos} · 1년 초과성과 {_pct(t['rel_1y'])} · "
               f"벤치마크 {m['bench_label'] or '—'}")
    out.append(f"  수익률    YTD {_pct(d['returns']['ytd'])} · 3년 {_pct(d['returns']['y3'])} · "
               f"5년 {_pct(d['returns']['y5'])}")
    if fi.get("base_index") or fi.get("issuer"):   # 한국 전용 항목
        out.append(f"  펀드정보  기초지수 {fi.get('base_index') or '—'} · 운용사 {fi.get('issuer') or '—'} · "
                   f"상장일 {fi.get('listed_date') or '—'}")
    if fi.get("basket_note"):
        out.append(f"  산출근거  {fi['basket_note']}")
    out.append(f"  구성      보유 {len(d['holdings'])}종 · 섹터 {len(d['sectors'])}개 · "
               f"국가 {len(d.get('countries') or [])}개 · 분배금 {len(d['distributions'])}년 · 뉴스 {len(d['news'])}건")
    for label, reason in d["masked"]:
        out.append(f"  마스킹    {label} — {reason}")
    for n in d["notes"]:
        out.append(f"  참고      {n}")
    out.append(f"  출처      {' · '.join(d['sources'])}")
    return "\n".join(out) + "\n"


def dump_json(market: str, sym: str) -> str:
    from src.web.serialize import analyze_etf

    try:
        data = analyze_etf(market, sym)
    except Exception as e:  # noqa: BLE001
        return f"=== {market} {sym} ===\n  예외 발생: {e!r}\n"
    return (f"=== {market} {sym} ===\n"
            + json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def main(argv: list[str]) -> str:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    market = args[0].upper() if args and args[0].upper() in DEFAULTS else "US"
    symbols = [a for a in args if a.upper() not in DEFAULTS] or DEFAULTS[market]
    render = dump_json if as_json else summarize
    return "\n".join(render(market, s) for s in symbols)


if __name__ == "__main__":
    text = main(sys.argv[1:])
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text)
    except Exception:
        out_path = Path(__file__).resolve().parent / "check_etf_output.txt"
        out_path.write_text(text, encoding="utf-8")
        print(f"(콘솔 출력 실패 - {out_path} 에 UTF-8로 저장했습니다)")
