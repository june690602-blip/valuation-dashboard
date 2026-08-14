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


def _has_substance(m) -> bool:
    """`base._info_has_substance`와 같은 판정 — 야후가 레이트리밋으로 주는 '빈 성공'을 거른다."""
    return isinstance(m, dict) and any(
        m.get(k) is not None for k in ("marketCap", "currentPrice", "trailingPE", "priceToBook"))


def _sweep(yf, tickers, workers, gap=0.0, mark=50):
    """`.info`를 직접 부르며 `mark`개 단위로 실속 비율을 찍는다 (우리 캐시를 타지 않는다)."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        if gap:
            time.sleep(gap)
        try:
            return _has_substance(yf.Ticker(t).info)
        except Exception:  # noqa: BLE001 — 실패도 '못 얻었다'로 센다
            return False

    got, block, marks = 0, 0, []
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for i, ok in enumerate(ex.map(one, tickers), 1):
            got += ok
            block += ok
            if i % mark == 0:
                marks.append(f"{block}/{mark}")
                block = 0
    if len(tickers) % mark:
        marks.append(f"{block}/{len(tickers) % mark}")
    print(f"    {mark}개 단위 실속: {' · '.join(marks)}")
    print(f"    합계 {got}/{len(tickers)} ({got / len(tickers):.0%}) · {time.time() - t0:.0f}초")
    return got / len(tickers)


def _refresh(level: int) -> str:
    """야후 인증 토큰(crumb)을 되살린다. 단계를 올려 가며 무엇이 필요한지 가른다.

    `.info`(quoteSummary)는 **crumb**이라는 토큰을 요구하고 yfinance는 그것을 싱글턴
    `YfData`에 들고 있다. 러너 실측(2026-08-14): 803종목까지 100%로 되다가 그 뒤
    **전부 401 `Invalid Crumb`**이 됐다. 토큰이 죽었는데 yfinance가 다시 받지 않는 것이다.

    `_get_cookie_basic`이 ㉠ 메모리의 `_cookie` → ㉡ **디스크에 저장된 쿠키** 순으로
    재사용하므로, 메모리만 비우면 낡은 쿠키를 디스크에서 되읽어 온다. 그래서 단계를 나눈다.
    """
    from yfinance import data as D
    d = D.YfData()
    d._crumb = None                                    # 1단계: 토큰만 버린다
    if level >= 2:
        d._cookie = None                               # 2단계: 쿠키도 버린다
        d._logged_in = False
        try:
            d._session.cookies.clear()
        except Exception:  # noqa: BLE001 — 없으면 없는 대로 간다
            pass
    if level >= 3:
        try:                                           # 3단계: 디스크에 저장된 쿠키까지
            from yfinance import cache as C
            C.get_cookie_cache().store("curlCffi", {})
        except Exception:  # noqa: BLE001
            pass
    return {1: "토큰만", 2: "토큰+쿠키+세션", 3: "토큰+쿠키+세션+디스크"}[level]


def _recover_test(yf, syms) -> None:
    """죽은 뒤에 되살릴 수 있는가 — 이게 되면 고칠 방법이 정해진다."""
    print("\n■ 죽은 토큰을 되살릴 수 있는가 (위에서 이미 401이 난 상태다)")
    for level in (1, 2, 3):
        what = _refresh(level)
        print(f"  {level}단계 — {what} 를 버리고 100종목 다시")
        rate = _sweep(yf, syms[:100], workers=12, mark=100)
        if rate >= 0.8:
            print(f"    → **{level}단계로 살아난다({rate:.0%}).** 고칠 방법은 이것이다.")
            return
    print("  → 세 단계 다 못 살렸다. 토큰 갱신 말고 다른 수가 필요하다.")
    _after_cliff_recipe(yf, syms)


def _after_cliff_recipe(yf, syms) -> None:
    """절벽 **이후에** 대체 레시피가 사는가 — 이 답이 곧 해법이다.

    `.info`는 quoteSummary API라 crumb 토큰을 요구하고 그것이 죽었다. 그런데 주가는
    **chart API**, 재무제표는 **fundamentals-timeseries API**로 **다른 통로**다.
    토큰이 죽은 지금도 그 둘이 살아 있다면, 시총 = 주가 × 주식수로 배수를 직접 만들어
    `.info` 없이 계수를 세울 수 있다.
    """
    from src.data.base import extract_financials
    print("\n■ 절벽 **이후에** 대체 레시피가 사는가 (토큰이 죽은 지금 상태에서 20종목)")
    made = fin_ok = px_ok = 0
    sample = syms[900:920]        # 절벽 뒤 구간에서 고른다
    for s in sample:
        try:
            tk = yf.Ticker(s)
            h = tk.history(period="5d", auto_adjust=False)
            px_ok += _n(h) > 0
            fin, _w = extract_financials(tk)
            fin_ok += fin is not None and not fin.empty
            if fin is None or fin.empty or not _n(h):
                continue
            last = fin.iloc[-1]

            def _f(col, row=None):
                v = (row if row is not None else last).get(col)
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return None
                return v if v == v and v != 0 else None

            sh, ni, eq, rev = (_f("shares_outstanding"), _f("net_income"),
                               _f("total_equity"), _f("revenue"))
            if not sh or float(h["Close"].iloc[-1]) <= 0:
                continue   # 시총 = 주가 × 주식수 — 둘 다 있어야 배수가 나온다
            made += sum(x is not None and x > 0 for x in (ni, eq, rev)) > 0
        except Exception:  # noqa: BLE001 — 실패도 '못 얻었다'로 센다
            pass
    n = len(sample)
    print(f"  주가(chart) {px_ok}/{n} · 재무제표(fundamentals) {fin_ok}/{n}"
          f" · **배수를 만든 종목 {made}/{n}**")
    if made >= n * 0.8:
        print("  → **토큰이 죽어도 대체 레시피는 산다.** 이것이 해법이다 —"
              " `collect_us`가 `.info` 하나에 매달린 구조를 깨고 이 경로로 받치면 된다.")
    elif px_ok >= n * 0.8:
        print("  → 주가는 살지만 재무제표가 죽었다. 재무는 SEC EDGAR 같은 다른 원천이 필요하다.")
    else:
        print("  → 이 통로도 죽었다. 야후 전체가 이 IP에서 막힌 상태다"
              " — 그렇다면 **부르는 양을 줄이는 것**이 유일한 수다.")


def _volume_test(yf) -> str | None:
    """많이 부르면 죽는가 — IP 차단과 레이트리밋을 가르는 실험."""
    try:
        from src.data.universe import get_sp1500
        uni = get_sp1500()
        col = "Symbol" if "Symbol" in uni.columns else uni.columns[0]
        syms = [str(s) for s in uni[col].tolist()]
    except Exception as e:  # noqa: BLE001
        print(f"\n{BAD} 유니버스를 못 받았다: {type(e).__name__}: {str(e)[:70]}")
        return None
    if len(syms) < 350:
        print(f"\n{BAD} 유니버스가 {len(syms)}개뿐이라 양 실험을 못 한다.")
        return None

    # 355호출로는 안 죽었다(2026-08-14 러너 실측 100% · 4초). 실제 빌드는 KR 2,700을
    # 먼저 훑고 그 다음 US 1,506을 치므로 **한 작업에서 4,200번**을 부른다. 그러니
    # **유니버스 전체**로 올려 본다 — 무너지는 지점이 곧 원인이다.
    print(f"\n■ 양을 늘리면 죽는가 — 유니버스 **{len(syms):,}종목 전체**를 지금 설정으로 훑는다")
    print("  (워커 12 · 계수 빌드가 하는 방식 그대로 · 250개 단위로 실속을 찍는다)")
    a = _sweep(yf, syms, workers=12, mark=250)

    if a >= 0.8:
        print(f"\n  → **전체 {len(syms):,}종목이 멀쩡하다({a:.0%}).** 양만으로는 안 죽는다"
              " — 계수 빌드와 이 탐침의 **다른 차이**를 찾아야 한다(KR이 먼저 도는 것 등).")
        return "volume-ok"
    print(f"\n  → **전체에서는 {a:.0%}로 무너진다.** 블록별 숫자에서 무너진 지점을 보라 —"
          " 앞이 100%이고 뒤가 0%인 **절벽**이면 레이트리밋이 아니라 **토큰이 죽은 것**이다"
          " (401 `Invalid Crumb`).")
    _recover_test(yf, syms)
    return "crumb"


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

    # ── 4. 양을 늘리면 죽는가 — IP 차단인가 레이트리밋인가 ───────────────────
    #
    # 2026-08-14 러너 실측에서 **5종목은 전부 성공**했는데 같은 날 계수 빌드(1,506종목)는
    # 다리 0개였다. CLAUDE.md는 *"러너 IP에서 빈 값을 준다"*고 적었지만, IP가 원인이면
    # 5종목도 비어야 한다. 코드 주석은 다른 말을 한다 —
    # `_info_has_substance`가 *"야후가 **레이트리밋으로** 빈 info를 성공처럼 줄 때를
    # 걸러낸다"*이다. **둘 중 어느 쪽인지 여기서 가른다.**
    #
    # 캐시를 타지 않으려고 `yf.Ticker().info`를 직접 부른다 — 우리 캐시가 아니라
    # 야후를 재야 한다.
    vol = _volume_test(yf)

    # ── 판정 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("■ 판정")
    if vol == "crumb":
        print("  **IP 차단도 레이트리밋도 아니다 — 야후 인증 토큰(crumb)이 도중에 죽는다.**")
        print("  → 고칠 곳은 원천이 아니라 **토큰을 다시 받는 것**이다.")
        print("    지금은 KR(2,700종목)이 먼저 훑으면서 토큰을 태우고, US 차례에는 이미 죽어 있다.")
    elif vol == "blocked":
        print("  **적은 양에서도 빈다 — IP 차단으로 보인다.** 대체 원천이 필요하다.")
    elif filled:
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
