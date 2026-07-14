"""종목 뉴스 헤드라인 수집 — 키 불필요 (Google News RSS), 미국은 yfinance 폴백.

AI 분석 전 단계로, 제목·출처·날짜·링크만 모은다. 본문 크롤링은 하지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import xml.etree.ElementTree as ET

import requests

from .cache import file_cache

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 블로그·카페·개인 게시물 등 '기업 뉴스'로 보기 어려운 출처. 종목명만 스쳐도 검색에 걸리는
# 잡음의 주범이라 출처 이름(소문자)에 아래 조각이 들어가면 제외한다.
_SOURCE_DENY = (
    "blog", "블로그", "tistory", "티스토리", "brunch", "브런치",
    "cafe", "카페", "post.naver", "네이버 포스트", "velog", "wordpress",
)

# 대형 언론 보강용(한국) — 종목 기사는 경제 전문지·통신사가 물량을 압도해 종합지가 묻히기 쉬움.
# site: 질의로 별도 수집해 목록 앞쪽에 소량 섞는다.
_MAJOR_SITES_KR = ("chosun.com", "joongang.co.kr", "donga.com", "hani.co.kr",
                   "khan.co.kr", "mk.co.kr", "hankyung.com")


def _bad_source(source: str) -> bool:
    s = (source or "").lower()
    return any(bad in s for bad in _SOURCE_DENY)


def _name_variants(name: str) -> set[str]:
    """제목 관련성 판정을 위한 회사명 변형 집합(소문자). 법인 접미사를 떼어 매칭률을 높인다."""
    base = (name or "").strip()
    out = {base}
    for suf in (" Inc.", " Inc", " Corporation", " Corp.", " Corp", " Co., Ltd.",
                " Co.", " Co", " Ltd.", " Ltd", " Limited", " plc", " Company",
                " Holdings", " Group", " Class A", " Class C"):
        if base.lower().endswith(suf.lower()):
            out.add(base[:-len(suf)].strip())
    for suf in ("(주)", "㈜", "주식회사"):
        out.add(base.replace(suf, "").strip())
    return {t.lower() for t in out if len(t) >= 2}


def _title_relevant(title: str, variants: set[str]) -> bool:
    t = (title or "").lower()
    return any(v in t for v in variants)


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


def _dedup(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        k = it["title"][:40]
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


@file_cache("topic_news", ttl_hours=6)
def fetch_topic_news(query: str, market: str = "KR", limit: int = 10) -> list[dict]:
    """토픽 헤드라인(예: '기준금리 OR 국고채') — 채권·거시 뉴스용. 실패 시 빈 리스트."""
    try:
        items = _google_news(requests.utils.quote(query), market, limit * 3)
    except Exception:
        return []
    # 토픽은 제목-회사명 관련성 판정 대상이 아니지만, 블로그·카페 잡음은 여기서도 걷어낸다.
    items = [it for it in items if not _bad_source(it["source"])]
    return _dedup(items)[:limit]


@file_cache("news", ttl_hours=6)
def fetch_news(name: str, market: str, yahoo_ticker: str, limit: int = 12) -> list[dict]:
    """기업 헤드라인. 종목명이 제목에 실제로 들어간 기사만(블로그·카페 출처 제외) 남겨,
    '키워드만 스친' 무관한 글이 섞이지 않게 한다. 실패해도 예외 대신 빈 리스트에 가깝게 동작."""
    variants = _name_variants(name)
    # 금융 맥락 힌트를 붙여 생활·블로그성 잡음을 줄인다(결과가 적으면 원 질의로 폴백).
    if market == "KR":
        qualified = requests.utils.quote(f"{name} 주가 OR 실적 OR 공시 OR 증권")
    else:
        qualified = requests.utils.quote(f"{name} stock OR earnings OR shares")
    raw: list[dict] = []
    try:
        raw = _google_news(qualified, market, limit * 4)
    except Exception:
        raw = []
    if len({it["title"][:40] for it in raw}) < 5:
        try:
            raw += _google_news(requests.utils.quote(name), market, limit * 4)
        except Exception:
            pass

    # 1차: 출처가 블로그·카페가 아니고 + 제목에 회사명이 실제로 있는 것
    good_src = [it for it in raw if not _bad_source(it["source"])]
    kept = [it for it in good_src if _title_relevant(it["title"], variants)]
    # 제목 표기가 상장명과 다른 종목(약칭 'LG엔솔', 한/영 혼용 등)은 관련성 필터가 과해질 수
    # 있음 → 출처 필터만 적용한 목록으로 완화(질의 자체가 회사명 앵커라 대체로 관련 기사).
    if len(kept) < 3:
        kept = good_src or raw

    if len(kept) < 3 and market == "US":
        try:
            kept += _yf_news(yahoo_ticker, limit)
        except Exception:
            pass

    # 대형 언론 보강(한국): 종합지 기사를 별도 질의로 받아 앞쪽에 소량(최대 limit/4) 섞는다.
    majors: list[dict] = []
    if market == "KR":
        try:
            mq = requests.utils.quote(f"{name} (" + " OR ".join(f"site:{s}" for s in _MAJOR_SITES_KR) + ")")
            majors = [it for it in _google_news(mq, market, limit)
                      if _title_relevant(it["title"], variants)][:max(2, limit // 4)]
        except Exception:
            majors = []
    return _dedup(majors + kept)[:limit]
