"""업종 피어 대비 백분위 점수화 (0~100).

같은 소스(yfinance info + 네이버/KRX 보정)의 값끼리 비교하기 위해
타깃 값도 피어 테이블의 자기 행(is_self)에서 가져온다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 지표별 유효 범위 (이상치·무의미 값 제거)
_BOUNDS = {
    "per": (0.01, 300), "forward_per": (0.01, 300), "pbr": (0.01, 100),
    "psr": (0.01, 100), "ev_ebitda": (0.01, 200), "div_yield": (0.0, 0.30),
    "roe": (-3, 3), "roa": (-2, 2),
    "gross_margin": (-1, 1), "op_margin": (-2, 1), "net_margin": (-3, 1),
    "rev_growth": (-0.95, 5), "earnings_growth": (-0.95, 10),
    "debt_to_equity": (0, 2000), "current_ratio": (0.01, 20),
    "fcf_yield": (-1, 1), "ocf_yield": (-1, 1), "beta": (-1, 5),
}

# 카테고리 → [(피어 컬럼, 높을수록 좋은가)]
CATEGORY_SPECS: dict[str, list[tuple[str, bool]]] = {
    "밸류에이션": [("per", False), ("pbr", False), ("psr", False),
              ("ev_ebitda", False), ("div_yield", True)],
    "수익성": [("roe", True), ("roa", True), ("gross_margin", True),
            ("op_margin", True), ("net_margin", True)],
    "성장성": [("rev_growth", True), ("earnings_growth", True)],
    "재무 안정성": [("debt_to_equity", False), ("current_ratio", True)],
    "현금흐름": [("fcf_yield", True), ("ocf_yield", True)],
}

# 금융업에서 비교가 무의미한 피어 컬럼
_FINANCIAL_SKIP = {"psr", "ev_ebitda", "debt_to_equity", "current_ratio",
                   "fcf_yield", "ocf_yield"}


@dataclass
class CategoryScores:
    scores: dict = field(default_factory=dict)        # 카테고리 → 0~100 | None
    details: dict = field(default_factory=dict)       # 카테고리 → [(지표, 내값, 피어중앙값, 점수)]
    overall: float | None = None
    n_peers: int = 0


def sanitize_peer_frame(peers: pd.DataFrame) -> pd.DataFrame:
    """유효 범위를 벗어난 값(음수 PBR, ROE 1800% 등)을 NaN 처리한 복사본."""
    df = peers.copy()
    for col, (lo, hi) in _BOUNDS.items():
        if col in df.columns:
            v = pd.to_numeric(df[col], errors="coerce")
            df[col] = v.where((v >= lo) & (v <= hi))
    return df


def peer_median(peers: pd.DataFrame, col: str, exclude_self: bool = True):
    """정제된 피어 중앙값 (표본 3개 미만이면 None)."""
    if col not in peers.columns:
        return None
    df = peers[~peers["is_self"]] if exclude_self and "is_self" in peers.columns else peers
    v = df[col].dropna()
    return float(v.median()) if len(v) >= 3 else None


def _percentile_score(target: float, peer_values: pd.Series, higher_better: bool) -> float:
    """타깃이 피어보다 '나은' 비율 (동률 0.5) × 100."""
    v = peer_values.dropna()
    better = (target > v) if higher_better else (target < v)
    ties = np.isclose(v, target)
    return float((better.sum() + 0.5 * ties.sum()) / len(v) * 100)


def compute_scores(peers: pd.DataFrame, self_ticker: str,
                   is_financial: bool = False) -> CategoryScores:
    out = CategoryScores()
    if peers.empty or "is_self" not in peers.columns or not peers["is_self"].any():
        return out
    df = sanitize_peer_frame(peers)
    me = df[df["is_self"]].iloc[0]
    others = df[~df["is_self"]]
    out.n_peers = len(others)

    for cat, specs in CATEGORY_SPECS.items():
        rows, scores = [], []
        for col, higher in specs:
            if is_financial and col in _FINANCIAL_SKIP:
                continue
            target = me.get(col)
            pv = others[col].dropna() if col in others.columns else pd.Series(dtype=float)
            med = float(pv.median()) if len(pv) >= 3 else None
            if target is None or (isinstance(target, float) and np.isnan(target)) or len(pv) < 3:
                rows.append((col, target, med, None))
                continue
            sc = _percentile_score(float(target), pv, higher)
            scores.append(sc)
            rows.append((col, float(target), med, sc))
        out.details[cat] = rows
        out.scores[cat] = float(np.mean(scores)) if scores else None

    valid = [v for v in out.scores.values() if v is not None]
    out.overall = float(np.mean(valid)) if valid else None
    return out


# 저평가·우량 랭킹용 지표 (피어 컬럼, 낮을수록 좋은가)
_VALUE_METRICS = [("per", False), ("pbr", False), ("psr", False), ("ev_ebitda", False)]
_QUALITY_METRICS = [("roe", True), ("op_margin", True)]
_RANK_FIN_SKIP = {"psr", "ev_ebitda"}


def _pctrank(series: pd.Series, higher_better: bool) -> pd.Series:
    """피어 내 백분위(0~100). NaN은 유지."""
    r = series.rank(pct=True) * 100
    return r if higher_better else 100 - r


def rank_peers_cheapness(peers: pd.DataFrame, is_financial: bool = False,
                         value_weight: float = 0.6) -> pd.DataFrame:
    """업종 내 '싸고 우량한' 순 랭킹. 이미 로드된 피어 지표만 사용(추가 조회 없음).

    combined = value_weight·가치점수 + (1-value_weight)·우량점수, 둘 다 피어 백분위 평균.
    """
    if peers.empty:
        return pd.DataFrame()
    df = sanitize_peer_frame(peers)
    vcols = [c for c, _ in _VALUE_METRICS
             if c in df.columns and not (is_financial and c in _RANK_FIN_SKIP)]
    qcols = [c for c, _ in _QUALITY_METRICS if c in df.columns]

    vparts = [_pctrank(df[c], False) for c in vcols]
    qparts = [_pctrank(df[c], True) for c in qcols]
    value_score = pd.concat(vparts, axis=1).mean(axis=1) if vparts else pd.Series(np.nan, df.index)
    quality_score = pd.concat(qparts, axis=1).mean(axis=1) if qparts else pd.Series(np.nan, df.index)

    out = pd.DataFrame({
        "name": df.get("name"), "market_cap": df.get("market_cap"),
        "per": df.get("per"), "pbr": df.get("pbr"), "roe": df.get("roe"),
        "value_score": value_score, "quality_score": quality_score,
        "is_self": df.get("is_self", False),
    })
    out["combined"] = (value_weight * out["value_score"].fillna(50)
                       + (1 - value_weight) * out["quality_score"].fillna(50))
    # 가치·우량 점수가 둘 다 없는 행은 랭킹에서 제외
    out = out[value_score.notna() | quality_score.notna()]
    return out.sort_values("combined", ascending=False)
