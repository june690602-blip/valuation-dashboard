"""미국 계수가 왜 못 만들어지나 — 야후의 어느 통로가 살아 있는지 잰다.

    python scripts/check_us_sources.py                 # 집 PC(기준선)
    Actions → "Probe US sources" → Run workflow        # 깃허브 러너(문제의 자리)

## 왜 이 스크립트가 필요한가

`collect_us`는 종목마다 **`_info_metrics()` 한 번**을 부르고 거기서 per·pbr·psr·
ev_ebitda·시총·roe를 **전부** 꺼낸다. 그 호출이 비면 여섯 개가 동시에 `None`이 되고
다리가 0개가 된다. 한국은 per·pbr을 네이버에서 따로 받아 야후가 죽어도 **얇아질 뿐**인데,
미국은 받쳐 주는 원천이 없다(CLAUDE.md · ADR-0049).

야후가 **깃허브 러너 IP에서만** `info`를 빈 값으로 준다는 것까지는 알고 있다. 모르는 것은
**다른 통로도 같이 막히는가**이다. `.info`는 quoteSummary API이고, 주가(`history`)와
재무제표(`income_stmt`)는 **다른 API**다. 뒤의 둘이 살아 있으면 배수를 직접 만들 수 있다:

    시총 = 주가 × 주식수        (주가=차트 API · 주식수=손익계산서의 Basic Average Shares)
    PER  = 시총 / 순이익
    PBR  = 시총 / 자본총계
    PSR  = 시총 / 매출

**막힌 통로에 대체재를 붙이면 헛일이므로, 만들기 전에 잰다.**

## 무엇을 판정하나

각 통로를 따로 두드려 보고, 마지막에 **위 레시피가 실제로 돌아가는지**까지 계산해 본다.
통로 하나하나가 살아 있는 것보다 **레시피가 끝까지 도는 것**이 중요하다.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8")

TICKERS = ["AAPL", "MSFT", "JNJ", "KO", "CAT"]
OK, BAD = "[확인]", "[문제]"


def _probe(name: str, fn):
    """통로 하나를 두드린다. 예외는 여기서 잡아 표 한 줄로 만든다."""
    try:
        got, note = fn()
        print(f"  {OK if got else BAD} {name:<28} {note}")
        return got
    except Exception as e:  # noqa: BLE001 — 어느 통로가 어떻게 죽는지가 이 스크립트의 결과다
        print(f"  {BAD} {name:<28} {type(e).__name__}: {str(e)[:90]}")
        return False


def _n(df) -> int:
    return 0 if df is None else int(getattr(df, "shape", [0])[0] or 0)


def main() -> int:
    import yfinance as yf

    print("=" * 78)
    print("■ 야후 통로별 생사 — 미국 계수가 못 만들어지는 원인을 가른다")
    print("=" * 78)
    print(f"yfinance {yf.__version__} · python {sys.version.split()[0]}")
    try:
        import curl_cffi
        print(f"curl_cffi {curl_cffi.__version__} (브라우저 위장 — 지문이 아니라 IP가 문제인지 본다)")
    except Exception:
        print("curl_cffi 없음")

    # ── 1. 통로별로 두드린다 ────────────────────────────────────────────────
    t = TICKERS[0]
    tk = yf.Ticker(t)
    print(f"\n■ 통로별 ({t})")

    alive = {}
    alive["info"] = _probe(".info  (quoteSummary)", lambda: (
        (lambda d: (len([1 for v in (d or {}).values() if v is not None]) > 5,
                    f"값 {len([1 for v in (d or {}).values() if v is not None])}개"))(tk.info)))

    def _fast():
        f = tk.fast_info
        px = f.get("lastPrice") if hasattr(f, "get") else getattr(f, "last_price", None)
        sh = f.get("shares") if hasattr(f, "get") else getattr(f, "shares", None)
        return (px is not None, f"lastPrice={px} · shares={sh}")
    alive["fast_info"] = _probe(".fast_info  (chart)", _fast)

    alive["history"] = _probe(".history(5d)  (chart)", lambda: (
        (lambda h: (_n(h) > 0, f"{_n(h)}행 · 종가={None if not _n(h) else round(float(h['Close'].iloc[-1]), 2)}"))(
            tk.history(period="5d", auto_adjust=False))))

    alive["income"] = _probe(".income_stmt  (fundamentals)", lambda: (
        (lambda d: (_n(d) > 0, f"{getattr(d, 'shape', None)}"))(tk.income_stmt)))

    alive["balance"] = _probe(".balance_sheet  (fundamentals)", lambda: (
        (lambda d: (_n(d) > 0, f"{getattr(d, 'shape', None)}"))(tk.balance_sheet)))

    alive["download"] = _probe("yf.download 배치 (chart)", lambda: (
        (lambda d: (_n(d) > 0, f"{_n(d)}행 · {len(TICKERS)}종목 한 번에"))(
            yf.download(TICKERS, period="5d", progress=False, auto_adjust=False))))

    # ── 2. 지금 쓰는 경로가 실제로 몇 종목에서 비는가 ────────────────────────
    print(f"\n■ 지금 쓰는 경로 (`fetch_info_metrics`) — {len(TICKERS)}종목")
    from src.data.base import fetch_info_metrics
    filled = 0
    for s in TICKERS:
        try:
            m = fetch_info_metrics(s) or {}
            legs = {k: m.get(k) for k in ("per", "pbr", "psr", "ev_ebitda")}
            got = sum(v is not None for v in legs.values())
            filled += got > 0
            print(f"  {OK if got else BAD} {s:<6} 다리 {got}/4  " +
                  " · ".join(f"{k}={v}" for k, v in legs.items()))
        except Exception as e:  # noqa: BLE001
            print(f"  {BAD} {s:<6} {type(e).__name__}: {str(e)[:60]}")
    print(f"  → {filled}/{len(TICKERS)}종목에서 다리를 하나라도 얻었다"
          f"{'  ← 이게 0이면 계수가 안 만들어진다' if not filled else ''}")

    # ── 3. 대체 레시피가 끝까지 도는가 (이게 진짜 질문이다) ──────────────────
    print("\n■ 대체 레시피 — `info` 없이 재무제표+주가만으로 배수를 만들 수 있나")
    print("  시총 = 주가 × 주식수 · PER = 시총/순이익 · PBR = 시총/자본 · PSR = 시총/매출")
    from src.data.base import extract_financials
    made = 0
    for s in TICKERS:
        try:
            tkx = yf.Ticker(s)
            fin, _w = extract_financials(tkx)
            h = tkx.history(period="5d", auto_adjust=False)
            if fin is None or fin.empty or not _n(h):
                print(f"  {BAD} {s:<6} 재무 {0 if fin is None else len(fin)}행 · 주가 {_n(h)}행")
                continue
            px = float(h["Close"].iloc[-1])
            last = fin.iloc[-1]

            def _f(col):
                v = last.get(col)
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return None
                return v if v == v and v != 0 else None   # NaN·0 제외

            sh, ni, eq, rev = _f("shares_outstanding"), _f("net_income"), _f("total_equity"), _f("revenue")
            if not sh:
                print(f"  {BAD} {s:<6} 주식수를 못 얻었다 (재무 열: {list(fin.columns)[:6]}…)")
                continue
            mcap = px * sh
            per = mcap / ni if ni and ni > 0 else None
            pbr = mcap / eq if eq and eq > 0 else None
            psr = mcap / rev if rev and rev > 0 else None
            got = sum(v is not None for v in (per, pbr, psr))
            made += got > 0
            fmt = lambda v: "—" if v is None else f"{v:.2f}"  # noqa: E731
            print(f"  {OK if got else BAD} {s:<6} 다리 {got}/3  "
                  f"per={fmt(per)} · pbr={fmt(pbr)} · psr={fmt(psr)}   "
                  f"(주가 {px:.2f} × 주식수 {sh:,.0f})")
        except Exception as e:  # noqa: BLE001
            print(f"  {BAD} {s:<6} {type(e).__name__}: {str(e)[:70]}")
            if "--trace" in sys.argv:
                traceback.print_exc()
    print(f"  → {made}/{len(TICKERS)}종목에서 대체 레시피가 배수를 만들었다")

    # ── 판정 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("■ 판정")
    if filled:
        print("  야후 info가 여기서는 **살아 있다.** 이 자리는 문제의 자리가 아니다"
              " — 러너에서 돌려야 뜻이 있다.")
    elif made:
        print("  **info는 죽었는데 대체 레시피는 돈다.** 받쳐 주는 원천을 붙이면 해결된다.")
        print("  붙일 곳: `src/data/universe_multiples.py`의 `collect_us`"
              " — `_info_metrics` 하나에 매달린 구조를 깬다.")
    else:
        print("  **둘 다 죽었다.** 야후가 이 IP에서 통째로 막혔다는 뜻이므로")
        print("  대체 레시피로는 안 풀린다 → 다른 원천(예: SEC EDGAR)이나 다른 실행 장소가 필요하다.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
