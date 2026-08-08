"""Piotroski F-Score — 재무제표 9개 신호의 이진 합산 (Piotroski 2000).

**이 모듈은 적정주가를 만들지 않는다.** F-Score는 가격이 아니라 0~9점이고, 쓰임은
'싸다'를 *'싸고 좋아지는 중'*과 *'싸고 나빠지는 중'*으로 가르는 **품질 층**이다.
문헌에서 적정주가를 내는 계보는 할인 모형(Ohlson/Penman)과 정당화된 배수(Damodaran)
둘뿐이고 F-Score는 줄 세우는 도구다(이슈 #132). 그래서 `METHOD_WEIGHTS`에 등록하지
않고 `compute_valuation()`도 부르지 않는다 — 가중치를 받는 순간 가격 구성요소가 된다.

지을 값이 있는지 먼저 재고(B1 개정 4), **재는 코드가 곧 근거가 되게 `src/`에 둔다** —
`epv.py`가 같은 선택을 했다(스크립트에 두면 시험할 자리가 없어진다).

정규화는 **기초 총자산**(전기말)으로 한다 — Piotroski 원안이다. 그래서 t년 점수는
회계연도 t·t−1·t−2 **셋**을 요구한다(ΔROA가 t−1의 기초자산까지 쓴다). 문헌 정합성을
택한 것이고(ADR-0025와 같은 성격) 대가는 커버리지다.

각 신호는 1 / 0 / **None**(못 세움) 셋이다. 못 세운 신호를 0으로 두면 *데이터가 없다*가
*나쁘다*로 읽힌다 — 무료 데이터에서 그 오독은 체계적 편향이 된다(ADR-0011).
그래서 분모(`max_score`)를 함께 낸다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 9개 중 이만큼은 서야 점수를 낸다. 이보다 적으면 '점수'라고 부를 수 없다 —
# 4/5점과 4/9점은 다른 말인데 화면에서 같아 보인다.
FSCORE_MIN_SIGNALS = 7

# 신주발행 판정의 여유폭. 원 정의는 "직전 연도에 보통주를 발행했는가"인데 우리에게는
# 발행 이벤트가 없어 유통주식수 증가로 대신 본다. 그런데 `shares_outstanding`이
# **기간 평균**이라 증자가 없어도 소수점이 흔들린다(ADR-0028 한계 4의 드리프트).
# 0% 기준이면 잡음만으로 대량 탈락한다. **측정값이 아니라 판단값이다**
# (`BAND_CORR_LIMIT`·`PBR_GATE`와 같은 성격) — B1 개정 4가 이 신호를 뺀 8점 척도도
# 함께 내라고 적은 이유가 이것이다.
EQ_OFFER_TOL = 0.01

# 신호 이름 — 화면·연구 스크립트가 같은 이름을 쓰게 공개한다. Piotroski 원논문의
# 세 범주 순서를 유지한다(수익성 4 · 재무건전성 3 · 운영효율 2).
SIGNAL_NAMES = ("ROA", "CFO", "DELTA_ROA", "ACCRUAL",
                "DELTA_LEVER", "DELTA_LIQUID", "EQ_OFFER",
                "DELTA_MARGIN", "DELTA_TURN")

# 이 신호를 세우려면 있어야 하는 컬럼들 — 커버리지 진단이 원인을 말할 수 있게 남긴다.
SIGNAL_INPUTS = {
    "ROA": ("net_income", "total_assets"),
    "CFO": ("ocf", "total_assets"),
    "DELTA_ROA": ("net_income", "total_assets"),
    "ACCRUAL": ("ocf", "net_income"),
    "DELTA_LEVER": ("total_debt", "total_assets"),
    "DELTA_LIQUID": ("current_assets", "current_liabilities"),
    "EQ_OFFER": ("shares_outstanding",),
    "DELTA_MARGIN": ("gross_profit", "revenue"),
    "DELTA_TURN": ("revenue", "total_assets"),
}


def _num(v) -> float | None:
    """숫자로 못 읽히거나 유한하지 않으면 None. 무료 데이터에는 둘 다 섞인다."""
    x = pd.to_numeric(v, errors="coerce")
    try:
        return None if not np.isfinite(x) else float(x)
    except TypeError:
        return None


def _ratio(num: float | None, den: float | None) -> float | None:
    """분모가 없거나 0 이하면 None. 총자산·매출·유동부채가 0 이하인 행은 쓸 수 없다."""
    if num is None or den is None or den <= 0:
        return None
    return num / den


def _rose(now: float | None, before: float | None) -> int | None:
    """'올랐는가'를 1/0으로. 한쪽이라도 없으면 None — 0으로 두지 않는다."""
    if now is None or before is None:
        return None
    return int(now > before)


def _pick(fin, col: str, i: int) -> float | None:
    """fin의 뒤에서 i번째 행(`i=0`이 최신)의 col 값. 없으면 None."""
    if col not in fin.columns or len(fin) <= i:
        return None
    return _num(fin[col].iloc[-1 - i])


def fscore(fin) -> dict | None:
    """(dict) F-Score. 신호가 `FSCORE_MIN_SIGNALS` 미만이면 **None**.

    ``fin``: `FIN_COLUMNS` 규격의 재무 프레임(index=회계연도, **과거→최신**).
    **시점 통제는 호출부의 몫이다** — 이 함수는 넘겨받은 프레임의 마지막 행을 t로 본다.
    백테스트는 공시일로 자른 프레임을 넘기므로 룩어헤드가 여기서 생기지 않는다
    (`backtest_panel.py`의 `effective_filed` 경로를 그대로 탄다).

    반환::

        {"score": 7, "max_score": 9, "ratio": 0.78,
         "signals": {"ROA": 1, "CFO": 1, ..., "EQ_OFFER": None},
         "fiscal_year": 2025, "years_used": 3,
         "score_ex_equity": 7, "max_ex_equity": 8}

    `score_ex_equity`는 7번(신주발행)을 뺀 8점 척도다 — 그 신호가 유통주식수 근사라
    9개 중 가장 약해서, 결론이 그 하나에 매달리지 않는지 볼 수 있게 함께 낸다
    (B1 개정 4 한계 1).
    """
    if fin is None or not hasattr(fin, "columns") or len(fin) < 2:
        return None

    # 기초 총자산 = 전기말 총자산. t와 t−1 두 해의 ROA를 재려면 t−1·t−2 자산이 필요하다.
    a_beg = _pick(fin, "total_assets", 1)      # t의 기초 = t−1 기말
    a_beg_prev = _pick(fin, "total_assets", 2)  # t−1의 기초 = t−2 기말

    ni, ni_prev = _pick(fin, "net_income", 0), _pick(fin, "net_income", 1)
    ocf = _pick(fin, "ocf", 0)
    rev, rev_prev = _pick(fin, "revenue", 0), _pick(fin, "revenue", 1)
    gp, gp_prev = _pick(fin, "gross_profit", 0), _pick(fin, "gross_profit", 1)

    roa, roa_prev = _ratio(ni, a_beg), _ratio(ni_prev, a_beg_prev)
    cfo = _ratio(ocf, a_beg)

    sig: dict[str, int | None] = {}

    # ── 수익성 4 ────────────────────────────────────────────────────
    sig["ROA"] = None if roa is None else int(roa > 0)
    sig["CFO"] = None if cfo is None else int(cfo > 0)
    sig["DELTA_ROA"] = _rose(roa, roa_prev)
    # 발생액: 영업현금흐름이 순이익을 넘는가. 같은 분모로 나눈 비교라 분모를 생략해도
    # 부호가 같다 — 총자산이 없어도 이 신호는 선다(원 정의와 결과가 같다).
    sig["ACCRUAL"] = None if (ocf is None or ni is None) else int(ocf > ni)

    # ── 재무건전성·자금조달 3 ────────────────────────────────────────
    # 근사 1: 원 정의는 장기부채인데 우리에게 그 항목이 없어 총차입금을 쓴다.
    # 단기차입 비중이 큰 회사에서 부호가 달라질 수 있다(B1 개정 4 근사 1).
    lever = _ratio(_pick(fin, "total_debt", 0), a_beg)
    lever_prev = _ratio(_pick(fin, "total_debt", 1), a_beg_prev)
    sig["DELTA_LEVER"] = _rose(lever_prev, lever)   # **줄어야** 1점 — 인자 순서가 뒤집혀 있다

    liq = _ratio(_pick(fin, "current_assets", 0), _pick(fin, "current_liabilities", 0))
    liq_prev = _ratio(_pick(fin, "current_assets", 1), _pick(fin, "current_liabilities", 1))
    sig["DELTA_LIQUID"] = _rose(liq, liq_prev)

    # 근사 2: 유통주식수가 늘지 않았으면 1점. EQ_OFFER_TOL 주석 참조.
    sh, sh_prev = _pick(fin, "shares_outstanding", 0), _pick(fin, "shares_outstanding", 1)
    if sh is None or sh_prev is None or sh_prev <= 0:
        sig["EQ_OFFER"] = None
    else:
        sig["EQ_OFFER"] = int(sh <= sh_prev * (1.0 + EQ_OFFER_TOL))

    # ── 운영효율 2 ──────────────────────────────────────────────────
    sig["DELTA_MARGIN"] = _rose(_ratio(gp, rev), _ratio(gp_prev, rev_prev))
    sig["DELTA_TURN"] = _rose(_ratio(rev, a_beg), _ratio(rev_prev, a_beg_prev))

    stood = {k: v for k, v in sig.items() if v is not None}
    if len(stood) < FSCORE_MIN_SIGNALS:
        return None

    ex_eq = {k: v for k, v in stood.items() if k != "EQ_OFFER"}
    fy = fin.index[-1]
    return {
        "score": int(sum(stood.values())),
        "max_score": int(len(stood)),
        "ratio": float(sum(stood.values()) / len(stood)),
        "signals": {k: sig[k] for k in SIGNAL_NAMES},
        "fiscal_year": int(fy) if isinstance(fy, (int, np.integer)) else fy,
        "years_used": int(min(3, len(fin))),
        "score_ex_equity": int(sum(ex_eq.values())),
        "max_ex_equity": int(len(ex_eq)),
    }


def missing_inputs(fin) -> dict[str, list[str]]:
    """못 세운 신호 → 없는 컬럼 목록. **커버리지 진단이 원인을 말하게** 하는 함수다.

    B1 개정 4가 *"가설을 재기 전에 신호별 커버리지를 먼저 낸다"*고 적었고, 그때
    "5번이 안 선다"만으로는 원인을 모른다 — `total_debt`가 없는 것과 총자산이 없는 것은
    고치는 방법이 다르다.
    """
    if fin is None or not hasattr(fin, "columns"):
        return {name: list(cols) for name, cols in SIGNAL_INPUTS.items()}
    out: dict[str, list[str]] = {}
    got = fscore(fin)
    sig = got["signals"] if got else {}
    for name in SIGNAL_NAMES:
        if got and sig.get(name) is not None:
            continue
        gone = [c for c in SIGNAL_INPUTS[name]
                if c not in fin.columns
                or not np.isfinite(pd.to_numeric(fin[c], errors="coerce")).any()]
        out[name] = gone
    return out
