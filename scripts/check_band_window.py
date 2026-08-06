"""② 역사적 밴드의 창을 몇 년으로 둘 것인가 — **처음으로 근거를 만든다**.

    python scripts/check_band_window.py --limit 80

## 왜 이 진단이 있나

**지금 5년인 이유가 저장소 어디에도 없다.** `base.py`의 `fetch_price_frame(..., period="5y")`
기본 인자이고, 밴드 창을 5년으로 정한 ADR이 없다. 오히려 ADR-0012는 *"창이 '5년'이
아니었다"*(실측 2.84~4.99년)를 발견하고 **라벨을 실측값으로 바꿨다** — 창을 정한 것이
아니라 거짓 라벨을 지운 것이다. 근거가 적힌 유일한 창 결정은 하한 `BAND_MIN_YEARS = 3.0`인데
그것도 코드가 스스로 *"'자기 과거 분위'라 부르려면 이만큼은 봐야 한다"*는 **판단값**이라고
밝히고 있다.

DART 6년 · 주가 5년과 **같은 종류의 착각**이다(ADR-0025). 세 번째다.

## 기준을 먼저 고정한다 — 재고 나서 고르지 않는다

ADR-0024가 안았던 이해충돌(원하는 답이 나오게 기준을 고르는 것)을 피하려면 순서가
전부다. **이 세 기준과 선택 규칙은 한 줄도 재기 전에 적었다.**

  C1. **②의 자기 실패 모드가 줄어야 한다.** ADR-0012가 정한 `corr(log배수, log주가) ≥ 0.90`
      탈락률이 창이 길수록 낮아지는가. 펀더멘털이 움직일 시간을 주는 것이 이 축의
      전제이므로, 이것은 밖에서 빌려온 기준이 아니라 **이 축이 스스로 정한 기준**이다.
  C2. **커버리지를 크게 깎지 않아야 한다.** 창이 길수록 이력이 모자란 종목이 빠진다.
  C3. **창에 덜 휘둘려야 한다.** 창을 한 해 뒤로 밀었을 때 밴드의 중심 배수가 얼마나 튀나.

  **선택 규칙**: C1의 개선이 **꺾이는 첫 지점**(더 늘려도 탈락률이 거의 안 주는 곳)을
  고른다. 그 지점에서 C2 손실이 C1 이득보다 크면 **한 단계 짧은 값**을 고른다.

## 무엇을 재는가

`_band`가 하는 일을 그대로 재현한다 — 미조정 주가 ÷ 일별 펀더멘털(회계연도 종료 +90일에
계단으로 붙이고 ffill). 다른 점은 **주가와 재무를 깊게 받는다**는 것뿐이다:

  주가 — `period='max'` (005930은 2000년부터 26년)
  재무 — DART 사다리 3년 간격 (중앙 13년). `HISTORY_YEARS`(8)가 자르기 전의 원본이다

**PER 다리만 잰다.** DART가 `eps`를 직접 주므로 `shares_outstanding` 없이 선다.
PBR 다리는 연도별 주식수가 필요한데 그쪽이 결측이 흔해(ADR-0012: 삼성전자 2020·2021)
창 길이가 아니라 결측이 결과를 정한다 — 섞으면 무엇을 쟀는지 모르게 된다.

**KR만 잰다.** 미국은 yfinance 연간 재무가 4행이라 창을 무엇으로 두든 밴드가 4년을
못 넘는다(AAPL 실측 3.59년). 미국의 창은 우리가 고르는 값이 아니라 데이터가 정하는
값이고, 그것을 바꾸는 일은 SEC EDGAR를 붙이는 작업이다(`docs/HANDOFF-BACKTEST.md`).

네트워크와 `OPENDART_API_KEY`가 필요하다. CI가 아니라 수동 계열이다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))    # check_window_tradeoff 재사용
sys.stdout.reconfigure(encoding="utf-8")

import FinanceDataReader as fdr                                        # noqa: E402

from check_window_tradeoff import _deep_frame                          # noqa: E402
from src.analysis.valuation import (BAND_CORR_LIMIT,                   # noqa: E402
                                    BAND_MIN_OBS, BAND_MIN_YEARS,
                                    _band_quality)
from src.data.opendart import get_api_key, get_corp_code_map           # noqa: E402
from src.data.universe import get_kr_listing                           # noqa: E402

WINDOWS = (3, 4, 5, 6, 7, 8, 10)
LAG_DAYS = 90        # 회계연도 종료 후 공시까지. `_fundamental_daily`와 같은 값이다.


def _deep_prices(code: str) -> pd.Series | None:
    """깊은 **미조정** 일별 종가 (KRX 공식, FinanceDataReader).

    **제품 코드는 yfinance를 쓰는데 이 진단만 fdr을 쓴다.** 이유 둘:

    1. **yfinance가 대량 연속 호출에서 빈 응답을 준다.** 실측: 10종목을 잇달아 부르면
       전부 *"possibly delisted; no price data found"*로 돌아오고, 한참 뒤 하나만 부르면
       6,524행이 온다. 재시도로는 안 풀린다(스로틀이 몇 분 단위로 걸린다). 그대로 쓰면
       **표본이 조용히 비고** 그것을 '데이터가 없다'로 잘못 읽게 된다 — 이 저장소가
       이미 세 번 한 착각이다.
    2. **fdr의 Close는 KRX 공식 종가라 미조정이다.** `actual_prices`가 요구하는 바로
       그 값이다(수정종가를 쓰면 과거 배수가 배당 누적분만큼 낮게 깔린다).

    **한계**: fdr은 3,000행에서 끊는다(2014-05~, 약 12년). 그래서 창 후보를 10년까지만 둔다.
    이 진단이 재는 것은 '창을 몇 년으로 둘까'이지 '주가를 어디서 받을까'가 아니므로,
    제품 경로를 바꾸는 것은 이 진단의 결론이 정해진 뒤에 할 일이다.
    """
    try:
        df = fdr.DataReader(code, "2000-01-01")
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None
    s = pd.to_numeric(df["Close"], errors="coerce").dropna()
    s.index = pd.to_datetime(s.index)
    return s if len(s) >= BAND_MIN_OBS else None


def _multiple_series(fin: pd.DataFrame, px: pd.Series) -> tuple[pd.Series, pd.Series] | None:
    """(배수 시리즈, 일별 EPS) — `_fundamental_daily` + `_band`의 앞부분을 그대로 재현."""
    if fin is None or fin.empty or "eps" not in fin.columns:
        return None
    fin = fin.copy()
    # 깊은 프레임에는 `fiscal_end`가 없을 수 있다 — `_parse_report`는 보고서에서 날짜를
    # 찾을 때만 넣고, 12월 말 폴백은 `_dart_financials_df` 쪽에 있다. 같은 폴백을 쓴다.
    fallback = pd.Series([pd.Timestamp(int(y), 12, 31) for y in fin.index], index=fin.index)
    fin["fiscal_end"] = (pd.to_datetime(fin["fiscal_end"], errors="coerce").fillna(fallback)
                         if "fiscal_end" in fin.columns else fallback)
    vals = fin[["eps", "fiscal_end"]].dropna()
    if len(vals) < 2:
        return None
    steps = pd.Series(
        pd.to_numeric(vals["eps"], errors="coerce").values,
        index=pd.to_datetime(vals["fiscal_end"]) + pd.Timedelta(days=LAG_DAYS),
    ).sort_index()
    steps = steps[~steps.index.duplicated(keep="last")]
    if getattr(px.index, "tz", None) is not None:
        px = px.copy()
        px.index = px.index.tz_localize(None)
    daily = steps.reindex(px.index, method="ffill").where(lambda s: s > 0)
    mult = (px / daily).dropna()
    return (mult, daily) if len(mult) else None


def _slice_years(s: pd.Series, years: float, end=None) -> pd.Series:
    """마지막 `years`년 구간. `end`를 주면 그 시점까지(창을 뒤로 밀 때 쓴다)."""
    if not len(s):
        return s
    last = end if end is not None else s.index[-1]
    return s[(s.index <= last) & (s.index > last - pd.Timedelta(days=int(365.25 * years)))]


def _measure(mult: pd.Series, px: pd.Series, daily: pd.Series,
             window: int, end=None) -> dict | None:
    """창 하나에서 ②가 서는지와 밴드 분위. 판별은 **제품 코드의 `_band_quality`**를 쓴다."""
    m = _slice_years(mult, window, end)
    if len(m) < 2:
        return None
    p, f = px.reindex(m.index), daily.reindex(m.index)
    q = _band_quality(m, p, f)
    out = {"n": q["n"], "years": q["years"], "corr": q["corr"],
           "sd_fund": q["sd_fund"], "usable": q["usable"]}
    mm = m.where(m > 0).dropna()
    if len(mm):
        out.update({"q25": float(mm.quantile(0.25)), "q50": float(mm.quantile(0.50)),
                    "q75": float(mm.quantile(0.75))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=80, help="표본 종목 수 (시총 5분위 층화)")
    args = ap.parse_args()

    key = get_api_key()
    if not key:
        print("OPENDART_API_KEY가 없다 — 이 진단은 키가 필요하다.")
        return 1
    cmap = get_corp_code_map()

    L = get_kr_listing()
    pool = L[L["is_common"] & (L["Marcap"] > 0)].copy()
    pool["층"] = pd.qcut(pool["Marcap"], 5, labels=["Q1 최소형", "Q2", "Q3", "Q4", "Q5 최대형"])
    per = max(1, args.limit // 5)
    samp = (pool.groupby("층", observed=True)
                .apply(lambda g: g.sample(min(per, len(g)), random_state=11),
                       include_groups=False)
                .reset_index())

    print(f"표본 {len(samp)}종목 · 창 후보 {list(WINDOWS)}년 · PER 다리만\n")
    print(f"관문(ADR-0012): corr ≥ {BAND_CORR_LIMIT} 제외 · 창 < {BAND_MIN_YEARS:g}년 제외 "
          f"· 관측 < {BAND_MIN_OBS}일 제외\n")

    rows, skipped_px = [], 0
    for r in samp.itertuples():
        code = r.Code
        if code not in cmap.index:
            continue
        try:
            fin, _, _ = _deep_frame(key, cmap.at[code, "corp_code"])
        except Exception:
            continue
        px = _deep_prices(code)
        if px is None:
            skipped_px += 1
            continue
        built = _multiple_series(fin, px)
        if built is None:
            continue
        mult, daily = built
        span = (mult.index[-1] - mult.index[0]).days / 365.25
        rec = {"code": code, "name": r.Name, "층": r.층, "가능": span}
        for w in WINDOWS:
            m = _measure(mult, px, daily, w)
            if m is None:
                continue
            rec[f"ok{w}"] = m["usable"]
            rec[f"corr{w}"] = m["corr"]
            rec[f"yr{w}"] = m["years"]
            rec[f"q50_{w}"] = m.get("q50")
            rec[f"wid{w}"] = (m.get("q75") / m["q25"]) if m.get("q25") else np.nan
            # C3 — 창을 한 해 뒤로 밀어 같은 창으로 다시 잰다
            back = _measure(mult, px, daily, w,
                            end=mult.index[-1] - pd.Timedelta(days=365))
            if back and back.get("q50") and m.get("q50"):
                rec[f"shift{w}"] = abs(back["q50"] / m["q50"] - 1)
        rows.append(rec)

    if not rows:
        print("응답한 종목이 없다 — 키나 네트워크를 확인하라.")
        return 1
    res = pd.DataFrame(rows)
    n = len(res)
    print(f"배수 시리즈를 만든 종목 {n} · 만들 수 있는 기간 중앙 {res['가능'].median():.1f}년"
          + (f" · 주가를 못 받아 뺀 종목 {skipped_px}" if skipped_px else "") + "\n")

    # ── 창별 결과 ────────────────────────────────────────────────
    print("창별 — C1 실패 모드 · C2 커버리지 · C3 흔들림")
    print(f"    {'창':>4}{'창을 채움':>11}{'②가 섬':>10}{'corr≥0.90':>12}"
          f"{'corr 중앙':>11}{'밴드폭':>9}{'창 밀기Δ':>11}")
    print("    " + "─" * 70)
    tab = {}
    for w in WINDOWS:
        ok, cr, yr = f"ok{w}", f"corr{w}", f"yr{w}"
        if ok not in res:
            continue
        have = res[yr].notna()
        # '창을 채움' = 배수 시리즈가 그 창의 90% 이상을 실제로 덮은 종목
        filled = (res[yr] >= w * 0.9) & have
        usable = res[ok].fillna(False) & have
        pricey = (res[cr] >= BAND_CORR_LIMIT) & have
        tab[w] = {"filled": filled.mean(), "usable": usable.mean(),
                  "pricey": pricey.mean(), "corr": res.loc[have, cr].median(),
                  "wid": res.loc[have, f"wid{w}"].median(),
                  "shift": res[f"shift{w}"].median() if f"shift{w}" in res else np.nan}
        t = tab[w]
        print(f"    {w:>3}년{t['filled']:>10.0%}{t['usable']:>10.0%}{t['pricey']:>12.0%}"
              f"{t['corr']:>11.3f}{t['wid']:>9.2f}{t['shift']:>11.1%}")

    print("\n    창을 채움 = 배수 시리즈가 그 창의 90% 이상을 덮은 종목 (C2)")
    print("    ②가 섬    = ADR-0012의 두 관문을 통과해 판정에 실리는 비율")
    print("    corr≥0.90 = '배수가 아니라 주가의 분위'로 탈락하는 비율 (C1)")
    print("    밴드폭    = q75/q25 중앙. 넓을수록 '어디든 들어맞는' 밴드라 정보가 적다")
    print("    창 밀기Δ  = 창을 한 해 뒤로 밀었을 때 중심 배수의 변화율 중앙 (C3)")

    # ── 선택 규칙 적용 ───────────────────────────────────────────
    print("\n선택 규칙 — 재기 전에 적어 둔 것을 그대로 적용한다")
    ws = [w for w in WINDOWS if w in tab]
    print(f"    {'창':>4}{'탈락률':>9}{'직전 대비 개선':>16}{'채움 손실':>11}")
    print("    " + "─" * 42)
    best, prev = None, None
    for w in ws:
        gain = (prev - tab[w]["pricey"]) if prev is not None else np.nan
        loss = (tab[ws[0]]["filled"] - tab[w]["filled"])
        mark = ""
        if prev is not None and gain < 0.02 and best is None:
            best = w
            mark = "  ← C1 개선이 꺾이는 첫 지점"
        print(f"    {w:>3}년{tab[w]['pricey']:>9.0%}"
              f"{('—' if np.isnan(gain) else f'{gain:+.1%}'):>16}{loss:>11.0%}{mark}")
        prev = tab[w]["pricey"]
    if best is None:
        print("\n    ★ 탈락률이 끝까지 계속 줄었다 — 꺾이는 지점이 표 안에 없다.")
        print("      창 후보를 늘려 다시 재라. 규칙을 바꾸지 말 것.")
    else:
        loss = tab[ws[0]]["filled"] - tab[best]["filled"]
        gain = tab[ws[0]]["pricey"] - tab[best]["pricey"]
        print(f"\n    ★ 후보 {best}년 — 3년 대비 탈락률 {gain:+.0%}p · 채움 손실 {loss:.0%}p")
        if loss > gain:
            i = ws.index(best)
            print(f"      C2 손실({loss:.0%}p)이 C1 이득({gain:.0%}p)보다 크다 → "
                  f"규칙대로 한 단계 짧은 **{ws[max(0, i-1)]}년**을 고른다.")
        else:
            print(f"      C2 손실({loss:.0%}p)이 C1 이득({gain:.0%}p)보다 작다 → "
                  f"**{best}년**을 고른다.")

    print("\n이 표를 ADR에 옮겨 적어라. 규칙은 재기 전에 고정했고, 여기서 바꾸지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
