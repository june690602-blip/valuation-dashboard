"""시나리오 분석(비관/기준/낙관) + 멀티플×EPS 민감도 — 순수 함수.

증권사 리포트의 Bull/Base/Bear 관행을 따른다:
  시나리오 가격 = 기준 EPS × (1 + EPS 조정률) × 시나리오 멀티플
- 기준 EPS: 컨센서스 12개월 선행 EPS 우선, 없으면 TTM EPS
- 멀티플: 자기 5년 PER 밴드 분위(비관 q25 / 기준 q50 / 낙관 q75) 우선,
  밴드가 없으면 피어 PER 중앙값 × (0.8 / 1.0 / 1.2)
적자 기업(EPS<=0)은 이익 기반 시나리오가 성립하지 않아 None을 반환한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 민감도 그리드 축 (EPS 조정률 5단계 — 가운데가 기준)
GRID_EPS_DELTAS = (-0.30, -0.15, 0.0, 0.15, 0.30)
# 멀티플 5단계: 밴드가 있으면 q10~q90, 없으면 피어 중앙값 × 아래 배율
GRID_PEER_MULTS = (0.6, 0.8, 1.0, 1.2, 1.4)


@dataclass
class ScenarioCase:
    name: str              # 비관 | 기준 | 낙관
    eps_delta: float       # 기준 EPS 대비 조정률
    eps: float
    multiple: float
    price: float
    upside: float | None   # 현재가 대비 (+면 상승여력)


@dataclass
class ScenarioResult:
    eps_base: float
    eps_basis: str         # EPS 출처 설명
    multiple_basis: str    # 멀티플 출처 설명
    cases: list = field(default_factory=list)      # [ScenarioCase] 비관→기준→낙관
    grid: pd.DataFrame | None = None               # 민감도 (index=EPS 조정, cols=멀티플)
    notes: list = field(default_factory=list)


def _pick_eps(eps_fwd, eps_ttm) -> tuple[float, str] | None:
    if eps_fwd is not None and eps_fwd > 0:
        return float(eps_fwd), "컨센서스 12개월 선행 EPS"
    if eps_ttm is not None and eps_ttm > 0:
        return float(eps_ttm), "최근 12개월(TTM) EPS — 컨센서스 없음"
    return None


def _pick_multiples(per_q, peer_per):
    """(비관, 기준, 낙관 멀티플, 그리드용 5단계, 설명) | None"""
    if per_q:
        q = {k: per_q.get(k) for k in (10, 25, 50, 75, 90)}
        if q[25] and q[50] and q[75]:
            grid5 = [v for v in (q[10], q[25], q[50], q[75], q[90]) if v]
            if len(grid5) < 5:   # q10/q90 결측이면 q25·q75 밖으로 보간
                grid5 = [q[25] * 0.8, q[25], q[50], q[75], q[75] * 1.2]
            return (q[25], q[50], q[75], grid5,
                    "자기 5년 PER 밴드 분위 (비관 25 · 기준 50 · 낙관 75분위)")
    if peer_per and peer_per > 0:
        return (peer_per * 0.8, peer_per, peer_per * 1.2,
                [peer_per * m for m in GRID_PEER_MULTS],
                "피어 PER 중앙값 × 0.8/1.0/1.2 (자기 밴드 부족)")
    return None


def build_scenarios(price: float, eps_fwd, eps_ttm, per_q, peer_per,
                    bear_delta: float = -0.15,
                    bull_delta: float = 0.15,
                    mult_adjust: float = 0.0) -> ScenarioResult | None:
    """비관/기준/낙관 3케이스 + 민감도 그리드. 적자·데이터 부족이면 None.

    mult_adjust: 세 케이스 멀티플에 일괄 적용하는 조정률(예: +0.1 = 10% 높게).
    민감도 그리드는 멀티플 축 자체가 변수라 조정과 무관하게 고정이다.
    """
    picked = _pick_eps(eps_fwd, eps_ttm)
    mults = _pick_multiples(per_q, peer_per)
    if picked is None or mults is None:
        return None
    eps_base, eps_basis = picked
    m_bear, m_base, m_bull, grid_mults, m_basis = mults

    res = ScenarioResult(eps_base=eps_base, eps_basis=eps_basis, multiple_basis=m_basis)
    for name, delta, mult in (("비관", bear_delta, m_bear),
                              ("기준", 0.0, m_base),
                              ("낙관", bull_delta, m_bull)):
        eps = eps_base * (1 + delta)
        m = mult * (1 + mult_adjust)
        p = eps * m
        res.cases.append(ScenarioCase(
            name=name, eps_delta=delta, eps=eps, multiple=m, price=p,
            upside=(p / price - 1) if price and price > 0 else None))

    # 민감도: EPS 조정 5단계 × 멀티플 5단계 (가운데 셀 = 기준 케이스)
    rows = {}
    for delta in GRID_EPS_DELTAS:
        rows[delta] = [eps_base * (1 + delta) * m for m in grid_mults]
    grid = pd.DataFrame(rows).T
    grid.index = [f"{d:+.0%}" for d in GRID_EPS_DELTAS]
    grid.index.name = "EPS 조정"
    grid.columns = [f"{m:.1f}배" for m in grid_mults]
    res.grid = grid

    if "TTM" in eps_basis:
        res.notes.append("컨센서스 선행 EPS가 없어 TTM EPS 기준입니다 — "
                         "미래 실적 변화가 반영되지 않은 시나리오입니다.")
    res.notes.append("시나리오는 가정을 바꿔보는 사고 실험이지 예측이 아닙니다. "
                     "EPS 조정률과 멀티플 가정을 직접 바꿔 보세요.")
    return res


def grid_upside(grid: pd.DataFrame, price: float) -> pd.DataFrame:
    """민감도 그리드를 현재가 대비 괴리율(%)로 변환한 사본."""
    if price and price > 0:
        return grid / price - 1
    return grid * np.nan
