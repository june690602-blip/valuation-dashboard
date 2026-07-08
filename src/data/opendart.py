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
    "interest_expense": (("CF",), ["ifrs-full_InterestPaidClassifiedAsOperatingActivities"],
                         ["이자의지급", "이자지급"]),
    "total_assets": (("BS",), ["ifrs-full_Assets"], ["자산총계"]),
    "total_equity": (("BS",), ["ifrs-full_EquityAttributableToOwnersOfParent",
                               "ifrs-full_Equity"], ["지배기업의소유주에게귀속되는자본", "자본총계"]),
    "total_liabilities": (("BS",), ["ifrs-full_Liabilities"], ["부채총계"]),
    "current_assets": (("BS",), ["ifrs-full_CurrentAssets"], ["유동자산"]),
    "current_liabilities": (("BS",), ["ifrs-full_CurrentLiabilities"], ["유동부채"]),
    "cash": (("BS",), ["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산"]),
    "ocf": (("CF",), ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
            ["영업활동현금흐름", "영업활동으로인한현금흐름"]),
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
    """account_id 우선 → 한글명 키워드 순으로 첫 매칭 행."""
    for aid in ids:
        for r in rows:
            if r.get("sj_div") in sj_set and r.get("account_id") == aid:
                return r
    for kw in keywords:
        for r in rows:
            if r.get("sj_div") in sj_set and kw in (r.get("account_nm") or "").replace(" ", ""):
                return r
    return None


def _fetch_report(key: str, corp: str, year: int) -> dict | None:
    """단일 연간 보고서(전체 재무제표). 연결(CFS) 우선, 없으면 별도(OFS)."""
    for fs in ("CFS", "OFS"):
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
    for col, (sj, ids, kws) in DART_MAP.items():
        row = _find_row(rows, set(sj), ids, [k.replace(" ", "") for k in kws])
        if not row:
            continue
        out[base][col] = _num(row.get("thstrm_amount"))
        out[base - 1][col] = _num(row.get("frmtrm_amount"))
        out[base - 2][col] = _num(row.get("bfefrmtrm_amount"))
    return out


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
    older = _fetch_report(key, corp, base - 3)  # 3년 앞 보고서로 이력 연장
    if older:
        reports[base - 3] = older

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
    df = df.tail(6)
    df["fiscal_end"] = [pd.Timestamp(int(y), 12, 31) for y in df.index]
    df.attrs["fs_div"] = reports[base].get("_fs", "CFS")
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
