"""Gemini 기반 AI 분석 — 업종분류·뉴스분석·종합 투자평가.

원칙:
- 숫자는 프롬프트에 제공된 값만 인용하도록 지시(환각 방지).
- 결과는 한국어 마크다운. 항상 '투자 조언이 아님' 면책을 포함.
- 키가 없거나 실패하면 호출부가 폴백을 처리하도록 예외를 그대로 올린다.
"""
from __future__ import annotations

import json

from ..data.gemini import generate_text

DISCLAIMER = ("\n\n> ⚠️ 위 내용은 공개정보를 바탕으로 한 AI 생성 참고 의견이며 투자 조언이 아닙니다. "
              "수치는 대시보드가 계산한 값을 인용했으나 오차가 있을 수 있으니 반드시 원문·공시로 교차 확인하세요.")


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    return t.strip()


# ── ① 업종분류 + 피어 후보 ──────────────────────────────────────────
def classify_peers(name: str, market: str, hint_industry: str = "") -> dict:
    """{'sector','industry','peers':[{'name','ticker'}...]} 반환. 같은 시장 상장사 위주."""
    if market == "KR":
        mkt = "한국(KOSPI/KOSDAQ)"
        tk_rule = "ticker는 6자리 종목코드(예: 005930)"
    else:
        mkt = "미국(NYSE/NASDAQ)"
        tk_rule = "ticker는 거래소 심볼(예: AAPL)"
    prompt = f"""너는 주식 애널리스트다. 아래 기업의 실제 주력 사업을 기준으로 업종을 분류하고,
{mkt}에 상장된 **직접 경쟁사/동종기업**을 골라라. 지주회사면 실질 사업을 기준으로 판단한다.

기업명: {name}
참고용 기존 분류(부정확할 수 있음): {hint_industry or '없음'}

아래 JSON만 출력(설명 금지). {tk_rule}:
{{"sector": "대분류(예: 반도체, 자동차, 인터넷서비스, 은행)",
  "industry": "구체 업종",
  "peers": [{{"name": "동종 상장사명", "ticker": "코드/심볼"}}]}}
peers는 입력 기업을 제외하고 {mkt} 상장사만 8~12개."""
    raw = generate_text(prompt, temperature=0.2, max_tokens=1024, json_out=True)
    data = json.loads(_strip_json(raw))
    peers = []
    for p in data.get("peers", []):
        if isinstance(p, dict) and (p.get("name") or p.get("ticker")):
            peers.append({"name": str(p.get("name", "")).strip(),
                          "ticker": str(p.get("ticker", "")).strip()})
        elif isinstance(p, str) and p.strip():
            peers.append({"name": p.strip(), "ticker": ""})
    return {"sector": (data.get("sector") or "").strip(),
            "industry": (data.get("industry") or "").strip(),
            "peers": peers[:12]}


# ── ①-b 기업 소개 번역 ──────────────────────────────────────────────
def translate_overview(name: str, text: str) -> str:
    """영문 기업 소개(yfinance longBusinessSummary)를 한국어로 번역. 실패 시 예외(호출부 영문 폴백).

    번역만 하고 사실을 더하지 않는다(환각 방지) — AI 역할을 '생성'이 아닌 '변환'으로 제한.
    """
    prompt = f"""다음은 '{name}'의 영문 기업 소개다. **사실을 더하거나 빼지 말고** 한국어로 자연스럽게
번역하라. 3~5문장으로 간결하게 정리하되 사업 부문·주요 제품명은 유지한다. 번역문만 출력.

{text[:2000]}"""
    return generate_text(prompt, temperature=0.1, max_tokens=800)


