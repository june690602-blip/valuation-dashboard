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
from src.analysis.fscore import fscore                                  # noqa: E402
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
# 어디까지 거슬러 가나 — **데이터가 허락하는 데까지**다 (사전등록 개정 3).
#   KR: DART 전체 재무제표 API의 하한이 회계연도 2013이라(2015년 보고서의 전전기)
#       ⑤의 최소 3년을 채우는 첫 시점이 2016이다. 2015는 0종목(실측).
#   US: EDGAR는 2007년부터라 2012에 953종목이 선다(실측). 개정 1이 2017로 잡았던 것은
#       한국에 맞추려던 것뿐이고 다른 이유가 없었다 — 시점 9개가 이 연구의 병목이었다.
# 12개월 선행수익률이 필요해 양쪽 다 2025가 마지막이다.
START_YEAR = {"KR": 2016, "US": 2012}
YEARS = range(START_YEAR["KR"], 2026)          # main()이 시장에 맞춰 덮어쓴다
HORIZON_DAYS = 365
# check_analysis.py·check_epv_viability.py와 **같은 가정**. 갈라지면 진단이 화면과 다른
# 자본비용으로 재게 된다. 시장마다 따로 둔다(ADR-0017).
RF_MRP = {"KR": (0.035, 0.06), "US": (0.045, 0.05)}
MARKET = {"KR": {"currency": "KRW", "index": "^KS11", "bench": "KOSPI",
                 "dir": "backtest"},
          "US": {"currency": "USD", "index": "^GSPC", "bench": "S&P 500",
                 "dir": "backtest_us"}}
RF, MRP = RF_MRP["KR"]                         # main()이 시장에 맞춰 덮어쓴다
MIN_YEARS_KNOWN = 3                            # ⑤의 NORMALIZE_MIN_YEARS와 같은 하한
EPV = "EPV"

# 패널이 기록하는 축 — **판정이 쓰는 축과 같지 않다. 같으면 안 된다.**
# 이 패널은 연구 자료다. 판정에서 빠진 축이 패널에서도 빠지면 **그 축을 다시 잴 방법이
# 사라진다** — 빼자는 결정이 옳았는지 되짚을 수도, 새 표본에서 재개할 수도 없다.
# EPV가 처음부터 이렇게 기록돼 왔다(판정에 없는데 열은 있다). ADR-0035로 ② 역사적 밴드가
# 판정에서 빠질 때 이 자리가 드러났다 — 실제로 재빌드 한 번에 `역사적 밴드` 열이 통째로
# 사라졌고, 그러면 check_backtest_combos.py의 A2·B1·B6·C3가 잴 것을 잃는다.
BAND = "역사적 밴드"
PANEL_AXES = tuple(dict.fromkeys((*FUNDAMENTAL_METHODS, BAND, EPV)))

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


def effective_filed(fin: pd.DataFrame) -> pd.Series:
    """회계연도별 '이 값이 알려진 날'. 접수일이 없으면 회계연도말+90일.

    **같은 날짜가 겹치면 뒤로 물린다.** EDGAR의 첫 XBRL 보고서는 3개 회계연도를
    한꺼번에 담아서(당기·전기·전전기) 세 해의 `filed`가 같아진다. 그대로 두면
    계단 시리즈의 인덱스가 중복돼 `_fundamental_daily`가 예외로 죽는다(실측: 미국
    1,259종목 중 1,124곳).

    겹친 해를 **버리지 않고 1년씩 앞당기는** 이유: 그 숫자들은 실제로 그때 공개돼
    있었다. XBRL 태깅이 2009~2011년에 단계 도입된 것은 **SEC의 사정이지 정보의
    사정이 아니다** — 2011 회계연도 실적은 2012년 종이 10-K에 있었다. 사업보고서는
    1년 간격으로 나오므로 간격도 365일로 둔다.

    보수적인 대안은 겹친 해를 버리는 것이고 그러면 룩어헤드 위험이 0이 되지만,
    밴드·정규화의 창이 그만큼 짧아진다. 어느 쪽이든 ADR에 적는다.
    """
    filed = pd.to_datetime(fin.get("filed"), errors="coerce")
    fallback = pd.to_datetime(fin["fiscal_end"], errors="coerce") + pd.Timedelta(days=90)
    eff = (filed.fillna(fallback) if filed is not None else fallback).sort_index()
    v = eff.to_numpy(copy=True)
    year = pd.Timedelta(days=365)
    for i in range(len(v) - 2, -1, -1):          # 최신에서 과거로 내려오며 순서를 강제
        if pd.isna(v[i]) or pd.isna(v[i + 1]):
            continue
        if v[i] >= v[i + 1]:
            v[i] = v[i + 1] - year
    return pd.Series(v, index=eff.index)


