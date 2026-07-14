"""투자 위험 프로파일 자가진단 — 문항·채점·CML 시나리오 계산.

이 모듈은 금융회사의 공식 투자자정보확인서를 대신하지 않는다. 결과는 현재 투자금의
손실 감당 여력(capacity), 가격 변동을 받아들이는 태도(tolerance), 투자 경험과 이해,
목표의 적극성을 나누어 보여주는 교육용 참고 자료다.

평균-분산 모형의 위험회피계수 ``A``와 CML 접점은 결과를 설명하는 시나리오 도구다.
자가진단으로 추정한 ``assessed_A``와 사용자가 화면에서 조절하는 ``scenario_A``를
구분해야 하며, 후자를 검사 결과처럼 저장하거나 개인화에 사용하면 안 된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

import numpy as np


PROFILE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Question:
    id: str
    chapter: str
    dimension: str
    text: str
    options: tuple[tuple[str, int], ...]
    guide: str = ""
    behavioral: bool = False
    weight: float = 1.0


DIMENSIONS = {
    "capacity": {
        "label": "손실 감당 여력",
        "short": "투자 기간과 생활 계획을 해치지 않고 손실을 버틸 수 있는 여건",
        "weight": 0.35,
    },
    "knowledge": {
        "label": "경험·이해도",
        "short": "상품 구조와 손실 가능성을 이해하고 운용해 본 정도",
        "weight": 0.15,
    },
    "tolerance": {
        "label": "변동성 수용도",
        "short": "가격 하락을 마주했을 때 계획을 유지할 수 있는 심리적 범위",
        "weight": 0.30,
    },
    "objective": {
        "label": "목표의 적극성",
        "short": "현재 투자금으로 추구하는 성장 수준과 위험자산 범위",
        "weight": 0.20,
    },
}


QUESTIONS: tuple[Question, ...] = (
    Question(
        "horizon", "현재 자금", "capacity",
        "이 투자금을 다시 사용할 가능성이 가장 높은 시점은 언제인가요?",
        (
            ("1년 이내 — 가까운 시일에 사용할 수 있습니다", 1),
            ("1~3년", 2),
            ("3~5년", 3),
            ("5~10년", 4),
            ("10년 이후 또는 구체적인 사용 계획이 없습니다", 5),
        ),
        "막연한 장기 계획이 아니라, 지금 평가하려는 자금의 실제 사용 시점을 기준으로 답해주세요.",
    ),
    Question(
        "loss_impact", "현재 자금", "capacity",
        "이 투자금에 20% 손실이 생기면 생활과 재무계획에 어떤 영향이 있나요?",
        (
            ("생활비나 부채 상환에 직접적인 영향이 있습니다", 1),
            ("주거·교육 등 중요한 계획을 미뤄야 합니다", 2),
            ("일부 계획을 조정해야 하지만 생활은 유지할 수 있습니다", 3),
            ("불편하지만 주요 계획은 그대로 유지할 수 있습니다", 4),
            ("주요 계획에 영향이 없고 회복을 기다릴 여력이 있습니다", 5),
        ),
        "감정적인 불편함보다 현금흐름과 예정된 지출에 미치는 영향을 생각해 보세요.",
    ),
    Question(
        "experience", "경험과 이해", "knowledge",
        "투자 경험과 상품 이해도에 가장 가까운 것은 무엇인가요?",
        (
            ("예·적금 외 상품은 거의 경험하지 않았습니다", 1),
            ("분산형 펀드·ETF를 이용해 봤고 원금 손실 가능성을 압니다", 2),
            ("주식·채권을 직접 거래했고 가격 변동과 비용을 비교할 수 있습니다", 3),
            ("여러 자산을 운용하며 하락장과 리밸런싱을 경험했습니다", 4),
            ("복잡한 상품의 구조와 최대 손실을 스스로 설명할 수 있습니다", 5),
        ),
        "상품의 이름보다 손실 구조를 이해하고 실제로 운용해 본 경험을 기준으로 답해주세요.",
    ),
    Question(
        "drawdown", "손실 상황", "tolerance",
        "앞으로 1년 동안 이 투자금이 어느 정도 하락해도 계획을 유지할 수 있나요?",
        (
            ("5% 미만", 1),
            ("약 10%", 2),
            ("약 20%", 3),
            ("약 30%", 4),
            ("30%를 넘어도 장기 계획을 유지할 수 있습니다", 5),
        ),
        "희망하는 수익률이 아니라, 실제 계좌에서 견딜 수 있는 최대 낙폭을 골라주세요.",
        behavioral=True,
    ),
    Question(
        "drawdown_action", "손실 상황", "tolerance",
        "투자 근거에는 큰 변화가 없지만 위험자산이 한 달 사이 20% 하락했습니다. 실제 행동과 가장 가까운 것은?",
        (
            ("불안을 줄이기 위해 대부분 매도합니다", 1),
            ("손실 확대를 막기 위해 일부 비중을 줄입니다", 2),
            ("정보를 다시 확인한 뒤 기존 비중을 유지합니다", 3),
            ("미리 정한 목표 비중까지 리밸런싱합니다", 4),
            ("사전에 정한 상한 안에서 위험자산 비중을 조금 늘립니다", 5),
        ),
        "좋아 보이는 답보다 과거 하락장에서 실제로 했거나 지킬 수 있는 행동을 골라주세요.",
        behavioral=True,
    ),
    Question(
        "certainty_equivalent", "손실 상황", "tolerance",
        "50% 확률로 1,000만원, 50% 확률로 0원을 받는 선택권을 확정 금액과 바꾼다면 최소 얼마가 적당한가요?",
        (
            ("300만원", 1),
            ("400만원", 2),
            ("500만원", 3),
            ("600만원", 4),
            ("700만원 이상", 5),
        ),
        "기댓값은 500만원입니다. 정답은 없으며 불확실성을 받아들이는 태도를 보는 보조 문항입니다.",
        behavioral=True,
        weight=0.5,
    ),
    Question(
        "objective", "운용 기준", "objective",
        "이 투자금으로 가장 먼저 이루고 싶은 목표는 무엇인가요?",
        (
            ("원금 보전과 필요할 때 바로 쓸 수 있는 유동성", 1),
            ("물가를 방어하는 안정적인 운용", 2),
            ("제한된 손실 범위 안에서 예·적금보다 나은 성장", 3),
            ("중장기 시장 성장에 참여하는 것", 4),
            ("큰 변동을 감수한 적극적인 장기 성장", 5),
        ),
        "전체 재산의 목표가 아니라, 지금 평가 중인 투자금의 우선순위를 선택해주세요.",
    ),
    Question(
        "risky_share", "운용 기준", "objective",
        "전체 금융자산 중 가격 변동이 큰 자산에 배분해도 감당할 수 있는 최대 비중은?",
        (
            ("10% 미만", 1),
            ("10~25%", 2),
            ("25~50%", 3),
            ("50~75%", 4),
            ("75% 이상", 5),
        ),
        "주식·고수익채권·대체자산처럼 손실 폭이 커질 수 있는 자산을 뜻합니다. ETF도 구성에 따라 위험자산일 수 있습니다.",
    ),
)


@dataclass(frozen=True)
class RiskLevel:
    min_score: int
    label: str
    official_label: str
    archetype: str
    symbol: str
    summary: str
    description: str
    principles: tuple[str, ...]
    watchouts: tuple[str, ...]
    allocation: tuple[tuple[str, int], ...]
    allocation_range: tuple[tuple[str, tuple[int, int]], ...]


LEVELS: tuple[RiskLevel, ...] = (
    RiskLevel(
        0, "자본 보전형", "안정형", "자본 보전가", "01",
        "잃지 않는 구조에서 시작합니다.",
        "수익의 크기보다 자금의 안전성과 사용 시점을 먼저 확인합니다. 단기 지출과 비상자금을 "
        "분리하고 손실 가능성이 낮은 자산을 중심으로 운용할 때 계획을 지키기 쉽습니다. 다만 "
        "현금 비중이 지나치면 물가 상승으로 실질 구매력이 낮아질 수 있습니다.",
        ("비상자금과 투자금을 분리하기", "자금 사용 시점에 맞춰 만기를 나누기", "물가 위험까지 포함해 분산하기"),
        ("가까운 시일에 쓸 돈을 위험자산으로 옮기는 행동", "손실을 피하려다 현금만 과도하게 보유하는 행동"),
        (("주식", 10), ("채권", 50), ("예금·현금", 40)),
        (("주식", (0, 20)), ("채권", (40, 60)), ("예금·현금", (30, 50))),
    ),
    RiskLevel(
        20, "안정 성장형", "안정추구형", "안정 성장가", "02",
        "속도보다 지속 가능한 성장을 택합니다.",
        "큰 손실은 피하면서도 예금 이상의 완만한 성장을 원합니다. 방어자산을 중심에 두고 "
        "분산된 위험자산을 제한적으로 더하는 방식이 잘 맞습니다. 수익률을 높이기보다 감당 가능한 "
        "하락폭을 정하고 꾸준히 유지하는 것이 핵심입니다.",
        ("안전자산을 포트폴리오의 중심에 두기", "위험자산은 넓게 분산하기", "정기적으로 원래 비중으로 되돌리기"),
        ("단기 성과가 좋을 때 위험자산 비중을 급격히 늘리는 행동", "손실 직후 계획 없이 전부 현금화하는 행동"),
        (("주식", 25), ("채권", 50), ("예금·현금", 25)),
        (("주식", (15, 35)), ("채권", (40, 55)), ("예금·현금", (15, 35))),
    ),
    RiskLevel(
        40, "균형 배분형", "위험중립형", "균형 배분가", "03",
        "기회와 방어의 비중을 조절합니다.",
        "기대수익과 손실 가능성을 함께 보고 어느 한쪽으로 치우치지 않는 구성을 선호합니다. 여러 "
        "자산에 나누어 투자하고 정해진 주기에 원래 비중으로 되돌리는 방식과 잘 맞습니다. 균형형도 "
        "시장 하락을 피하는 전략은 아니므로 단기 사용 자금은 별도로 두어야 합니다.",
        ("자산별 역할과 목표 비중을 문서로 정하기", "정기 또는 허용범위 방식으로 리밸런싱하기", "단기자금은 포트폴리오 밖에 두기"),
        ("시장 전망에 따라 균형 비중을 자주 뒤집는 행동", "분산을 이유로 이해하지 못한 자산을 추가하는 행동"),
        (("주식", 45), ("채권", 40), ("예금·현금", 15)),
        (("주식", (35, 55)), ("채권", (30, 45)), ("예금·현금", (10, 25))),
    ),
    RiskLevel(
        60, "성장 탐색형", "적극투자형", "성장 탐색가", "04",
        "변동성을 감수하며 장기 성장 기회를 찾습니다.",
        "중장기 성장 가능성을 위해 비교적 큰 가격 변동을 감수할 수 있습니다. 위험자산 비중을 "
        "높이더라도 분산, 비중 상한, 리밸런싱 규칙을 미리 정해 두는 것이 중요합니다. 높은 감수 "
        "의향이 실제 손실 감당 여력을 대신하지는 않습니다.",
        ("종목·자산별 비중 상한을 먼저 정하기", "하락 전에 리밸런싱 규칙을 정하기", "회복을 기다릴 장기 현금흐름을 확보하기"),
        ("성향 점수를 근거로 집중투자를 정당화하는 행동", "추가매수 한도 없이 하락할 때마다 비중을 늘리는 행동"),
        (("주식", 65), ("채권", 25), ("예금·현금", 10)),
        (("주식", (55, 75)), ("채권", (15, 30)), ("예금·현금", (5, 15))),
    ),
    RiskLevel(
        80, "기회 확장형", "공격투자형", "고위험 전략가", "05",
        "큰 변동을 전제로 위험 예산을 설계합니다.",
        "높은 기대수익을 위해 큰 폭의 손실과 긴 회복 기간까지 받아들이는 편입니다. 공격적인 "
        "성향은 집중투자나 레버리지를 정당화하지 않습니다. 최악의 손실 규모와 현금흐름을 먼저 "
        "점검하고 감당 가능한 범위 안에서 위험 예산을 명확히 두어야 합니다.",
        ("최대 손실 금액과 중단 기준을 숫자로 정하기", "현금·채권 완충자산을 유지하기", "레버리지와 집중도를 별도 한도로 관리하기"),
        ("높은 위험 선호를 높은 분석 능력으로 착각하는 행동", "회복 기간을 고려하지 않고 차입으로 손실을 확대하는 행동"),
        (("주식", 80), ("채권", 15), ("예금·현금", 5)),
        (("주식", (70, 90)), ("채권", (5, 20)), ("예금·현금", (0, 10))),
    ),
)


@dataclass
class RiskProfile:
    score: int
    raw_score: int
    level: int
    label: str
    official_label: str
    archetype: str
    symbol: str
    summary: str
    description: str
    allocation: dict[str, int]
    allocation_range: dict[str, tuple[int, int]]
    A: float
    dimension_scores: dict[str, int]
    consistency: str
    principles: list[str] = field(default_factory=list)
    watchouts: list[str] = field(default_factory=list)
    behavioral_notes: list[str] = field(default_factory=list)
    guardrail_note: str | None = None

    @property
    def nickname(self) -> str:
        """구버전 소비 코드 호환용 별칭."""
        return self.archetype

    @property
    def emoji(self) -> str:
        """구버전 소비 코드 호환용 별칭. 새 UI에서는 symbol을 사용한다."""
        return self.symbol


def _validate_answers(answer_indices: list[int]) -> None:
    if len(answer_indices) != len(QUESTIONS):
        raise ValueError("모든 문항에 답해야 채점할 수 있습니다.")
    for i, (question, answer) in enumerate(zip(QUESTIONS, answer_indices), start=1):
        if isinstance(answer, bool) or not isinstance(answer, int):
            raise ValueError(f"{i}번 문항의 응답 인덱스는 정수여야 합니다.")
        if answer < 0 or answer >= len(question.options):
            raise ValueError(f"{i}번 문항의 응답 인덱스가 범위를 벗어났습니다.")


def _dimension_scores(answer_indices: list[int]) -> dict[str, int]:
    buckets: dict[str, list[tuple[int, float]]] = {key: [] for key in DIMENSIONS}
    for question, answer in zip(QUESTIONS, answer_indices):
        buckets[question.dimension].append((question.options[answer][1], question.weight))

    scores = {}
    for key, values in buckets.items():
        weighted = sum(score * weight for score, weight in values)
        total_weight = sum(weight for _, weight in values)
        average = weighted / total_weight
        scores[key] = round((average - 1.0) / 4.0 * 100)
    return scores


def risk_aversion_from_score(score: float) -> float:
    """위험 감수 의향 지수(0~100) → 교육용 위험회피계수 A(9.0~1.3)."""
    if not isfinite(float(score)):
        raise ValueError("성향 지수는 유한한 숫자여야 합니다.")
    s = float(np.clip(score, 0.0, 100.0))
    return round(9.0 - s * (9.0 - 1.3) / 100.0, 2)


def grade(answer_indices: list[int]) -> RiskProfile:
    """문항별 선택 인덱스를 네 축의 위험 프로파일로 요약한다.

    최종 유형은 가중 평균만으로 결정하지 않는다. 현재 자금의 손실 감당 여력이 매우 낮거나
    상품 이해도가 낮으면 공격적인 태도가 나타나도 유형 상한을 적용한다. 이는 수익 선호보다
    실제 감내 능력을 우선하기 위한 교육용 안전장치다.
    """
    _validate_answers(answer_indices)
    dimensions = _dimension_scores(answer_indices)
    raw_score = round(sum(dimensions[key] * meta["weight"] for key, meta in DIMENSIONS.items()))

    raw_level_idx = 0
    for i, level in enumerate(LEVELS):
        if raw_score >= level.min_score:
            raw_level_idx = i

    max_level_idx = len(LEVELS) - 1
    guardrail_reasons = []
    capacity_answers = [QUESTIONS[i].options[answer_indices[i]][1] for i in (0, 1)]
    if min(capacity_answers) == 1:
        max_level_idx = min(max_level_idx, 1)
        guardrail_reasons.append("현재 자금의 사용 시점 또는 손실 영향이 커서 감내 여력을 우선했습니다")
    elif dimensions["capacity"] < 40:
        max_level_idx = min(max_level_idx, 2)
        guardrail_reasons.append("손실 감당 여력이 위험 감수 의향보다 낮아 보수적으로 반영했습니다")
    if dimensions["knowledge"] < 25:
        max_level_idx = min(max_level_idx, 2)
        guardrail_reasons.append("상품 경험·이해도가 낮아 복잡한 위험을 감수하는 단계는 제한했습니다")

    level_idx = min(raw_level_idx, max_level_idx)
    effective_score = raw_score
    if level_idx < raw_level_idx:
        next_threshold = LEVELS[level_idx + 1].min_score
        effective_score = min(effective_score, next_threshold - 1)

    preference_score = dimensions["tolerance"] * 0.65 + dimensions["objective"] * 0.35
    # CML 참고점도 최종 유형과 같은 안전장치를 따른다. 감수 의향만 높고 실제 여력이나
    # 이해도가 낮을 때 낮은 A(높은 위험자산 비중)가 제시되는 모순을 막는다.
    model_score = min(preference_score, float(effective_score))
    assessed_A = risk_aversion_from_score(model_score)

    spread = max(dimensions.values()) - min(dimensions.values())
    consistency = "고르게 나타남" if spread <= 20 else "일부 축이 다르게 나타남" if spread <= 40 else "축별 차이가 큼"

    notes = []
    if dimensions["tolerance"] >= dimensions["capacity"] + 25:
        notes.append(
            "가격 변동은 비교적 잘 받아들이지만 투자 기간과 자금 여력은 더 보수적으로 나타났습니다. "
            "성향보다 여력이 우선입니다. 비상자금과 사용 예정 자금을 분리한 뒤 남은 장기자금 안에서만 "
            "위험 수준을 정해보세요."
        )
    if dimensions["objective"] >= dimensions["tolerance"] + 25:
        notes.append(
            "장기 목표는 적극적인 편이지만 급락 상황의 행동은 더 보수적으로 나타났습니다. 실제 비중은 "
            "평소 생각보다 하락장에서 지킬 수 있는 수준을 기준으로 검토하는 편이 좋습니다."
        )
    if dimensions["capacity"] >= dimensions["tolerance"] + 25:
        notes.append(
            "재무적으로는 손실을 기다릴 여력이 있지만 심리적으로 편안한 변동 범위는 더 낮습니다. "
            "감당할 수 있다는 이유만으로 위험을 늘리기보다, 계획을 유지할 수 있는 수준을 선택하세요."
        )
    if dimensions["knowledge"] < 30 and max(dimensions["tolerance"], dimensions["objective"]) >= 60:
        notes.append(
            "위험을 감수하려는 의향에 비해 상품 경험·이해도가 낮게 나타났습니다. 비중을 높이기 전에 "
            "손실 구조와 비용을 설명할 수 있는 단순한 상품부터 확인하는 것이 우선입니다."
        )

    level = LEVELS[level_idx]
    return RiskProfile(
        score=effective_score,
        raw_score=raw_score,
        level=level_idx + 1,
        label=level.label,
        official_label=level.official_label,
        archetype=level.archetype,
        symbol=level.symbol,
        summary=level.summary,
        description=level.description,
        allocation=dict(level.allocation),
        allocation_range=dict(level.allocation_range),
        A=assessed_A,
        dimension_scores=dimensions,
        consistency=consistency,
        principles=list(level.principles),
        watchouts=list(level.watchouts),
        behavioral_notes=notes,
        guardrail_note=" · ".join(guardrail_reasons) if guardrail_reasons else None,
    )


def profile_to_dict(profile: RiskProfile) -> dict:
    """웹 API와 저장소가 공통으로 쓰는 JSON 직렬화 형태."""
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "score": profile.score,
        "raw_score": profile.raw_score,
        "level": profile.level,
        "label": profile.label,
        "official_label": profile.official_label,
        "archetype": profile.archetype,
        "symbol": profile.symbol,
        "summary": profile.summary,
        "description": profile.description,
        "allocation": dict(profile.allocation),
        "allocation_range": {key: list(value) for key, value in profile.allocation_range.items()},
        "assessed_A": profile.A,
        "A": profile.A,
        "dimension_scores": dict(profile.dimension_scores),
        "consistency": profile.consistency,
        "principles": list(profile.principles),
        "watchouts": list(profile.watchouts),
        "behavioral_notes": list(profile.behavioral_notes),
        "guardrail_note": profile.guardrail_note,
        # v1 소비 코드 호환. 새 코드에서는 archetype/symbol을 사용한다.
        "nickname": profile.archetype,
        "emoji": profile.symbol,
    }


def risk_profile_config() -> dict:
    """정적 웹이 Python과 동일한 문항·분류를 쓰도록 제공하는 공개 설정."""
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "estimated_minutes": 2,
        "question_count": len(QUESTIONS),
        "dimensions": [
            {"key": key, "label": value["label"], "short": value["short"]}
            for key, value in DIMENSIONS.items()
        ],
        "questions": [
            {
                "id": question.id,
                "chapter": question.chapter,
                "dimension": question.dimension,
                "text": question.text,
                "guide": question.guide,
                "behavioral": question.behavioral,
                "options": [label for label, _score in question.options],
            }
            for question in QUESTIONS
        ],
        "levels": [
            {
                "label": level.label,
                "official_label": level.official_label,
                "archetype": level.archetype,
                "symbol": level.symbol,
                "summary": level.summary,
            }
            for level in LEVELS
        ],
    }


# ── CML·시나리오 수학 ──────────────────────────────────────────────
def optimal_risky_share(er_m: float, rf: float, sigma_m: float, A: float) -> float:
    """머튼 비율 y* = (E(Rm)−Rf)/(A·σm²).

    개인의 제약·세금·현금흐름을 반영하지 않은 이론값이므로 권장 비중이나 상한으로 부르면 안 된다.
    """
    values = (er_m, rf, sigma_m, A)
    if not all(isfinite(float(value)) for value in values):
        raise ValueError("CML 입력값은 모두 유한한 숫자여야 합니다.")
    if A <= 0 or sigma_m <= 0:
        return 0.0
    return (er_m - rf) / (A * sigma_m ** 2)


def tangency_point(er_m: float, rf: float, sigma_m: float, A: float) -> dict:
    """CML과 무차별곡선의 모형상 접점 좌표를 계산한다."""
    y = optimal_risky_share(er_m, rf, sigma_m, A)
    sig_p = abs(y) * sigma_m
    er_p = rf + y * (er_m - rf)
    utility = er_p - 0.5 * A * sig_p ** 2
    sharpe = (er_m - rf) / sigma_m if sigma_m > 0 else 0.0
    return {
        "y_star": y,
        "sigma_p": sig_p,
        "er_p": er_p,
        "utility": utility,
        "sharpe": sharpe,
        "mrs": A * sig_p,
    }


def indifference_curve(A: float, u_star: float, sigmas: np.ndarray) -> np.ndarray:
    """효용 u_star를 주는 무차별곡선: E(r) = U + ½·A·σ²."""
    return u_star + 0.5 * A * np.asarray(sigmas) ** 2