# ── ② 뉴스 분석 ─────────────────────────────────────────────────────
def analyze_news(name: str, items: list[dict]) -> str:
    if not items:
        raise RuntimeError("분석할 뉴스가 없습니다.")
    lines = "\n".join(f"- ({it.get('date','')}) {it.get('title','')} [{it.get('source','')}]"
                      for it in items[:15])
    prompt = f"""너는 주식 애널리스트다. '{name}' 관련 최근 뉴스 헤드라인이다. 헤드라인만으로
과잉해석하지 말고, 투자자에게 유용하게 한국어 마크다운으로 정리하라.

[헤드라인]
{lines}

다음 순서로 간결하게:
### 전반 뉴스 감성
한 줄로 긍정/중립/부정 + 근거.
### 핵심 이슈 3~5가지
각 줄 앞에 📈(호재)/📉(악재)/⚪(중립) 이모지.
### 잠재적 촉매(주가를 움직일 이벤트)
### 리스크·주의점
### 한 줄 투자 관점
헤드라인이 애매하면 단정하지 말고 '확인 필요'라고 적어라."""
    return generate_text(prompt, temperature=0.45, max_tokens=1400) + DISCLAIMER


# ── ②-b 뉴스 카테고리 분류 (거시/산업/기업 + 태그) ──────────────────
NEWS_CATEGORIES = ("기업", "산업", "거시")
EVENT_TAGS = ("실적", "수주·계약", "M&A", "규제·소송", "지배구조", "신제품")
PEST_TAGS = ("정책 P", "경제 E", "사회 S", "기술 T")

# 키워드 폴백 규칙 — Gemini 키가 없거나 실패해도 분류가 동작해야 한다(무키 원칙)
_MACRO_KW = ("기준금리", "금리", "물가", "인플레", "CPI", "환율", "연준", "Fed", "FOMC",
             "한국은행", "한은", "GDP", "수출", "무역", "국채", "경기", "고용", "실업")
_EVENT_KW = {
    "실적": ("실적", "영업이익", "매출", "순이익", "어닝", "컨센서스"),
    "수주·계약": ("수주", "계약", "공급", "납품", "협약"),
    "M&A": ("인수", "합병", "M&A", "지분 인수", "매각"),
    "규제·소송": ("규제", "소송", "제재", "과징금", "조사", "리콜"),
    "지배구조": ("지배구조", "배당", "자사주", "총수", "승계", "주주"),
    "신제품": ("출시", "신제품", "공개", "양산", "개발 성공"),
}
_PEST_KW = {
    "정책 P": ("정책", "규제", "법안", "정부", "선거", "관세", "제재"),
    "경제 E": ("금리", "물가", "환율", "GDP", "수출", "고용", "경기", "국채"),
    "사회 S": ("인구", "고령화", "소비 트렌드", "노조", "파업", "안전"),
    "기술 T": ("AI", "인공지능", "반도체 기술", "특허", "R&D", "신기술"),
}


def keyword_classify_news(name: str, sector: str, items: list[dict]) -> list[dict]:
    """키워드 규칙만으로 분류(폴백·순수 함수). 각 항목에 category·tags를 붙여 반환."""
    out = []
    sec_words = [w for w in (sector or "").replace("·", " ").split() if len(w) >= 2]
    for it in items:
        title = it.get("title", "")
        if name and name in title:
            cat = "기업"
        elif any(k in title for k in _MACRO_KW):
            cat = "거시"
        elif any(w in title for w in sec_words):
            cat = "산업"
        else:
            cat = "기업"  # 종목명 검색 결과가 대부분이므로 기본값은 기업
        tag_src = _PEST_KW if cat == "거시" else _EVENT_KW
        tags = [t for t, kws in tag_src.items() if any(k in title for k in kws)][:2]
        out.append({**it, "category": cat, "tags": tags})
    return out


