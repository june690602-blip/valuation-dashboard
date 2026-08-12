"""첫 조회가 어디서 시간을 쓰는가 — 단계별 실측 (docs/HANDOFF-PERF.md).

    python scripts/check_load_timing.py KR 005930
    python scripts/check_load_timing.py US AAPL
    python scripts/check_load_timing.py KR 161890 --no-news

**네트워크가 필요하다.** CI 관문이 아니라 수동 진단이다.

## 왜 이 스크립트가 있나

"느리다"는 말로는 아무것도 고칠 수 없고, "빨라졌다"는 말은 증명할 수 없다. 이 저장소는
재현 수단 없는 숫자로 이미 두 번 데였다 — 인계문의 가중치 폭(ADR-0041)과 신뢰도 IC
표(ADR-0043)가 둘 다 재현되지 않았고 원인도 못 밝혔다. **성능 숫자도 같은 규칙을 따른다:
표를 인용하려거든 그 표를 다시 내는 명령을 함께 적는다.**

## 어떻게 재나

호출부를 **한 줄도 건드리지 않는다.** 데이터 계층의 함수들을 타이머로 감싸(monkeypatch)
호출 수와 누적 시간을 세고, 끝나면 원래대로 둘 필요도 없이 프로세스가 죽는다.

`from X import y`로 이름을 당겨 간 모듈이 있으므로(예: `kr_provider`가 `base`의 함수들을
직접 임포트한다) **양쪽 모듈에 모두 심는다.** 한 쪽만 감싸면 0.00초로 나와 "이 단계는
공짜"라는 반대 결론을 낸다 — 이 스크립트를 처음 쓸 때 실제로 그랬다.

## 읽는 법

- **`serialize._pipeline`과 `serialize._news`의 합이 총 시간에 가깝다**면 그 둘이 순차라는
  뜻이다(지금이 그렇다). 병렬로 묶으면 합이 아니라 최댓값이 총 시간이 된다.
- **순수 계산**(`compute_*`·`build_commentary`)이 0.0x초라면 알고리즘을 손댈 이유가 없다.
  남는 것은 전부 네트워크 대기다.
- 같은 종목이라도 **캐시 상태에 따라 병목이 바뀐다.** 한 종목만 재고 결론짓지 마라.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

STATS: dict[str, list] = {}


def _wrap(mod, name: str, label: str | None = None) -> None:
    """모듈 속성 하나를 타이머로 감싼다. 없으면 조용히 넘어간다(시장별로 다르다)."""
    fn = getattr(mod, name, None)
    if fn is None or not callable(fn):
        return
    label = label or f"{mod.__name__.split('.')[-1]}.{name}"
    STATS.setdefault(label, [0, 0.0])

    def inner(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            s = STATS[label]
            s[0] += 1
            s[1] += time.perf_counter() - t

    inner.__name__ = getattr(fn, "__name__", name)
    setattr(mod, name, inner)


# 데이터 계층에서 실제로 네트워크를 타는 함수들. 이름은 원본 모듈 기준이다.
_DATA_TARGETS = [
    ("src.data.base", ["extract_financials", "extract_ttm", "fetch_prices",
                       "fetch_prices_raw", "fetch_price_frame", "fetch_index_prices",
                       "fetch_price_pair", "build_peer_table", "fetch_info_metrics",
                       "fetch_company_profile"]),
    # 병렬 그룹의 **벽시계 시간**. 이 값이 그룹 구성원들의 합보다 한참 작으면 실제로
    # 겹쳐 돌았다는 뜻이고, 합에 가까우면 어딘가에서 줄을 서 있다는 뜻이다(ADR-0045).
    ("src.data.parallel", ["gather"]),
    ("src.data.naver", ["fetch_naver_fundamental", "fetch_company_overview"]),
    ("src.data.opendart", ["get_dart_financials"]),
    ("src.data.news", ["fetch_news", "fetch_topic_news", "_google_news"]),
    ("src.data.universe", ["get_kr_listing", "select_peers_kr", "select_peers_us",
                           "get_sp500", "get_us_etf", "get_kr_etf"]),
    ("src.data.universe_multiples", ["coefficients_or_none"]),
]

# 순수 계산 — **0에 가까워야 한다.** 아니면 병목이 네트워크가 아니라는 뜻이다.
_PURE_TARGETS = [
    ("src.analysis.indicators", ["compute_indicators"]),
    ("src.analysis.scoring", ["compute_scores"]),
    ("src.analysis.capital_cost", ["compute_capital_cost"]),
    ("src.analysis.valuation", ["compute_valuation"]),
    ("src.analysis.commentary", ["build_commentary"]),
]

# provider가 `from .base import ...`로 당겨 간 이름들 — 여기도 심어야 실제로 걸린다.
_PROVIDER_REEXPORTS = [
    "extract_financials", "extract_ttm", "fetch_price_pair",
    "fetch_index_prices", "build_peer_table", "fetch_naver_fundamental",
    "get_dart_financials", "get_kr_listing", "select_peers_kr", "fetch_info_metrics",
    # provider가 `from .parallel import gather`로 당겨 갔다 — 여기 안 심으면 병렬 그룹의
    # 벽시계 시간이 표에서 통째로 빠진다(이 목록이 존재하는 이유 그 자체다).
    "gather",
]


def _install(market: str) -> None:
    import importlib

    for mod_name, names in _DATA_TARGETS + _PURE_TARGETS:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        for n in names:
            _wrap(mod, n)

    prov_name = "src.data.kr_provider" if market == "KR" else "src.data.us_provider"
    prov = importlib.import_module(prov_name)
    short = prov_name.split(".")[-1]
    for n in _PROVIDER_REEXPORTS:
        _wrap(prov, n, f"{short}.{n}")
    # Gemini 업종분류 — 단일 최대 병목일 때가 많은데 provider 안의 비공개 함수라
    # 위 목록으로는 안 걸린다. 이름을 명시해 따로 심는다.
    _wrap(prov, "_ai_classify_kr" if market == "KR" else "_ai_classify_us",
          f"{short}.AI 업종분류(Gemini)")

    ser = importlib.import_module("src.web.serialize")
    for n in ["_load", "_pipeline", "_news", "_gather_news_items", "_company"]:
        _wrap(ser, n, f"serialize.{n}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("market", nargs="?", default="KR", choices=["KR", "US"])
    ap.add_argument("query", nargs="?", default="005930")
    ap.add_argument("--peers", type=int, default=9)
    ap.add_argument("--no-news", action="store_true",
                    help="뉴스를 빼고 잰다 — 파이프라인만의 시간을 보고 싶을 때")
    args = ap.parse_args()

    _install(args.market)
    from src.web.serialize import analyze

    t0 = time.perf_counter()
    data = analyze(args.market, args.query, peer_count=args.peers,
                   include_news=not args.no_news)
    total = time.perf_counter() - t0

    name = data.get("meta", {}).get("name", args.query)
    print(f"\n=== {args.market} {args.query} → {name} · 총 {total:.2f}s ===")
    print(f"{'단계':<44}{'호출':>6}{'초':>9}{'%':>7}")
    print("─" * 66)
    rows = sorted(((k, v) for k, v in STATS.items() if v[0]), key=lambda kv: -kv[1][1])
    for label, (n, sec) in rows:
        if sec < 0.005:
            continue
        print(f"{label:<44}{n:>6}{sec:>9.2f}{sec / total * 100:>6.1f}%")

    pure = sum(STATS.get(f"{m.split('.')[-1]}.{n}", [0, 0.0])[1]
               for m, names in _PURE_TARGETS for n in names)
    print("─" * 66)
    print(f"{'순수 계산 합계 (네트워크 아님)':<44}{'':>6}{pure:>9.2f}{pure / total * 100:>6.1f}%")
    print("\n※ `_pipeline`과 `_news`의 합이 총 시간에 가까우면 그 둘이 순차라는 뜻이다.")
    print("※ 캐시 상태에 따라 병목이 바뀐다 — 한 종목만 재고 결론짓지 마라(HANDOFF-PERF.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
