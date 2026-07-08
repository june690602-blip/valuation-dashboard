"""종목 뉴스 헤드라인 수집 — 키 불필요 (Google News RSS), 미국은 yfinance 폴백.

AI 분석 전 단계로, 제목·출처·날짜·링크만 모은다. 본문 크롤링은 하지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import xml.etree.ElementTree as ET

import requests

from .cache import file_cache

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fmt_date(rfc822: str | None) -> str:
    if not rfc822:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(rfc822).astimezone().strftime("%Y-%m-%d")
    except Exception:
        return rfc822[:16]


def _google_news(query: str, market: str, limit: int) -> list[dict]:
    if market == "KR":
        url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    else:
        url = f"https://news.google.com/rss/search?q={query}%20stock&hl=en-US&gl=US&ceid=US:en"
    r = requests.get(url, headers=_HEADERS, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = src_el.text.strip() if src_el is not None and src_el.text else ""
        # 구글뉴스 제목은 '헤드라인 - 매체' 형태가 많음 → 매체 분리
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        items.append({"title": title, "source": source,
                      "date": _fmt_date(it.findtext("pubDate")),
                      "link": (it.findtext("link") or "").strip()})
        if len(items) >= limit:
            break
    return items


def _yf_news(yahoo_ticker: str, limit: int) -> list[dict]:
    import yfinance as yf
    raw = getattr(yf.Ticker(yahoo_ticker), "news", None) or []
    items = []
    for n in raw:
        c = n.get("content", n)  # yfinance 버전별 구조 차이
        title = c.get("title") or n.get("title", "")
        if not title:
            continue
        prov = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else n.get("publisher", "")
        ts = n.get("providerPublishTime")
        date = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
        link = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else n.get("link", "")
        items.append({"title": title, "source": prov or "", "date": date, "link": link or ""})
        if len(items) >= limit:
            break
    return items


@file_cache("news", ttl_hours=6)
def fetch_news(name: str, market: str, yahoo_ticker: str, limit: int = 12) -> list[dict]:
    """헤드라인 목록. 실패해도 예외 대신 빈 리스트에 가깝게 동작."""
    q = requests.utils.quote(name)
    items: list[dict] = []
    try:
        items = _google_news(q, market, limit)
    except Exception:
        items = []
    if len(items) < 3 and market == "US":
        try:
            items = (items + _yf_news(yahoo_ticker, limit))[:limit]
        except Exception:
            pass
    # 중복 제목 제거
    seen, out = set(), []
    for it in items:
        k = it["title"][:40]
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out
