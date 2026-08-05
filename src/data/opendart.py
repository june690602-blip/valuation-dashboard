"""OpenDART(전자공시) 클라이언트 — 한국 종목의 공시 원본 재무제표.

yfinance 대비 이점: ① 계산값이 네이버/DART 공식 숫자와 정렬됨 ② 재무 이력이 길어져
(한 보고서에 당기·전기·전전기 3년 → 보고서 2개로 ~6년) 역사적 밴드·백테스트 표본 확대.

키는 소스에 하드코딩하지 않고 환경변수 OPENDART_API_KEY 또는 .streamlit/secrets.toml에서 읽는다.
키가 없으면 None을 돌려주고, 호출부(KRProvider)는 조용히 yfinance로 폴백한다.
"""
from __future__ import annotations

import datetime as _dt
import io
import os
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .cache import file_cache

BASE = "https://opendart.fss.or.kr/api"
ROOT = Path(__file__).resolve().parents[2]

# (sj_div 후보, account_id 우선순위, 한글명 키워드 폴백) → 표준 컬럼
# sj_div: BS=재무상태표, IS=손익, CIS=포괄손익, CF=현금흐름
DART_MAP: dict[str, tuple] = {
    "revenue": (("IS", "CIS"),
                ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"],
                ["매출액", "영업수익", "수익(매출액)"]),
    "gross_profit": (("IS", "CIS"), ["ifrs-full_GrossProfit"], ["매출총이익"]),
    "operating_income": (("IS", "CIS"),
                         ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
                         ["영업이익"]),
    "net_income": (("IS", "CIS"), ["ifrs-full_ProfitLoss"], ["당기순이익"]),
    "pretax_income": (("IS", "CIS"), ["ifrs-full_ProfitLossBeforeTax"],
                      ["법인세비용차감전순이익", "법인세비용차감전순손익"]),
    "tax_expense": (("IS", "CIS"), ["ifrs-full_IncomeTaxExpenseContinuingOperations"],
                    ["법인세비용"]),
    "eps": (("IS", "CIS"), ["ifrs-full_BasicEarningsLossPerShare"],
            ["기본주당이익", "기본주당순이익", "주당순이익"]),
    # 이자비용은 **손익(IS/CIS)의 발생주의 비용**이다. 예전에는 현금흐름표의 '이자의지급'
    # (InterestPaidClassifiedAsOperatingActivities)을 읽었는데, 그건 그해 실제로 나간 현금이라
    # 이름표와 값이 어긋났다. merge_financials가 DART를 우선하므로 yfinance의 손익 항목까지
    # 덮어써, capital_cost._cost_of_debt의 k_d가 현금주의로 계산됐다
    # (실측: 삼성전자 2025년 DART 0.47조 vs 손익 0.61조 → k_d 2.11% vs 2.72%).
    # 손익에 이자비용 계정이 없으면(비금융업에 흔함) 채우지 않고 yfinance 값에 맡긴다.
    # ifrs-full_FinanceCosts('금융비용')는 쓰지 않는다 — 외환차손·파생상품손실까지 묶인
    # 넓은 계정이라 이자비용이 아니다(실측: 삼성전자 2025년 금융비용 11.7조 vs 이자비용 0.61조).
    "interest_expense": (("IS", "CIS"), ["ifrs-full_InterestExpense"], ["이자비용"]),
    "total_assets": (("BS",), ["ifrs-full_Assets"], ["자산총계"]),
    "total_equity": (("BS",), ["ifrs-full_EquityAttributableToOwnersOfParent",
                               "ifrs-full_Equity"], ["지배기업의소유주에게귀속되는자본", "자본총계"]),
    "total_liabilities": (("BS",), ["ifrs-full_Liabilities"], ["부채총계"]),
    "current_assets": (("BS",), ["ifrs-full_CurrentAssets"], ["유동자산"]),
    "current_liabilities": (("BS",), ["ifrs-full_CurrentLiabilities"], ["유동부채"]),
    # 표준 컬럼 cash의 이름표는 '현금및현금성자산(+단기금융)'이고 yfinance도 단기금융을
    # 포함한 값을 준다. DART에서 현금및현금성자산만 읽으면 순현금이 과소 잡히고 EV가 그만큼
    # 과대해져 EV/EBITDA가 고평가 쪽으로 치우친다(실측: 삼성전자 2025년 57.9조 vs 125.8조,
    # 순현금 68조 차이 = 시총의 5.3%). 단기금융상품은 별도 계정이라 아래 _sum_rows로 합산한다.
    "cash": (("BS",), ["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산"]),
    "ocf": (("CF",), ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
            ["영업활동현금흐름", "영업활동으로인한현금흐름"]),
}

