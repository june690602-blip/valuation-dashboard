"""배수의 두 정의를 대조한다 — 「현재」와 「업종 중앙값」이 같은 자를 쓰는가 (#86).

    python scripts/check_multiple_definition.py            # 기본 표본
    python scripts/check_multiple_definition.py KR 005930  # 한 종목만

## 무엇을 재나

밸류에이션 탭의 한 행은 이렇게 생겼다.

    PER    현재 14.5×    업종 중앙값 11.7×    vs 업종 24% 높음

**「현재」와 「업종 중앙값」의 출처가 다르다.**

| 자리 | 어디서 오나 | 무엇인가 |
|---|---|---|
| 「현재」 | `indicators.compute_indicators` | **우리가 계산한다** — 주가 ÷ 자체 TTM EPS 등 |
| 「업종 중앙값」 | 피어 프레임(`base.fetch_info_metrics`) | **제공자 공시값** — 야후 `trailingPE` 등 |

자사도 피어 프레임에 한 행(`is_self`)으로 들어 있어서, **같은 회사의 같은 지표가 두 값**으로
존재한다. 그 둘이 다르면 위 비교는 기울어져 있고, 그 비율이 `cheaper` 판정과 `vs` 퍼센트를
만든다(`serialize.py::_multiples`).

이 스크립트는 종목마다 두 값을 나란히 놓고 **차이가 얼마나 되는지**, 그리고 그 차이가
**판정을 뒤집는지**(싸다↔비싸다) 센다. 네트워크가 필요하므로 CI가 아니라 수동 계열이다.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.analysis.indicators import compute_indicators
from src.analysis.scoring import peer_median, sanitize_peer_frame

# _multiples가 한 행씩 그리는 지표 전부. PER만의 문제인지 전부인지가 이 이슈의 열린 질문이다.
KEYS = ("per", "pbr", "psr", "ev_ebitda", "p_fcf", "div_yield", "peg")

# 대형·소형·미국·적자를 섞는다. 한 종목만 보면 "삼성전자만 그런가"를 못 가른다.
SAMPLE = [("KR", "005930"), ("KR", "035420"), ("KR", "042700"),
          ("US", "AAPL"), ("US", "INTC")]


def load(market: str, code: str):
    if market == "KR":
        from src.data.kr_provider import KRProvider
        return KRProvider().load(code, peer_count=10)
    from src.data.us_provider import USProvider
    return USProvider().load(code, peer_count=10)


def one(market: str, code: str) -> list[dict]:
    d = load(market, code)
    ind = compute_indicators(d)
    peers = sanitize_peer_frame(d.peers)
    self_row = None
    if not peers.empty and "is_self" in peers.columns and peers["is_self"].any():
        self_row = peers[peers["is_self"]].iloc[0]

    print(f"\n{'═' * 76}")
    print(f"{market} {code}  {getattr(d, 'name', '') or ''}")
    print(f"{'═' * 76}")
    print(f"  {'지표':<12}{'현재(자체계산)':>16}{'피어프레임 자사':>16}{'차이':>10}"
          f"{'업종중앙':>12}{'판정 뒤집힘':>12}")

    rows = []
    for key in KEYS:
        cur = ind.valuation.get(key)                     # 「현재」가 쓰는 값
        own = None if self_row is None else self_row.get(key)
        med = peer_median(peers, key)
        own = None if own is None or own != own else float(own)   # NaN 제거

        gap = None
        if cur is not None and own not in (None, 0):
            gap = cur / own - 1

        # 두 정의로 각각 판정하면 결론이 갈리는가 (div_yield만 부호가 반대)
        flip = None
        if med and cur is not None and own is not None:
            a = (cur / med - 1 < 0) if key != "div_yield" else (cur / med - 1 > 0)
            b = (own / med - 1 < 0) if key != "div_yield" else (own / med - 1 > 0)
            flip = a != b

        rows.append({"market": market, "code": code, "key": key,
                     "cur": cur, "own": own, "med": med, "gap": gap, "flip": flip})
        f = lambda v, s="—": s if v is None else f"{v:,.2f}"
        print(f"  {key:<12}{f(cur):>16}{f(own):>16}"
              f"{('—' if gap is None else f'{gap * 100:+.1f}%'):>10}"
              f"{f(med):>12}{('—' if flip is None else ('예' if flip else '아니오')):>12}")
    return rows


def main() -> int:
    args = sys.argv[1:]
    sample = [(args[0].upper(), args[1])] if len(args) >= 2 else SAMPLE

    all_rows, failed = [], []
    for market, code in sample:
        try:
            all_rows += one(market, code)
        except Exception as e:                       # 한 종목이 막혀도 나머지는 본다
            failed.append(f"{market} {code}: {type(e).__name__}: {e}")
            print(f"\n[불가] {market} {code} — {type(e).__name__}: {e}")

    print(f"\n{'═' * 76}")
    print("요약")
    print(f"{'═' * 76}")
    print(f"  {'지표':<12}{'두 값이 있는 종목':>18}{'다른 종목':>12}{'최대 차이':>12}{'판정 뒤집힘':>12}")
    for key in KEYS:
        rs = [r for r in all_rows if r["key"] == key and r["gap"] is not None]
        diff = [r for r in rs if abs(r["gap"]) > 0.005]      # 0.5%p 넘으면 '다르다'
        flips = [r for r in rs if r["flip"]]
        worst = max((abs(r["gap"]) for r in rs), default=None)
        print(f"  {key:<12}{len(rs):>18}{len(diff):>12}"
              f"{('—' if worst is None else f'{worst * 100:.1f}%'):>12}{len(flips):>12}")

    n_diff = len({(r['market'], r['code'], r['key']) for r in all_rows
                  if r["gap"] is not None and abs(r["gap"]) > 0.005})
    n_flip = len([r for r in all_rows if r["flip"]])
    print(f"\n  두 정의가 갈린 자리 {n_diff}곳 · 그중 판정까지 뒤집힌 자리 {n_flip}곳")
    if failed:
        print("\n  수집 실패:")
        for f in failed:
            print(f"    {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
