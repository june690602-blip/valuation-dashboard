"""투자성향 테스트 — 문항·채점·위험회피계수(A)·최적 배분 (순수 함수).

이론 배경 (평균-분산 효용):
- 효용함수 U = E(r) − ½·A·σ²   (A = 위험회피계수, 클수록 위험을 싫어함)
- 최적 위험자산 비중 y* = (E(Rm) − Rf) / (A·σm²)   [머튼 비율]
- 최적점에서 무차별곡선이 CML에 접하고, 접점의 기울기(MRS = A·σ*)가
  CML 기울기(샤프비율 = (E(Rm)−Rf)/σm)와 같아진다.

분류는 표준투자권유준칙의 5단계(안정형~공격투자형)를 따르고,
문항은 일반형 5개 + 행동재무학형 3개(손실 대응·확실성등가·손실회피)로 구성.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# ── 문항 ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Question:
    text: str
    options: tuple  # ((보기 문구, 점수), ...)
    behavioral: bool = False  # 행동재무학 문항 여부(결과 해설에 사용)


QUESTIONS: tuple[Question, ...] = (
    Question("이 돈, 얼마나 오래 묻어둘 수 있나요?", (
        ("1년 미만 — 곧 쓸 돈이에요", 1),
        ("1~3년", 2),
        ("3~5년", 3),
        ("5~10년", 4),
        ("10년 이상 — 잊고 지낼 수 있어요", 5),
    )),
    Question("투자 경험이 가장 멀리 닿아본 곳은?", (
        ("예금·적금까지", 1),
        ("펀드·ETF 간접투자까지", 2),
        ("국내 주식 직접투자까지", 3),
        ("해외주식·채권까지", 4),
        ("파생상품·대체투자까지", 5),
    )),
    Question("이 투자금에 손실이 나면 생활에 어떤 영향이 있나요?", (
        ("생활비에 바로 타격이 옵니다", 1),
        ("결혼·주택 등 계획이 흔들립니다", 2),
        ("불편하지만 감당할 수 있습니다", 3),
        ("여유자금이라 영향 없습니다", 4),
        ("손실이 나도 추가 투자 여력이 있습니다", 5),
    )),
    Question("투자한 주식이 한 달 만에 **−20%**. 뉴스는 온통 비관론입니다. 나는?", (
        ("전부 팔고 발 뻗고 잔다", 1),
        ("절반은 팔아 위험을 줄인다", 2),
        ("판단을 유지하고 버틴다", 3),
        ("오히려 조금 더 산다", 4),
        ("계획대로 크게 추가 매수한다", 5),
    ), behavioral=True),
    Question("동전이 **앞면이면 1,000만원, 뒷면이면 0원**을 받는 게임권이 생겼습니다. "
             "이 게임권을 남에게 넘긴다면 최소 얼마는 받아야 하나요?", (
        ("300만원 — 불확실한 건 빨리 확정 짓고 싶다", 1),
        ("400만원은 받아야 한다", 2),
        ("500만원 — 기댓값만큼은 받아야 공평하다", 4),
        ("550만원 이상 아니면 그냥 게임하겠다", 5),
    ), behavioral=True),
    Question("동전이 **앞면이면 +150만원, 뒷면이면 −100만원**인 게임을 제안받았다면?", (
        ("절대 안 한다 — 100만원 잃는 게 더 크게 느껴진다", 1),
        ("내키지 않아 거절한다", 2),
        ("고민 끝에 한 번은 해본다", 3),
        ("기댓값이 +니까 기꺼이 한다", 4),
        ("이런 기회는 반복해서 잡는다", 5),
    ), behavioral=True),
    Question("투자 목표에 가장 가까운 것은?", (
        ("원금은 무조건 지킨다", 1),
        ("물가상승률보다 조금 더", 2),
        ("시장(지수)만큼이면 충분", 3),
        ("시장보다 초과수익을 노린다", 4),
        ("몇 배 수익을 노린다 — 변동은 감수", 5),
    )),
    Question("전체 금융자산 중, 넣어두고도 **잠이 오는** 위험자산 비율은?", (
        ("10% 미만", 1),
        ("10~25%", 2),
        ("25~50%", 3),
        ("50~75%", 4),
        ("75% 이상", 5),
    )),
)

SCORE_MIN = sum(min(s for _, s in q.options) for q in QUESTIONS)   # 8
SCORE_MAX = sum(max(s for _, s in q.options) for q in QUESTIONS)   # 40

# 위험회피계수 매핑 범위 (점수 최저=가장 위험회피적)
A_MAX, A_MIN = 9.0, 1.3

# ── 5단계 분류 (표준투자권유준칙 체계 + 별명) ───────────────────────
# (하한점수, 이름, 별명, 이모지, 해설, 예시 배분{주식,채권,예금·현금})
LEVELS = (
    (SCORE_MIN, "안정형", "성벽을 지키는 파수꾼", "🛡️",
     "원금 보전이 최우선입니다. 손실의 아픔이 수익의 기쁨보다 훨씬 크게 느껴지는 유형으로, "
     "예금·국공채 중심이 마음 편한 구성입니다. 다만 물가상승을 감안하면 '무위험'도 "
     "구매력 기준으론 위험이 있다는 점은 알아둘 만합니다.",
     {"주식": 10, "채권": 50, "예금·현금": 40}),
    (15, "안정추구형", "천천히 자라는 나무", "🌳",
     "원금을 크게 다치지 않는 선에서 이자보다 나은 수익을 원합니다. 채권 비중을 축으로 "
     "우량주·배당주를 소폭 곁들이는 구성이 어울립니다. 시장 급락 시 계획을 지키는 것이 "
     "가장 중요한 유형입니다.",
     {"주식": 25, "채권": 50, "예금·현금": 25}),
    (22, "위험중립형", "균형의 저울", "⚖️",
     "위험과 수익의 교환을 이해하고, 기댓값이 맞으면 변동을 감수합니다. 주식과 채권을 "
     "비슷한 무게로 두고 정기적으로 리밸런싱하는 전략이 잘 맞습니다.",
     {"주식": 40, "채권": 40, "예금·현금": 20}),
    (29, "적극투자형", "기회를 노리는 매", "🦅",
     "초과수익을 위해 변동성을 적극 감수합니다. 주식 중심 구성이 어울리지만, 하락장에서 "
     "추가 매수할 현금을 남겨두는 규율이 성과를 가릅니다.",
     {"주식": 60, "채권": 30, "예금·현금": 10}),
    (35, "공격투자형", "파도를 타는 서퍼", "🏄",
     "높은 변동성 자체를 기회로 봅니다. 이론상 차입(레버리지)까지 허용되는 유형이지만, "
     "실제로는 최대낙폭(MDD)을 버틸 수 있는지가 핵심입니다. 집중투자일수록 "
     "이 대시보드의 가치평가·백테스트로 근거를 확인하세요.",
     {"주식": 80, "채권": 15, "예금·현금": 5}),
)


@dataclass
class RiskProfile:
    score: int
    level: int              # 1~5
    label: str              # 안정형 ~ 공격투자형
    nickname: str
    emoji: str
    description: str
    allocation: dict        # 예시 배분(%)
    A: float                # 위험회피계수
    behavioral_notes: list = field(default_factory=list)


def risk_aversion_from_score(score: int) -> float:
    """점수(8~40) → 위험회피계수 A(9.0~1.3) 선형 매핑."""
    s = float(np.clip(score, SCORE_MIN, SCORE_MAX))
    return round(A_MAX - (s - SCORE_MIN) * (A_MAX - A_MIN) / (SCORE_MAX - SCORE_MIN), 2)


def grade(answer_indices: list[int]) -> RiskProfile:
    """문항별 선택 인덱스 → 성향 프로필. (문항 수와 길이가 같아야 함)"""
    if len(answer_indices) != len(QUESTIONS):
        raise ValueError("모든 문항에 답해야 채점할 수 있습니다.")
    score = sum(QUESTIONS[i].options[a][1] for i, a in enumerate(answer_indices))

    level_idx = 0
    for i, lv in enumerate(LEVELS):
        if score >= lv[0]:
            level_idx = i
    _, label, nickname, emoji, desc, alloc = LEVELS[level_idx]

    # 행동 문항 해설: 일반 문항 대비 행동 문항 점수가 크게 낮으면 '심리적 손실회피' 코멘트
    notes = []
    beh = [QUESTIONS[i].options[a][1] for i, a in enumerate(answer_indices) if QUESTIONS[i].behavioral]
    gen = [QUESTIONS[i].options[a][1] for i, a in enumerate(answer_indices) if not QUESTIONS[i].behavioral]
    if beh and gen:
        b_avg, g_avg = float(np.mean(beh)), float(np.mean(gen))
        if b_avg + 0.8 < g_avg:
            notes.append("계획(목표·기간)은 공격적인데 **심리 문항에선 손실회피가 강하게** 나타났습니다. "
                         "실제 하락장에서 계획보다 보수적으로 행동할 가능성이 높으니, 목표 비중을 "
                         "한 단계 낮춰 잡는 편이 오래 버티는 데 유리할 수 있습니다.")
        elif g_avg + 0.8 < b_avg:
            notes.append("심리는 위험을 잘 견디는데 **계획 여건(기간·여유자금)이 보수적**입니다. "
                         "여건이 허락하는 범위 안에서만 공격적으로 — 비상금·투자기간부터 확보하는 게 순서입니다.")

    return RiskProfile(score=score, level=level_idx + 1, label=label, nickname=nickname,
                       emoji=emoji, description=desc, allocation=alloc,
                       A=risk_aversion_from_score(score), behavioral_notes=notes)


# ── CML·최적 배분 수학 ──────────────────────────────────────────────
def optimal_risky_share(er_m: float, rf: float, sigma_m: float, A: float) -> float:
    """머튼 비율 y* = (E(Rm)−Rf)/(A·σm²). 1 초과면 차입(레버리지) 구간."""
    if A <= 0 or sigma_m <= 0:
        return 0.0
    return (er_m - rf) / (A * sigma_m ** 2)


def tangency_point(er_m: float, rf: float, sigma_m: float, A: float) -> dict:
    """접점 좌표와 관련 수치 — 차트·해설용.

    반환: {y_star, sigma_p, er_p, utility, sharpe, mrs}
    (mrs = A·σ* : 접점에서 무차별곡선의 기울기 = 샤프비율이어야 이론이 맞다)
    """
    y = optimal_risky_share(er_m, rf, sigma_m, A)
    sig_p = abs(y) * sigma_m
    er_p = rf + y * (er_m - rf)
    u = er_p - 0.5 * A * sig_p ** 2
    sharpe = (er_m - rf) / sigma_m if sigma_m > 0 else 0.0
    return {"y_star": y, "sigma_p": sig_p, "er_p": er_p, "utility": u,
            "sharpe": sharpe, "mrs": A * sig_p}


def indifference_curve(A: float, u_star: float, sigmas: np.ndarray) -> np.ndarray:
    """효용 u_star를 주는 무차별곡선: E(r) = U + ½·A·σ²."""
    return u_star + 0.5 * A * np.asarray(sigmas) ** 2
