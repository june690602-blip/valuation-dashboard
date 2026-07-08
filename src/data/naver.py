"""네이버 금융 모바일 API — 한국 종목 공식 멀티플 (키 불필요).

pykrx가 KRX 로그인 계정을 요구하게 되어, PER/PBR/EPS/BPS/배당수익률은
네이버 금융이 게시하는 값(KRX 공시 기반)을 사용한다.
"""
from __future__ import annotations

import re

import requests

from .cache import file_cache

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_UNIT = {"조": 1e12, "억": 1e8, "만": 1e4}


def _parse_number(text: str | None) -> float | None:
    """'22.35배' '12,372원' '0.60%' 'N/A' → float | None (단위 그대로, %는 소수 아님)."""
    if not text:
        return None
    t = str(text).strip()
    if t in ("N/A", "-", "", "null"):
        return None
    t = t.replace(",", "").replace("배", "").replace("원", "").replace("%", "")
    try:
        return float(t)
    except ValueError:
        return None


def _parse_korean_amount(text: str | None) -> float | None:
    """'1,616조 4,960억' → 1.61649600e15"""
    if not text:
        return None
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d,\.]+)\s*(조|억|만)", str(text)):
        total += float(num.replace(",", "")) * _UNIT[unit]
        found = True
    return total if found else _parse_number(text)


@file_cache("naver_fund", ttl_hours=12)
def fetch_naver_fundamental(code: str) -> dict:
    """종목 하나의 네이버 공시 지표. 실패 시 예외 → 호출부에서 폴백."""
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    r = requests.get(url, headers=_HEADERS, timeout=15)
    r.raise_for_status()
    j = r.json()
    infos = {i.get("code"): i.get("value") for i in j.get("totalInfos", [])}

    per = _parse_number(infos.get("per"))
    eps = _parse_number(infos.get("eps"))
    pbr = _parse_number(infos.get("pbr"))
    bps = _parse_number(infos.get("bps"))
    div = _parse_number(infos.get("dividendYieldRatio"))
    out = {
        "name": j.get("stockName"),
        "per": per if per and per > 0 else None,
        "forward_per": _parse_number(infos.get("cnsPer")),
        "eps": eps,
        "bps": bps,
        "pbr": pbr if pbr and pbr > 0 else None,
        "div_yield": (div / 100.0) if div is not None else None,  # % → 소수
        "dps": _parse_number(infos.get("dividend")),
        "market_cap": _parse_korean_amount(infos.get("marketValue")),
        "roe_approx": (eps / bps) if eps is not None and bps and bps > 0 else None,
        "source": "네이버금융(KRX 공시 기반)",
    }
    return out
