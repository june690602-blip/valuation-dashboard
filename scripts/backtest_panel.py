"""시점별 축 복원 — 각 리밸런싱일에 **판정 코드를 그대로 불러** 패널을 만든다.

    python scripts/backtest_panel.py                 # 전체
    python scripts/backtest_panel.py --dates 2021    # 한 시점만 (디버깅)

사전등록: `docs/review/B1-백테스트-사전등록.md` §2·§3.

**왜 축을 다시 구현하지 않는가**
--------------------------------
ADR-0009이 옛 백테스트를 접은 진짜 이유는 데이터가 아니라 **판정과 다른 계산을 하고
있었다**는 것이다 — ②는 1.5년 롤링(판정은 5년 분위), ③은 지속계수 0.9 고정(판정은
0.6~1.0), 장부가 게이트 없음. 그 문서의 표현으로 *"동일하게 계산되는 부분은 사실상 0%"*.

그래서 여기서는 축을 **한 줄도 다시 쓰지 않는다.** 그 시점의 `CompanyData`를 합성해
`compute_valuation()`을 그대로 부른다. 게이트(ADR-0007·0010)도, 밴드 품질 관문
(ADR-0012)도, 회귀 배수(ADR-0014)도 **화면이 쓰는 그 코드**가 판단한다.
같은 이유로 회귀 계수는 `build_coefficients`/`fit_leg`를 그대로 부른다.

**시점 데이터를 어떻게 강제하나**
--------------------------------
1. 회계연도 t의 재무는 `filed`(실제 접수일) **이후**에만 보인다. 근사가 아니다.
2. `_fundamental_daily`가 `fiscal_end + 90일`을 계단 시작점으로 쓰므로, 합성할 때
   **`fiscal_end := filed − 90일`**로 넣어 그 코드가 실제 공시일을 쓰게 만든다.
   제품 코드를 고치지 않고 시점을 맞추는 방법이고, 원래 `fiscal_end`는
   `fiscal_end_true`로 따로 남겨 근사와의 차이를 잰다.
3. 주가·지수는 t까지만 잘라 넘긴다. `ttm=None`이라 분기 정보가 새어 들어올 자리도 없다.
4. 회귀 계수도 **그 시점 횡단면으로만** 적합한다. 오늘 계수를 소급하면 그 자체가 룩어헤드다.

**주당 값의 기준** — `eps := net_income / 현재주식수`, `shares_outstanding := 현재주식수`로
통일한다. DART의 공시 EPS는 **분할 전 기준**이라(삼성 2016년 157,967원) 분할 수정된
주가와 곱하면 배수가 터진다. 시총·배수·BPS가 전부 같은 기준 위에 서는 것이 우선이고,
남는 오차는 증자·감자뿐이다(사전등록 §1).
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.capital_cost import compute_capital_cost              # noqa: E402
from src.analysis.epv import (epv_per_share,                            # noqa: E402
                              normalized_operating_income)
from src.analysis.indicators import compute_indicators                  # noqa: E402
from src.analysis.valuation import (FUNDAMENTAL_METHODS,                # noqa: E402
                                    compute_valuation)
from src.data import ca_bundle                                          # noqa: E402
from src.data.models import (FIN_COLUMNS, PEER_COLUMNS,                 # noqa: E402
                             CompanyData)
from src.data.universe_multiples import build_coefficients              # noqa: E402

ca_bundle.install()   # 지수 시세를 yfinance로 받는다 (ADR-0027)

OUT = ROOT / "data" / "backtest"
RAW = OUT / "raw"

REBALANCE_MONTH, REBALANCE_DAY = 6, 1          # 3월 공시가 모두 반영된 뒤 (사전등록 §2)
YEARS = range(2017, 2026)                      # 12개월 선행수익률이 필요해 2025가 마지막
HORIZON_DAYS = 365
RF, MRP = 0.035, 0.06                          # check_analysis.py·check_epv_viability.py와 동일
MIN_YEARS_KNOWN = 3                            # ⑤의 NORMALIZE_MIN_YEARS와 같은 하한
EPV = "EPV"

_EMPTY_PEERS = pd.DataFrame(columns=PEER_COLUMNS)


def load_all() -> dict:
    """종목별 (재무, 주가, 메타). 수집기가 만든 parquet만 읽는다."""
    out = {}
    for meta_p in sorted(RAW.glob("meta_*.json")):
        code = meta_p.stem[5:]
        fin_p, px_p = RAW / f"fin_{code}.parquet", RAW / f"px_{code}.parquet"
        if not (fin_p.exists() and px_p.exists()):
            continue
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            fin = pd.read_parquet(fin_p)
            px = pd.read_parquet(px_p)["close"]
        except Exception:
            continue
        px.index = pd.to_datetime(px.index)
        if not len(px) or not len(fin):
            continue
        out[code] = (fin, px.sort_index(), meta)
    return out


def known_at(fin: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    """t 시점에 **공시돼 있던** 회계연도만. 접수일을 모르면 회계연도말+90일로 물러선다."""
    filed = pd.to_datetime(fin.get("filed"), errors="coerce")
    fallback = pd.to_datetime(fin["fiscal_end"], errors="coerce") + pd.Timedelta(days=90)
    eff = filed.fillna(fallback) if filed is not None else fallback
    return fin[eff <= t]


def synth(code: str, fin: pd.DataFrame, px: pd.Series, meta: dict,
          t: pd.Timestamp, index_px: pd.Series) -> CompanyData | None:
    """t 시점의 CompanyData를 합성한다. 설 수 없으면 None."""
    known = known_at(fin, t)
    if len(known) < MIN_YEARS_KNOWN:
        return None
    px_t = px[px.index <= t]
    if len(px_t) < 250:                                  # 밴드·베타를 낼 최소한
        return None
    if (t - px_t.index[-1]).days > 30:                   # 이미 폐지된 종목
        return None

    shares = float(meta["shares"])
    price = float(px_t.iloc[-1])
    if not np.isfinite(price) or price <= 0 or shares <= 0:
        return None

    f = known.copy()
    filed = pd.to_datetime(f.get("filed"), errors="coerce")
    f["fiscal_end_true"] = pd.to_datetime(f["fiscal_end"], errors="coerce")
    # `_fundamental_daily`가 +90일을 더하므로 여기서 90일을 빼 두면 실제 공시일이 된다
    f["fiscal_end"] = (filed - pd.Timedelta(days=90)).fillna(f["fiscal_end_true"])
    f["shares_outstanding"] = shares
    f["eps"] = pd.to_numeric(f["net_income"], errors="coerce") / shares
    # 표준 컬럼을 **전부** 세운다. 없는 채로 두면 분석 계층이 `fin["interest_expense"]`처럼
    # 직접 인덱싱하는 자리에서 KeyError로 죽는다(실측: 409종목 전부).
    for col in FIN_COLUMNS:
        if col not in f.columns:
            f[col] = np.nan

    return CompanyData(
        ticker=code, yahoo_ticker=code, name=meta.get("name") or code,
        market="KR", currency="KRW", sector=meta.get("sector") or "기타",
        industry=meta.get("sector") or "기타",
        price=price, market_cap=shares * price, shares_outstanding=shares,
        financials=f, ttm=None,                          # ttm=None — 분기 정보 유입 차단
        prices=px_t, index_prices=index_px[index_px.index <= t],
        benchmark_name="KOSPI", peers=_EMPTY_PEERS, prices_raw=px_t,
        is_financial=False, consensus=None, financial_currency="KRW")


def snapshot_row(d: CompanyData) -> dict | None:
    """회귀 학습 표본 한 줄 — 그 시점 배수. `build_coefficients`가 기대하는 스키마."""
    eq, ni, rev = d.latest("total_equity"), d.latest("net_income"), d.latest("revenue")
    mc = d.market_cap
    if not eq or eq <= 0:
        return None
    return {"code": d.ticker, "sector": d.sector, "mcap": mc,
            "per": (mc / ni) if (ni and ni > 0) else None,
            "pbr": mc / eq,
            "psr": (mc / rev) if (rev and rev > 0) else None,
            "ev_ebitda": None,                 # 감가상각비가 본문에 없어 EBITDA를 못 만든다
            "roe": ni / eq if ni is not None else None}


def forward_return(px: pd.Series, t: pd.Timestamp, entry: float) -> tuple[float | None, bool]:
    """(12개월 가격수익률, 중도 폐지 여부).

    폐지되면 **정리매매 종가까지의 수익률을 실현하고 잔여기간은 현금**으로 둔다
    (사전등록 §3). 실제 체결가가 이미 폐지 사유를 반영하므로 사유로 나누지 않는다.
    """
    end = t + pd.Timedelta(days=HORIZON_DAYS)
    win = px[(px.index > t) & (px.index <= end)]
    if not len(win):
        return None, False
    delisted = (end - win.index[-1]).days > 30
    return float(win.iloc[-1] / entry - 1.0), delisted


def evaluate(t: pd.Timestamp, data: dict, index_px: pd.Series) -> pd.DataFrame:
    """한 리밸런싱일의 전 종목 결과."""
    built = {}
    snap = []
    for code, (fin, px, meta) in data.items():
        d = synth(code, fin, px, meta, t, index_px)
        if d is None:
            continue
        built[code] = d
        r = snapshot_row(d)
        if r:
            snap.append(r)
    if len(snap) < 50:
        return pd.DataFrame()

    coef = build_coefficients(pd.DataFrame(snap)) or None

    rows, errs = [], {}
    for code, d in built.items():
        fin, px, meta = data[code]
        try:
            ind = compute_indicators(d)
            cc = compute_capital_cost(d, rf=RF, mrp=MRP)
            v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
        except Exception as e:
            # **세어서 보고한다.** 조용히 넘기면 표본이 왜 줄었는지 알 수 없고,
            # 그 줄어듦이 편향인지 아닌지도 못 가린다.
            errs[type(e).__name__] = errs.get(type(e).__name__, 0) + 1
            continue
        fwd, delisted = forward_return(px, t, d.price)

        row = {"date": t, "code": code, "sector": d.sector, "mcap": d.market_cap,
               "price": d.price, "fwd_12m": fwd, "delisted_in_window": delisted,
               "listed_at_t": bool(meta.get("listed")),
               "verdict": v.verdict, "fair_mid": v.fair_mid,
               "n_methods": len(v.weights or {}),
               "rel_legs": v.relative_legs,
               "years_known": int(len(known_at(fin, t))),
               "norm_years": v.normalized_years}
        for e in v.estimates:
            if e.method in FUNDAMENTAL_METHODS and e.mid and e.mid > 0:
                row[e.method] = float(np.log(e.mid / d.price))

        oi, oi_years = normalized_operating_income(d.financials)
        net_debt = (d.latest("total_debt") or 0.0) - (d.latest("cash") or 0.0)
        epv = epv_per_share(oi, cc.tax_rate, cc.wacc, net_debt, d.shares_outstanding)
        if epv and epv > 0:
            row[EPV] = float(np.log(epv / d.price))
        row["epv_years"] = oi_years
        rows.append(row)

    df = pd.DataFrame(rows)
    if len(df):
        df.attrs["legs_fit"] = sorted(coef.keys()) if coef else []
        df.attrs["n_snapshot"] = len(snap)
        df.attrs["errors"] = errs
        df.attrs["n_built"] = len(built)
    return df


# ── 변형 (사전등록 §5-C) ─────────────────────────────────────────────
# 이미 내린 결정을 되짚는 후보 셋이다. **상수 하나만 갈아 끼우고 나머지는 전부 같다** —
# 두 판을 따로 구현하면 무엇이 차이를 냈는지 알 수 없게 된다.
VARIANTS = ("base", "norm5", "band5", "nogate")


def apply_variant(name: str) -> str:
    """판정 모듈의 상수를 그 자리에서 바꾼다. 무엇을 바꿨는지 문자열로 돌려준다."""
    from src.analysis import valuation as V

    if name == "base":
        return "현행 그대로"
    if name == "norm5":                       # C1 — ADR-0025가 못 잰 것
        V.NORMALIZE_WINDOW = 5
        return "⑤ 정규화 창 8년 → 5년"
    if name == "band5":                       # C3 — ADR-0026이 판단값으로 정한 것
        V.BAND_WINDOW_YEARS = 5.0
        return "② 밴드 창 7년 → 5년"
    if name == "nogate":                      # C2 — ADR-0007·0010을 끈다
        orig = V._book_quality

        def open_gate(d, pbr, roe, r=None, pbr_q=None):
            out = orig(d, pbr, roe, r, pbr_q)
            return {**out, "distorted": False, "short": "", "detail": ""}

        V._book_quality = open_gate
        return "③ 장부가 게이트 해제(ADR-0007·0010)"
    raise ValueError(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", type=int, nargs="*", help="연도만 골라 돌린다(디버깅)")
    ap.add_argument("--variant", default="base", choices=VARIANTS)
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    print(f"변형: {args.variant} — {apply_variant(args.variant)}")

    data = load_all()
    if not data:
        print("수집 데이터가 없다 — scripts/backtest_collect.py를 먼저 돌려라.")
        return 1
    print(f"종목 {len(data)}곳 적재")

    import yfinance as yf
    h = yf.Ticker("^KS11").history(period="max", auto_adjust=False)["Close"].dropna()
    h.index = pd.to_datetime(h.index).tz_localize(None)
    print(f"KOSPI 지수 {len(h)}행 ({h.index[0].date()}~)\n")

    years = args.dates or list(YEARS)
    frames, meta_rows = [], []
    for y in years:
        t = pd.Timestamp(y, REBALANCE_MONTH, REBALANCE_DAY)
        df = evaluate(t, data, h)
        if df.empty:
            print(f"{t.date()}  표본 부족 — 건너뜀")
            continue
        frames.append(df)
        got = {m: int(df[m].notna().sum()) for m in (*FUNDAMENTAL_METHODS, EPV)
               if m in df.columns}
        meta_rows.append({"date": t, "n": len(df), "n_fit": df.attrs["n_snapshot"],
                          "legs": ",".join(df.attrs["legs_fit"]),
                          "fwd_ok": int(df["fwd_12m"].notna().sum()),
                          "delisted": int(df["delisted_in_window"].sum())})
        print(f"{t.date()}  종목 {len(df):4d}/{df.attrs['n_built']:4d} · 회귀표본 "
              f"{df.attrs['n_snapshot']:4d} · 다리 [{','.join(df.attrs['legs_fit'])}] "
              f"· 선행수익 {int(df['fwd_12m'].notna().sum()):4d} · 폐지 "
              f"{int(df['delisted_in_window'].sum()):3d}")
        print(f"            축별 {got}"
              + (f" · 계산실패 {df.attrs['errors']}" if df.attrs["errors"] else ""))

    if not frames:
        return 1
    panel = pd.concat(frames, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.variant == "base" else f"_{args.variant}"
    panel.to_parquet(OUT / f"panel{suffix}.parquet")
    pd.DataFrame(meta_rows).to_csv(OUT / f"panel_meta{suffix}.csv", index=False)
    print(f"\n패널 {len(panel):,}행 → {OUT / f'panel{suffix}.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