# 표준 컬럼에 **더해야** 이름표와 값이 맞는 항목. DART는 계정을 잘게 나눠 공시하는데
# yfinance는 묶어서 주기 때문에, 한쪽만 읽으면 같은 컬럼이 시장마다 다른 값을 갖게 된다.
# (cash 이름표 = '현금및현금성자산(+단기금융)'. 삼성전자 2025년 현금 57.9조 + 단기금융 68.0조
#  = 125.9조로, yfinance의 125.8조와 맞는다. 더하지 않으면 EV가 68조 과대해진다.)
DART_ADDONS: dict[str, tuple] = {
    "cash": (("BS",),
             ["ifrs-full_ShorttermDepositsNotClassifiedAsCashEquivalents"],
             ["단기금융상품"]),
}


def get_api_key() -> str | None:
    """환경변수 → st.secrets(Streamlit Cloud) → 로컬 secrets.toml 순으로 키를 찾는다."""
    k = os.environ.get("OPENDART_API_KEY")
    if k:
        return k.strip()
    try:  # Streamlit Cloud는 비밀을 st.secrets로 제공
        import streamlit as st
        v = st.secrets.get("OPENDART_API_KEY")
        if v:
            return str(v).strip()
    except Exception:
        pass
    secrets = ROOT / ".streamlit" / "secrets.toml"
    if secrets.exists():
        try:
            import tomllib
            data = tomllib.loads(secrets.read_text(encoding="utf-8"))
            v = data.get("OPENDART_API_KEY")
            if v:
                return str(v).strip()
        except Exception:
            pass
    return None


def _num(s) -> float:
    s = str(s or "").strip().replace(",", "")
    if s in ("", "-", "None"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


@file_cache("dart_corpmap", ttl_hours=24 * 7)
def get_corp_code_map() -> pd.DataFrame:
    """전체 상장사 stock_code → corp_code 매핑 (DataFrame, index=stock_code)."""
    key = get_api_key()
    if not key:
        raise ValueError("OpenDART API 키가 없습니다.")
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=60)
    r.raise_for_status()
    import xml.etree.ElementTree as ET
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    rows = []
    for li in root.iter("list"):
        sc = (li.findtext("stock_code") or "").strip()
        if sc and sc != " ":
            rows.append({"stock_code": sc.zfill(6),
                         "corp_code": (li.findtext("corp_code") or "").strip(),
                         "corp_name": (li.findtext("corp_name") or "").strip()})
    df = pd.DataFrame(rows).drop_duplicates("stock_code").set_index("stock_code")
    return df


def _find_row(rows: list[dict], sj_set: set, ids: list[str], keywords: list[str]) -> dict | None:
    """account_id 우선 → 한글명 **완전일치** → 한글명 부분일치 순으로 첫 매칭 행.

    부분일치 전에 완전일치를 한 번 도는 이유: 계정명이 서로를 포함하는 경우가 있어
    부분일치만 쓰면 엉뚱한 행을 잡는다(예: '보험금융이자비용'이 '이자비용'을 포함한다).
    """
    for aid in ids:
        for r in rows:
            if r.get("sj_div") in sj_set and r.get("account_id") == aid:
                return r
    for exact in (True, False):
        for kw in keywords:
            for r in rows:
                if r.get("sj_div") not in sj_set:
                    continue
                nm = (r.get("account_nm") or "").replace(" ", "")
                if (nm == kw) if exact else (kw in nm):
                    return r
    return None


def _fetch_report(key: str, corp: str, year: int, fs_div: str | None = None) -> dict | None:
    """단일 연간 보고서(전체 재무제표). 연결(CFS) 우선, 없으면 별도(OFS)."""
    for fs in ((fs_div,) if fs_div else ("CFS", "OFS")):
        try:
            r = requests.get(f"{BASE}/fnlttSinglAcntAll.json", params={
                "crtfc_key": key, "corp_code": corp, "bsns_year": str(year),
                "reprt_code": "11011", "fs_div": fs}, timeout=40)
            j = r.json()
        except Exception:
            continue
        if j.get("status") == "000" and j.get("list"):
            j["_fs"] = fs
            return j
    return None


def _parse_report(j: dict, base: int) -> dict[int, dict]:
    """보고서 하나 → {연도: {표준컬럼: 값}} (당기·전기·전전기 3년)."""
    rows = j.get("list", [])
    out = {base: {}, base - 1: {}, base - 2: {}}
    period_fields = ((base, "thstrm_dt"), (base - 1, "frmtrm_dt"),
                     (base - 2, "bfefrmtrm_dt"))
    for year, field in period_fields:
        for row in rows:
            dates = re.findall(r"\d{4}[.-]\d{2}[.-]\d{2}", str(row.get(field) or ""))
            if dates:
                out[year]["fiscal_end"] = pd.to_datetime(dates[-1].replace(".", "-"))
                break
    amounts = ((base, "thstrm_amount"), (base - 1, "frmtrm_amount"),
               (base - 2, "bfefrmtrm_amount"))
    for col, (sj, ids, kws) in DART_MAP.items():
        row = _find_row(rows, set(sj), ids, [k.replace(" ", "") for k in kws])
        if not row:
            continue
        for year, field in amounts:
            out[year][col] = _num(row.get(field))

    # 더해야 이름표와 맞는 항목(단기금융상품 등). 본 계정이 없으면 더하지 않는다 —
    # 단기금융상품만 있는 값을 '현금'이라고 부르면 그게 다시 이름표 불일치가 된다.
    for col, (sj, ids, kws) in DART_ADDONS.items():
        row = _find_row(rows, set(sj), ids, [k.replace(" ", "") for k in kws])
        if not row:
            continue
        for year, field in amounts:
            base_val, add_val = out[year].get(col), _num(row.get(field))
            if base_val is not None and not pd.isna(base_val) and not pd.isna(add_val):
                out[year][col] = base_val + add_val
    return out


