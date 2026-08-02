"""규칙 기반 해설 엔진 — "왜 저평가/고평가로 보이는가"를 문장으로 설명.

핵심 목표: 낮은 멀티플이 '기회'인지 '밸류트랩(성장·수익성 훼손의 반영)'인지 구분 근거 제시.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.models import CompanyData, currency_mismatch
from .capital_cost import CapitalCost
from .indicators import Indicators
from .scoring import CategoryScores, peer_median, sanitize_peer_frame
from .valuation import ValuationResult


# 해설의 두 무리 — 성격이 달라서 한 목록에 두면 서로를 가린다(#68).
#   basis   판정의 **근거**. "PER이 업종 중앙값보다 39% 높다" 같은 관찰.
#   reading 판정을 **읽는 법**. "종합의 80%가 배수 하나에 의존한다" 같은 계산의 자기고백.
# 둘을 섞으면 "판정을 보수적으로 해석하세요"가 ROE 추이 아래에 놓인다.
GROUP_BASIS = "basis"
GROUP_READING = "reading"

# 근거 해설이 **무엇을 말하는가**. 판정과 근거가 반대인지 세려면 이게 필요하다 —
# "부채비율 31%로 안정적"(good)과 "PER이 업종 대비 39% 비싸다"(bad)를 함께 세면
# 3:3으로 상쇄돼 아무 충돌도 없는 것처럼 보인다. 실제로는 가격 수준을 말하는 셋만
# 판정과 반대 방향이다. 무엇을 말하는지는 문장을 뒤져서가 아니라 **만들 때 붙인다**.
ABOUT_PRICE = "price"       # 지금 가격이 싼가 비싼가
ABOUT_QUALITY = "quality"   # 회사가 튼튼한가 (가격과 무관)


@dataclass
class Comment:
    kind: str   # good | bad | warn | info
    text: str
    group: str = GROUP_BASIS
    about: str = ABOUT_QUALITY


def _pct(v, digits=1):
    return f"{v * 100:.{digits}f}%" if v is not None else "N/A"


def _x(v, digits=1):
    return f"{v:.{digits}f}배" if v is not None else "N/A"


def build_commentary(d: CompanyData, ind: Indicators, scores: CategoryScores,
                     cc: CapitalCost, val: ValuationResult) -> list[Comment]:
    out: list[Comment] = []
    peers = sanitize_peer_frame(d.peers)
    v, p, g, s, c = (ind.valuation, ind.profitability, ind.growth,
                     ind.stability, ind.cashflow)

    per, per_med = v.get("per"), peer_median(peers, "per")
    roe = p.get("roe")
    rev_growth = g.get("rev_cagr3") if g.get("rev_cagr3") is not None else g.get("rev_yoy")
    roe_series = ind.series.get("roe")
    # 3년 전 대비 10% 이상 상대 하락일 때만 '하락 추세'로 판단
    roe_falling = (roe_series is not None and len(roe_series) >= 3
                   and roe_series.iloc[-1] < roe_series.iloc[-3] * 0.9)

    # 1) PER vs 업종
    if per and per_med:
        diff = per / per_med - 1
        if diff <= -0.25:
            out.append(Comment("good", f"PER {_x(per)}로 업종 중앙값({_x(per_med)})보다 "
                                       f"{_pct(-diff, 0)} 낮게 거래되고 있습니다.", about=ABOUT_PRICE))
        elif diff >= 0.25:
            out.append(Comment("bad", f"PER {_x(per)}로 업종 중앙값({_x(per_med)}) 대비 "
                                      f"{_pct(diff, 0)} 프리미엄에 거래되고 있습니다.", about=ABOUT_PRICE))
    elif per is None and currency_mismatch(d):
        # PER가 없는 이유가 적자가 아니라 통화 혼재일 때 '적자'라고 말하면 오독이다.
        out.append(Comment("warn", f"재무제표가 {currency_mismatch(d)}로 공시되고 주가는 "
                                   f"{d.currency}라(ADR 등) PER·PBR을 계산하지 않았습니다 — "
                                   "적자라서가 아닙니다."))
    elif per is None and not d.is_financial:
        out.append(Comment("warn", "순이익이 적자(또는 데이터 없음)라 PER를 계산할 수 없습니다. "
                                   "PSR·PBR 중심으로 판단해야 합니다."))

    # 2) 역사적 밴드 위치
    if val.per_percentile is not None:
        pct = val.per_percentile
        if pct <= 25:
            # 밴드 위치는 '적정가 대비 판단'이 아니라 '자기 역사 안에서의 위치'다.
            # 저평가/고평가가 아니라 싼/비싼 구간으로 부른다(R3 용어집).
            out.append(Comment("good", f"현재 PER는 자기 과거 밴드의 하위 {pct:.0f}% 구간 — "
                                       "자기 역사 대비 싼 구간입니다.", about=ABOUT_PRICE))
        elif pct >= 75:
            out.append(Comment("bad", f"현재 PER는 자기 과거 밴드의 상위 {100 - pct:.0f}% 구간 — "
                                      "자기 역사 대비 비싼 구간입니다.", about=ABOUT_PRICE))

    # 3) RIM 정당 PBR vs 실제 PBR
    if val.rim_fair_pbr and v.get("pbr"):
        fair, actual = val.rim_fair_pbr, v["pbr"]
        if actual < fair * 0.8:
            out.append(Comment("good", f"ROE {_pct(val.rim_roe)} 기준 정당 PBR는 "
                                       f"{fair:.2f}배인데 실제 PBR는 {actual:.2f}배 — "
                                       "수익력 대비 장부가치가 할인되어 있습니다.", about=ABOUT_PRICE))
        elif actual > fair * 1.25:
            out.append(Comment("bad", f"실제 PBR {actual:.2f}배가 ROE {_pct(val.rim_roe)} 기준 "
                                      f"정당 PBR({fair:.2f}배)를 웃돕니다 — "
                                      "현재 수익력만으로는 설명되지 않는 프리미엄입니다.", about=ABOUT_PRICE))

    # 4) 성장성
    if rev_growth is not None:
        if rev_growth < 0:
            out.append(Comment("bad", f"매출이 연평균 {_pct(rev_growth)}로 역성장 중입니다. "
                                      "낮은 멀티플의 상당 부분이 성장성 부재로 설명될 수 있습니다."))
        elif rev_growth >= 0.15:
            out.append(Comment("good", f"매출이 연평균 {_pct(rev_growth, 0)} 성장 중입니다."))

    # 5) 밸류트랩 vs 진짜 저평가 (핵심 규칙)
    cheap = per is not None and per_med is not None and per < per_med * 0.75
    deteriorating = (rev_growth is not None and rev_growth < 0) or roe_falling
    if cheap and deteriorating:
        why = "매출 역성장" if (rev_growth is not None and rev_growth < 0) else "ROE 하락 추세"
        out.append(Comment("warn", f"싸 보이지만 {why}가 진행 중 — 낮은 멀티플이 펀더멘털 훼손을 "
                                   "반영한 '밸류트랩'일 가능성을 점검하세요.", about=ABOUT_PRICE))
    elif cheap and not deteriorating and roe is not None and roe > 0.08:
        out.append(Comment("good", f"업종 대비 낮은 멀티플인데 ROE {_pct(roe)}·성장성이 유지되고 "
                                   "있어 순수한 저평가에 가까워 보입니다.", about=ABOUT_PRICE))

    # 6) ROE 수준·추세
    if roe is not None and roe_series is not None and len(roe_series) >= 3:
        direction = "하락" if roe_falling else "유지·개선"
        kind = "bad" if roe_falling and roe < 0.08 else "info"
        out.append(Comment(kind, f"연간 ROE 추이 {_pct(roe_series.iloc[-3])} → "
                                 f"{_pct(roe_series.iloc[-1])} ({direction}), "
                                 f"최근 12개월(TTM) 기준 {_pct(roe)}."))

    # 7) 이익의 질 (OCF/순이익)
    ocf_ni = c.get("ocf_ni")
    if ocf_ni is not None:
        if ocf_ni < 0.8:
            out.append(Comment("warn", f"영업현금흐름이 순이익의 {_pct(ocf_ni, 0)}에 그칩니다 — "
                                       "장부 이익 대비 현금 창출이 약해 이익의 질을 점검해야 합니다."))
        elif ocf_ni >= 1.1:
            out.append(Comment("good", f"영업현금흐름이 순이익의 {_pct(ocf_ni, 0)} — "
                                       "이익이 현금으로 잘 뒷받침됩니다."))

    # 8) FCF 적자 지속
    fcf_series = ind.series.get("fcf")
    if fcf_series is not None and len(fcf_series) >= 2 and (fcf_series.tail(2) < 0).all():
        out.append(Comment("warn", "잉여현금흐름(FCF)이 2년 연속 적자입니다. "
                                   "투자 부담이 크거나 현금 창출력이 약한 구간입니다."))

    # 9) 재무 안정성 — 부채비율이 높아도 이자 감당력이 충분하면 경고하지 않음
    dr = s.get("debt_ratio")
    ic = s.get("interest_coverage")
    if dr is not None:
        if dr > 2.0 and (ic is None or ic < 5):
            out.append(Comment("warn", f"부채비율 {_pct(dr, 0)} — 재무 부담이 큰 편입니다."))
        elif dr > 2.0:
            out.append(Comment("info", f"부채비율 {_pct(dr, 0)}로 높지만 이자보상배율이 "
                                       f"{ic:.0f}배라 상환 능력에는 여유가 있습니다."))
        elif dr < 0.5 and (s.get("net_debt_ebitda") or 0) < 0:
            out.append(Comment("good", f"부채비율 {_pct(dr, 0)}에 순현금 상태 — "
                                       "재무구조가 매우 안정적입니다."))
    if ic is not None and 0 < ic < 2:
        out.append(Comment("warn", f"이자보상배율 {ic:.1f}배 — 영업이익으로 이자를 감당하기 "
                                   "빠듯한 수준입니다."))

    # 10) ROIC vs WACC (가치창출)
    if cc.spread is not None and cc.roic is not None and cc.wacc is not None:
        if cc.spread > 0.02:
            out.append(Comment("good", f"ROIC {_pct(cc.roic)} > WACC {_pct(cc.wacc)} — "
                                       f"자본비용을 {_pct(cc.spread)}p 웃도는 초과수익을 창출 중입니다."))
        elif cc.spread < 0:
            out.append(Comment("bad", f"ROIC {_pct(cc.roic)} < WACC {_pct(cc.wacc)} — "
                                      "투하자본이 자본비용만큼도 벌지 못하고 있습니다(가치 잠식)."))

    # 11) 재무레버리지가 자본비용에 주는 부담
    if cc.financial_risk_premium is not None and cc.financial_risk_premium > 0.015:
        out.append(Comment("info", f"자기자본비용 {_pct(cc.k_e)} 중 {_pct(cc.financial_risk_premium)}p는 "
                                   f"재무레버리지(D/E {_pct(cc.de_ratio, 0)})에서 오는 재무위험 프리미엄입니다. "
                                   f"영업위험만의 자본비용은 {_pct(cc.k_u)}입니다."))

    # 12) 배당
    dy = v.get("div_yield")
    if dy is not None and dy > cc.rf:
        out.append(Comment("good", f"배당수익률 {_pct(dy)}가 무위험이자율({_pct(cc.rf)})을 웃돕니다."))

    # 13) 베타/변동성
    if cc.beta_l is not None and cc.beta_l >= 1.4:
        out.append(Comment("info", f"베타 {cc.beta_l:.2f} — 시장보다 변동성이 큰 종목이라 "
                                   "요구수익률이 높게 형성됩니다."))

    # 14) 금융업 안내
    if d.is_financial:
        out.append(Comment("info", "금융업 특성상 PBR·ROE 중심으로 평가했으며, "
                                   "EV/EBITDA·부채비율·WACC 등은 표시하지 않습니다."))

    # 근거 해설은 good → bad → warn → info 순으로 정렬해 읽기 쉽게.
    # (아래에서 붙일 '읽는 법' 무리는 이 정렬에 섞이지 않는다 — 섞이면 경고가 맨 뒤로 밀린다.)
    order = {"good": 0, "bad": 1, "warn": 2, "info": 3}
    out.sort(key=lambda x: order.get(x.kind, 9))

    # 15) 계산이 스스로 남긴 '이 판정을 읽는 법'.
    # 등급은 ValuationNote.kind가 데이터로 들고 있다 — 예전처럼 '주의 ·' 접두어를
    # 부분일치로 찾아 등급을 정하지 않는다(그 방식이 조용히 깨진 사례가 R3 발견 7이다).
    reading = [Comment(n.kind, n.text, GROUP_READING) for n in val.notes]
    reading.sort(key=lambda x: order.get(x.kind, 9))

    # 판정과 근거가 반대 방향이면, 왜 그런지를 '읽는 법' 맨 앞에 놓는다(#69).
    conflict = verdict_conflict(val, out)
    if conflict:
        reading.insert(0, Comment("warn", conflict.detail, GROUP_READING))

    return out + reading


@dataclass
class VerdictConflict:
    """판정과 근거가 반대일 때 화면이 할 말. 두 벌인 이유는 놓일 자리가 둘이라서다.

    ``short``  헤드라인 바로 아래 — 큰 글씨를 본 사람이 아래로 내려가기 전에 읽는다.
    ``detail`` '이 판정을 읽을 때' 블록 맨 앞 — 왜 반대인지를 끝까지 설명한다.
    """
    short: str
    detail: str


def verdict_conflict(val: ValuationResult, basis: list[Comment]) -> VerdictConflict | None:
    """헤드라인 판정과 근거 해설이 서로 반대를 말하면, 그 이유를 한 문장으로 돌려준다.

    실측(삼성전자 2026-07-29): 헤드라인은 "크게 저평가(+96%)"인데 아래 부정 해설 셋이
    전부 "비싸다"고 말한다. 둘 다 정직한 결과다 — **다른 이익을 보고 있을 뿐이다.**
    헤드라인은 컨센서스 12개월 선행 EPS(미래), 부정 해설은 현재 이익·현재 배수(지금).
    삼성전자의 선행 EPS는 TTM 대비 +227%라 미래 기준으로는 싸고 현재 기준으로는 비싸다.

    문제는 계산이 아니라 **화면이 이 둘을 나란히 놓고 침묵한다**는 것이었다(#69).
    None을 돌려주면 화면은 아무 말도 하지 않는다 — 반대가 아닌데 설명하면 그게 소음이다.
    """
    if not val.verdict:
        return None
    positive = "저평가" in val.verdict
    negative = "고평가" in val.verdict
    if not (positive or negative):
        return None

    priced = [c for c in basis if c.about == ABOUT_PRICE]
    good = sum(1 for c in priced if c.kind == "good")
    bad = sum(1 for c in priced if c.kind == "bad")
    # 근거가 판정과 반대로 기울어야 한다. 2건 이상이라야 '한 지표의 예외'가 아니다.
    if positive and not (bad >= 2 and bad > good):
        return None
    if negative and not (good >= 2 and good > bad):
        return None

    head = (f"위 판정은 '{val.verdict}'인데 아래 근거는 반대쪽을 가리킵니다 — "
            "둘 다 맞습니다. 보고 있는 이익이 다릅니다.")
    if val.forward_growth is not None and abs(val.forward_growth) >= 0.20:
        direction = "늘어날" if val.forward_growth > 0 else "줄어들"
        return VerdictConflict(
            short=(f"이 판정은 <b>미래 이익</b> 기준입니다(컨센서스 선행 EPS가 현 TTM 대비 "
                   f"{val.forward_growth:+.0%}). <b>지금 이익</b> 기준으로는 아래 근거가 "
                   f"{'비싸다' if positive else '싸다'}고 말합니다 — 둘 다 맞습니다."),
            detail=(f"{head} 판정은 컨센서스 12개월 선행 EPS(현 TTM 대비 "
                    f"{val.forward_growth:+.0%} — 앞으로 이익이 {direction} 것이라는 시장 전망)를 "
                    "반영한 종합이고, 아래 근거는 지금 이익·지금 배수로 본 관찰입니다. "
                    "전망이 빗나가면 판정도 함께 빗나갑니다 — 컨센서스 목표주가와 함께 보세요."))
    return VerdictConflict(
        short=("이 판정은 여러 방법의 <b>가중 종합</b>입니다 — 아래 근거는 반대쪽을 "
               "가리키니 둘을 함께 보세요."),
        detail=(f"{head} 판정은 여러 방법의 가중 종합이고, 아래 근거는 개별 지표를 "
                "하나씩 본 결과라 한쪽으로 몰릴 수 있습니다. 어느 쪽이 더 무겁다고 "
                "단정하지 말고 둘을 함께 읽으세요."))