def known_at(fin: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    """t 시점에 **공시돼 있던** 회계연도만."""
    eff = effective_filed(fin)
    return fin.loc[eff.index][eff <= t]


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
    f["fiscal_end_true"] = pd.to_datetime(f["fiscal_end"], errors="coerce")
    # `_fundamental_daily`가 +90일을 더하므로 여기서 90일을 빼 두면 실제 공시일이 된다.
    # 겹친 날짜는 `effective_filed`가 이미 풀어 놨다 — 안 풀면 인덱스 중복으로 죽는다.
    f["fiscal_end"] = effective_filed(known) - pd.Timedelta(days=90)
    f["shares_outstanding"] = shares
    f["eps"] = pd.to_numeric(f["net_income"], errors="coerce") / shares
    # 표준 컬럼을 **전부** 세운다. 없는 채로 두면 분석 계층이 `fin["interest_expense"]`처럼
    # 직접 인덱싱하는 자리에서 KeyError로 죽는다(실측: 409종목 전부).
    for col in FIN_COLUMNS:
        if col not in f.columns:
            f[col] = np.nan

    mk = meta.get("market") or "KR"
    cfg = MARKET.get(mk, MARKET["KR"])
    return CompanyData(
        ticker=code, yahoo_ticker=code, name=meta.get("name") or code,
        market=mk, currency=cfg["currency"], sector=meta.get("sector") or "기타",
        industry=meta.get("sector") or "기타",
        price=price, market_cap=shares * price, shares_outstanding=shares,
        financials=f, ttm=None,                          # ttm=None — 분기 정보 유입 차단
        prices=px_t, index_prices=index_px[index_px.index <= t],
        benchmark_name=cfg["bench"], peers=_EMPTY_PEERS, prices_raw=px_t,
        is_financial=False, consensus=None, financial_currency=cfg["currency"])


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


# ── 조정 누락 가격 (데이터 품질 관문) ────────────────────────────────
# KRX 일간 가격제한은 ±30%다(2015년 이후 · 그 전 ±15%). 그보다 큰 하루 **상승**은 실제
# 등락이 아니라 **분할·병합 조정 누락**이다. 실측: 제일바이오 +300배(2026-02-09) ·
# 모아텍 +256배(2008-12-19) · DH오토넥스 +9배(2024-09-24).
#
# 이것이 왜 치명적인가 — **순위 통계는 멀쩡한데 평균 통계만 무너진다.** 오염된 패널에서
# B1의 IC는 0.110으로 정상이었는데(ADR-0028의 0.103) Q5−Q1은 +13.1%에서 **−79.1%**로
# 뒤집혔고 '크게 고평가' 평균이 **+66.9%**가 됐다. 728배 하나가 5분위 평균을 통째로 끌었다.
# IC만 보고 "재현됐다"고 넘어갔으면 사전등록의 성공선(스프레드 하한)을 오염된 자로
# 판정했을 것이다.
#
# 두 가지를 **일부러 하지 않는다**:
#   1. **값을 고치지 않는다.** 조정 비율을 우리가 추정하면 없는 가격을 지어내는 것이다.
#      그 관측만 버린다(실측 25/7,674 = 0.33%).
#   2. **하락 쪽은 걸러내지 않는다.** 정리매매에는 가격제한폭이 **없어서** −80% 하루가
#      정당하다. 그것을 버리면 ADR-0028이 폐지 332곳을 넣어 없앤 생존편향이 되돌아온다.
#      (하락까지 걸렀을 때 제거량이 0.33% → 1.72%로 다섯 배가 됐다 — 대부분 정리매매다.)
#
# **지속성을 함께 요구한다** — 스케일이 바뀌면 그 수준이 유지되고, 정리매매 급등은
# 유지되지 않는다. 셋 다 판단값이다(`BAND_CORR_LIMIT`과 같은 성격).
JUMP_UP = 1.0          # 하루 +100% 초과 (제한폭 ±30%에 신규상장·거래재개 여유를 얹은 값)
JUMP_PERSIST = 5       # 이후 5거래일
JUMP_RATIO = 2.0       # 그 중앙값이 직전가의 2배를 넘으면 스케일이 바뀐 것

# **이 관문은 한국 전용이다.** 근거가 "KRX 일간 제한폭 ±30%를 넘었다"인데 미국에는
# 일간 제한폭이 **없다** — 임상 결과가 나온 바이오가 하루에 두 배가 되고 그 수준을
# 유지하는 것은 조정 누락이 아니라 실제 등락이다. 같은 상수를 미국에 씌우면 진짜
# 급등주를 표본에서 빼서 예측력을 **지어내게** 된다(빠지는 쪽이 대체로 '고평가'다).
# 그리고 미국은 yfinance의 분할 조정이 실제로 잘 돼 있다.
# 미국에 관문이 필요한지는 **따로 재야 하고, 재기 전까지는 끄는 것이 안전하다** —
# ADR-0030·0031의 미국 숫자를 이 변경으로 말없이 움직이지 않는 쪽이기도 하다.
JUMP_GUARD_MARKETS = ("KR",)
MARKET_CODE = "KR"     # main()이 덮어쓴다


def scale_jump_dates(px: pd.Series) -> list:
    """조정 누락으로 보이는 날짜들. **값을 고치지 않고 날짜만** 돌려준다."""
    if MARKET_CODE not in JUMP_GUARD_MARKETS:
        return []
    r = px.pct_change()
    out = []
    for d in r.index[r.to_numpy() > JUMP_UP]:
        i = px.index.get_loc(d)
        if i < 1:
            continue
        after = px.iloc[i:i + JUMP_PERSIST]
        if len(after) and float(np.median(after)) > JUMP_RATIO * float(px.iloc[i - 1]):
            out.append(d)
    return out


def forward_return(px: pd.Series, t: pd.Timestamp, entry: float) -> tuple[float | None, bool]:
    """(12개월 가격수익률, 중도 폐지 여부).

    폐지되면 **정리매매 종가까지의 수익률을 실현하고 잔여기간은 현금**으로 둔다
    (사전등록 §3). 실제 체결가가 이미 폐지 사유를 반영하므로 사유로 나누지 않는다.

    창 안에 조정 누락(위 관문)이 있으면 **None** — 그 관측은 수익률을 못 만든다.
    폐지 여부는 그대로 돌려준다(패널의 폐지 편입 집계가 어긋나지 않게).
    """
    end = t + pd.Timedelta(days=HORIZON_DAYS)
    win = px[(px.index > t) & (px.index <= end)]
    if not len(win):
        return None, False
    delisted = (end - win.index[-1]).days > 30
    if any(t < d <= end for d in scale_jump_dates(px)):
        return None, delisted
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

        # ③이 PBR을 되읽고 있지 않은지 재려면 **그 시점 PBR**이 있어야 한다(ADR-0010).
        # 적정PBR = 1 + (ROE−r)·w/(1+r−w)이므로, 지속계수 w가 낮을수록 적정PBR이 전 종목
        # 1.0에 붙고 그러면 괴리율이 사실상 1/PBR − 1이 된다. 둘을 함께 실어야
        # `check_rim_persistence.py`가 w별로 그 상관을 잴 수 있다.
        # `rim_fair_pbr`은 ③이 빠진 종목에서 None으로 남는다 — 빠진 것과 1.0은 다르다.
        eq_t = d.latest("total_equity")
        row["pbr"] = (d.market_cap / eq_t) if (eq_t and eq_t > 0 and d.market_cap) else None
        row["rim_fair_pbr"] = v.rim_fair_pbr
        for e in v.estimates:
            if e.method in PANEL_AXES and e.mid and e.mid > 0:
                row[e.method] = float(np.log(e.mid / d.price))

        oi, oi_years = normalized_operating_income(d.financials)
        net_debt = (d.latest("total_debt") or 0.0) - (d.latest("cash") or 0.0)
        epv = epv_per_share(oi, cc.tax_rate, cc.wacc, net_debt, d.shares_outstanding)
        if epv and epv > 0:
            row[EPV] = float(np.log(epv / d.price))
        row["epv_years"] = oi_years

        # F-Score (사전등록 개정 4) — **괴리율이 아니라 점수다.** 다른 축들은
        # log(적정가/주가)로 들어가는데 이것은 가격을 내지 않으므로 점수 그대로 싣는다.
        # `d.financials`는 이미 공시일로 잘린 프레임이라(`known_at`) EPV와 같은 경로를
        # 타고 룩어헤드가 생기지 않는다.
        # 분모(`fscore_max`)를 함께 싣는 이유: 옛 연도에는 `total_debt`가 있는 해와
        # 없는 해가 섞여 **시점마다 분모가 달라질 수 있다**(사전등록 개정 4 커버리지 절).
        # 점수만 실으면 6/9와 6/8을 나중에 구별할 수 없다.
        fs = fscore(d.financials)
        if fs:
            row["fscore"] = fs["score"]
            row["fscore_max"] = fs["max_score"]
            row["fscore_ex_eq"] = fs["score_ex_equity"]      # 7번(신주발행) 뺀 8점 척도
        rows.append(row)

    df = pd.DataFrame(rows)
    if len(df):
        df.attrs["legs_fit"] = sorted(coef.keys()) if coef else []
        df.attrs["n_snapshot"] = len(snap)
        df.attrs["errors"] = errs
        df.attrs["n_built"] = len(built)
    return df


# ── 변형 (사전등록 §5-C) ─────────────────────────────────────────────
# 이미 내린 결정을 되짚는 후보들이다. **상수 하나만 갈아 끼우고 나머지는 전부 같다** —
# 두 판을 따로 구현하면 무엇이 차이를 냈는지 알 수 없게 된다.
#
# `w021`·`w062`·`w090`은 ③ 지속계수의 **중심만** 옮긴다(2026-08-11 계획 Phase A).
# 문헌값 0.62를 채택했을 때 ③이 1/PBR을 되읽는 쪽으로 가는지 재려는 것이지,
# IC가 가장 높은 w를 찾으려는 것이 아니다 — 그건 ADR-0003이 가중치에서 피한 짓이다.
VARIANTS = ("base", "norm5", "band5", "nogate", "w021", "w062", "w090")


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
    if name.startswith("w0"):                 # w021 · w062 · w090 — ③ 지속계수 중심
        center = int(name[1:]) / 100.0
        orig = V._rim

        def moved(bps, roe, r, _c=center, _orig=orig):
            # **하단·상단은 원본 그대로 둔다.** 여기서 재는 것은 중심값의 이동뿐이고,
            # 패널에 실리는 ③ 괴리율도 중심에서 나온다. 식을 통째로 바꾸면 무엇이
            # 차이를 냈는지 알 수 없게 된다(위 변형들과 같은 규약).
            fv, _ = _orig(bps, roe, r)
            if fv is None:
                return None, None
            mid = max(bps + bps * (roe - r) * _c / (1 + r - _c), 0.0)
            # 중심이 원본 범위 밖으로 나가면(w021이 그렇다 — 0.21 < 하단 0.6) 범위를 넓혀
            # low ≤ mid ≤ high를 지킨다. 판정·패널은 mid만 쓰지만 `aggregate()`가 low·high도
            # 가중평균하므로, 뒤집힌 채 두면 화면 범위가 거꾸로 서는 값이 만들어진다.
            return (V.FairValue("수익가치(RIM)", min(fv.low, mid), mid, max(fv.high, mid),
                                note=f"ROE {roe:.1%}, r {r:.1%}, 지속계수 중심 {_c}"),
                    mid / bps)

        V._rim = moved
        return f"③ 지속계수 중심 0.8 → {center}"
    raise ValueError(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", type=int, nargs="*", help="연도만 골라 돌린다(디버깅)")
    ap.add_argument("--variant", default="base", choices=VARIANTS)
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    args = ap.parse_args()
    warnings.filterwarnings("ignore")

    global OUT, RAW, RF, MRP, MARKET_CODE
    cfg = MARKET[args.market]
    OUT = ROOT / "data" / cfg["dir"]
    RAW = OUT / "raw"
    RF, MRP = RF_MRP[args.market]
    MARKET_CODE = args.market
    guard = "켬" if args.market in JUMP_GUARD_MARKETS else "끔(미국은 일간 제한폭이 없다)"
    print(f"시장: {args.market} · 변형: {args.variant} — {apply_variant(args.variant)}"
          f" · 조정누락 관문 {guard}")

    data = load_all()
    if not data:
        print("수집 데이터가 없다 — scripts/backtest_collect.py를 먼저 돌려라.")
        return 1
    print(f"종목 {len(data)}곳 적재")

    import yfinance as yf
    h = yf.Ticker(cfg["index"]).history(period="max", auto_adjust=False)["Close"].dropna()
    h.index = pd.to_datetime(h.index).tz_localize(None)
    print(f"{cfg['bench']} 지수 {len(h)}행 ({h.index[0].date()}~)\n")

    years = args.dates or list(range(START_YEAR[args.market], 2026))
    frames, meta_rows = [], []
    for y in years:
        t = pd.Timestamp(y, REBALANCE_MONTH, REBALANCE_DAY)
        df = evaluate(t, data, h)
        if df.empty:
            print(f"{t.date()}  표본 부족 — 건너뜀")
            continue
        frames.append(df)
        got = {m: int(df[m].notna().sum()) for m in PANEL_AXES
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