def classify_news_categories(name: str, sector: str, items: list[dict]) -> list[dict]:
    """Gemini로 헤드라인을 거시/산업/기업 + 태그로 일괄 분류. 실패 시 예외(호출부 폴백).

    태그: 기업·산업 기사는 이벤트 태그, 거시 기사는 PEST 태그를 붙인다.
    """
    if not items:
        return []
    lines = "\n".join(f"{i}. {it.get('title', '')}" for i, it in enumerate(items))
    prompt = f"""너는 투자분석가다. '{name}'(업종: {sector or '불명'}) 관련 뉴스 헤드라인을 분류하라.

category는 셋 중 하나:
- "기업": 이 회사 자체의 소식 (실적·수주·신제품·지배구조 등)
- "산업": 업종·경쟁사·시장 전반의 소식
- "거시": 금리·물가·환율·정책 등 경제 전반

tags 규칙 (0~2개):
- 기업/산업 기사: {", ".join(EVENT_TAGS)} 중에서
- 거시 기사: {", ".join(PEST_TAGS)} 중에서 (PEST 분석 관점)

[헤드라인]
{lines}

아래 JSON만 출력(설명 금지). 모든 번호를 포함:
{{"items": [{{"i": 0, "category": "기업", "tags": ["실적"]}}]}}"""
    raw = generate_text(prompt, temperature=0.1, max_tokens=1600, json_out=True)
    data = json.loads(_strip_json(raw))
    by_idx = {}
    for r in data.get("items", []):
        try:
            i = int(r.get("i"))
        except (TypeError, ValueError):
            continue
        cat = str(r.get("category", "")).strip()
        tags = [str(t).strip() for t in (r.get("tags") or []) if str(t).strip()][:2]
        if cat in NEWS_CATEGORIES:
            by_idx[i] = (cat, tags)
    out = []
    for i, it in enumerate(items):
        cat, tags = by_idx.get(i, ("기업", []))
        out.append({**it, "category": cat, "tags": tags})
    return out


# ── ③ 종합 투자평가 ─────────────────────────────────────────────────
def investment_opinion(context: str) -> str:
    prompt = f"""너는 근거를 명확히 제시하는 주식 애널리스트다. 아래는 대시보드가 실제로 계산한
사실이다. 이 사실에 근거해 현재 밸류에이션과 위험 요인을 한국어 마크다운으로 설명하라.

[중요 원칙]
- 아래에 제공된 수치(적정주가 범위·상승여력·52주 범위·업종 백분위 등)는 **적극적으로 인용**하라.
- 제공된 적정주가 범위는 **추천 목표가가 아니라 모형별 추정 범위**라고 명시하고,
  현재가와의 괴리율(%)을 계산해 밝혀라.
- 52주 최저·역사적 지지 수준은 매매 지시가 아니라 현재 관찰을 재검토할 기준으로 설명하라.
- 다만 **제공되지 않은 완전히 새로운 수치**(예: 존재하지 않는 미래 실적 추정)는 지어내지 말 것.
- 3가지 방법의 편차가 크면(신뢰도 낮음) 그 불확실성도 함께 밝혀라.

[분석 컨텍스트]
{context}

아래 형식(굵은 소제목 유지, 각 항목 2~4문장):
### 📌 한 줄 관찰
상태 [큰 저평가 관찰 / 저평가 관찰 / 적정 범위 관찰 / 고평가 관찰 / 큰 고평가 관찰] + 핵심 이유 한 문장.
### ✅ 강세 논거
### ⚠️ 약세 논거·리스크
밸류트랩·재무·뉴스 리스크 포함.
### 🎯 적정가 추정 범위와 괴리율
제공된 적정주가 범위를 모형 추정치로 제시하고 현재가 대비 괴리율(%)과 신뢰도를 명시.
### 🛡️ 관찰을 재검토할 기준
52주 최저·지지선 등 현재 해석이 약해질 수 있는 조건을 구체적으로 제시.
### 🧭 투자성향별 맞춤
컨텍스트에 '사용자 투자성향'이 있으면 그 성향(위험회피계수·권장 위험자산 비중)에 맞춰 이 종목의
어떤 변동성·집중위험을 더 확인해야 하는지 설명하라. 없으면 안정형/공격형 관점에서 한 줄씩.
### 👀 지켜볼 것
촉매·체크포인트."""
    return generate_text(prompt, temperature=0.35, max_tokens=2048) + DISCLAIMER