# 몇 해를 남길 것인가 — **⑤ 정규화 이익의 창이 이 값을 정한다**(ADR-0025).
# 창이 쓰는 것은 프레임의 마지막 N행이므로 그보다 깊게 받아도 판정은 안 쓴다.
# 이 값이 `valuation.NORMALIZE_WINDOW`보다 작아지면 창이 조용히 짧아진다 —
# `tests/test_normalized_earnings.py`가 그 어긋남을 막는다.
HISTORY_YEARS = 8

# 사업보고서 하나에 3년이 들어 있다(당기·전기·전전기). 그래서 3년 간격으로 받으면
# 빈 해가 없다 — 연도마다 받으면 같은 깊이에 3배를 부른다.
YEARS_PER_REPORT = 3


def _report_years(base: int) -> list[int]:
    """받을 사업연도 — `HISTORY_YEARS`를 덮는 데 필요한 만큼만."""
    n = -(-HISTORY_YEARS // YEARS_PER_REPORT)          # 올림 나눗셈
    return [base - YEARS_PER_REPORT * i for i in range(n)]


@file_cache("dart_fin", ttl_hours=24)
def _dart_financials_df(stock_code: str) -> pd.DataFrame:
    """DART 연간 재무제표 → 표준 스키마 DataFrame (index=회계연도, 과거→최신). 캐시용."""
    key = get_api_key()
    if not key:
        raise ValueError("no key")
    cmap = get_corp_code_map()
    if stock_code not in cmap.index:
        raise ValueError("corp_code not found")
    corp = cmap.at[stock_code, "corp_code"]

    y0 = _dt.date.today().year - 1
    reports = {}
    base = None
    for y in (y0, y0 - 1):  # 가장 최근 사업보고서 연도 찾기
        j = _fetch_report(key, corp, y)
        if j:
            base = y
            reports[y] = j
            break
    if base is None:
        raise ValueError("no annual report")
    fs_div = reports[base].get("_fs", "CFS")
    for y in _report_years(base)[1:]:      # 동일 연결/별도 기준으로 이력 연장
        older = _fetch_report(key, corp, y, fs_div=fs_div)
        if older:
            reports[y] = older

    data: dict[int, dict] = {}
    for ry in sorted(reports, reverse=True):  # 최신 보고서 우선(재작성 반영)
        for yr, vals in _parse_report(reports[ry], ry).items():
            slot = data.setdefault(yr, {})
            for c, v in vals.items():
                if (c not in slot or pd.isna(slot.get(c))) and not pd.isna(v):
                    slot[c] = v

    df = pd.DataFrame.from_dict(data, orient="index").sort_index()
    # 매출 또는 자산총계 중 하나라도 있는 연도만 (은행 등 매출 개념 없는 업종 대응)
    mask = pd.Series(False, index=df.index)
    for c in ("revenue", "total_assets", "net_income"):
        if c in df.columns:
            mask = mask | df[c].notna()
    df = df[mask] if mask.any() else df
    df = df.tail(HISTORY_YEARS)
    fallback_dates = pd.Series(
        [pd.Timestamp(int(y), 12, 31) for y in df.index], index=df.index
    )
    if "fiscal_end" not in df.columns:
        df["fiscal_end"] = fallback_dates
    else:
        df["fiscal_end"] = pd.to_datetime(df["fiscal_end"], errors="coerce").fillna(fallback_dates)
    df.attrs["fs_div"] = fs_div
    return df


def get_dart_financials(stock_code: str) -> tuple[pd.DataFrame | None, str, list[str]]:
    """(DataFrame, 출처라벨, 경고들). 키 없음·실패 시 (None, '', [경고])."""
    if not get_api_key():
        return None, "", []  # 키 없으면 조용히 폴백 (경고는 provider가 판단)
    try:
        df = _dart_financials_df(stock_code)
    except Exception as e:
        return None, "", [f"OpenDART 재무 조회 실패({type(e).__name__}) — yfinance 재무를 사용합니다."]
    if df is None or df.empty:
        return None, "", ["OpenDART에 연간 재무제표가 없어 yfinance를 사용합니다."]
    fs = "연결" if df.attrs.get("fs_div") == "CFS" else "별도"
    return df, f"DART {fs}(공시 원본)", []