# ── 투자평가용 컨텍스트 빌더 (순수 함수) ────────────────────────────
def build_opinion_context(d, ind, val, cc, scores, news_summary: str = "",
                          risk_profile: dict | None = None) -> str:
    """CompanyData·분석결과 → Gemini에 넣을 사실 요약 텍스트.

    risk_profile: 투자성향 테스트 결과(session_state["risk_profile"]) — 있으면 성향 맞춤용으로 주입.
    """
    def pct(v):
        return f"{v*100:.1f}%" if isinstance(v, (int, float)) else "N/A"

    def x(v):
        return f"{v:.1f}x" if isinstance(v, (int, float)) else "N/A"

    cur = f"{d.currency}"
    v = ind.valuation
    p = ind.profitability
    g = ind.growth
    lines = [
        f"기업: {d.name} ({d.ticker}), 시장 {d.market}, 업종 {d.sector or d.industry}",
        f"현재가 {d.price:,.0f} {cur}, 시가총액 {d.market_cap:,.0f}",
        f"종합 판정: {val.verdict} (적정가 대비 괴리율 {pct(val.gap)}, 신뢰도 {val.confidence})",
    ]
    # 적정주가(목표가 근거) + 상승여력
    if val.fair_mid:
        upside = val.fair_mid / d.price - 1 if d.price else None
        lines.append(
            f"적정주가 범위(3방법 평균, =목표가 근거): {val.fair_low:,.0f} ~ {val.fair_high:,.0f} {cur}"
            + (f", 중심 {val.fair_mid:,.0f} → 현재가 대비 상승여력 {pct(upside)}" if upside is not None else ""))
        # 방법별 세부 (편차=신뢰도 판단 근거)
        if getattr(val, "estimates", None):
            per_method = "; ".join(f"{e.method} {e.mid:,.0f}" for e in val.estimates)
            lines.append(f"방법별 적정가 중심: {per_method}")
    else:
        lines.append("적정주가: 계산 불가")
    # 52주 범위 (현재 관찰을 재검토할 가격 기준)
    try:
        c = d.prices.tail(252)
        hi52, lo52 = float(c.max()), float(c.min())
        pos = (d.price - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else None
        lines.append(f"52주 최고/최저: {hi52:,.0f} / {lo52:,.0f} {cur}"
                     + (f", 현재 밴드 내 위치 {pos:.0f}%(0=최저,100=최고)" if pos is not None else ""))
    except Exception:
        pass
    lines += [
        f"밸류에이션: PER {x(v.get('per'))}, PBR {x(v.get('pbr'))}, "
        f"EV/EBITDA {x(v.get('ev_ebitda'))}, 배당수익률 {pct(v.get('div_yield'))}",
        f"수익성: ROE {pct(p.get('roe'))}, 영업이익률 {pct(p.get('op_margin'))}, "
        f"순이익률 {pct(p.get('net_margin'))}",
        f"성장성: 매출 3년CAGR {pct(g.get('rev_cagr3'))}, EPS 3년CAGR {pct(g.get('eps_cagr3'))}",
        f"재무: 부채비율 {pct(ind.stability.get('debt_ratio'))}, "
        f"이자보상배율 {x(ind.stability.get('interest_coverage'))}",
        f"자본비용: WACC {pct(cc.wacc)}, ROIC {pct(cc.roic)}, "
        f"스프레드 {pct(cc.spread)} (양수면 가치창출), 베타 {cc.beta_l:.2f}"
        if cc.beta_l else "자본비용: 계산 제한",
    ]
    if scores.overall is not None:
        cat = ", ".join(f"{k} {int(s)}" for k, s in scores.scores.items() if s is not None)
        lines.append(f"업종 상대 백분위(0~100): 종합 {int(scores.overall)} [{cat}]")
    if risk_profile:
        y = risk_profile.get("y_star")
        lines.append(
            f"\n[사용자 투자성향] {risk_profile.get('label','')}"
            f"(위험회피계수 A≈{risk_profile.get('A', 0):.1f}"
            + (f", 권장 위험자산 비중 {y*100:.0f}%" if isinstance(y, (int, float)) else "") + ")")
    if news_summary:
        lines.append(f"\n[최근 뉴스 요약]\n{news_summary[:1200]}")
    return "\n".join(lines)
