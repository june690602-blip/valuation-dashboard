# -*- coding: utf-8 -*-
"""R2(가정의 적합성) 검증: 가정을 흔들면 판정이 뒤집히는가.

R1은 '이 값이 정말 그 값인가'를 봤다. 여기서는 값이 맞다고 치고 **그 값을 다루는 선택**을
본다 — 가중치·지속계수·자본비용·추정창·임계처럼 우리가 정한 숫자들이다.

방법은 하나다. **가정을 하나씩 흔들어 판정(5단계)이 바뀌는 종목 수를 센다.**
흔들었을 때 판정이 뒤집히는 가정은 '선택'이 아니라 사실상 **결론**이므로, 근거를 더
두껍게 대거나 화면에 그 취약성을 밝혀야 한다.

    python scripts/check_sensitivity.py            # 대표 종목 패널 (축별 요약)
    python scripts/check_sensitivity.py KR 005930  # 종목 하나 (축별 상세)
    python scripts/check_sensitivity.py --etf      # ETF 임계 민감도만

원칙: `src/` 코드는 건드리지 않는다. 흔들기는 이 스크립트가 순수 함수의 입력과
모듈 전역을 임시로 갈아 끼워서(patch) 수행한다 — 발견과 수정을 분리하기 위해서다.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis import valuation as V           # noqa: E402
from src.analysis.capital_cost import BETA_CLIP, estimate_beta  # noqa: E402
from src.web.serialize import _defaults, _pipeline  # noqa: E402

# 대표 종목 패널 — 가정이 종목 성격에 따라 다르게 물린다는 걸 보려고 축을 갈라 골랐다.
# 사이클(반도체·화학) / 안정(금융·필수소비재) / 고PBR 성장(RIM 스킵) / ADR(통화 혼재).
PANEL = [
    ("KR", "005930", "삼성전자 · 사이클"),
    ("KR", "000660", "SK하이닉스 · 사이클"),
    ("KR", "105560", "KB금융 · 금융"),
    ("KR", "035420", "NAVER · 성장"),
    ("KR", "005380", "현대차 · 가치"),
    ("US", "AAPL", "애플 · 고PBR"),
    ("US", "KO", "코카콜라 · 안정"),
    ("US", "JNJ", "존슨앤드존슨 · 안정"),
    ("US", "MSFT", "마이크로소프트 · 성장"),
    ("US", "TSM", "TSMC · ADR"),
]

ETF_PANEL = [("US", "SPY"), ("US", "QQQ"), ("US", "SCHD"), ("US", "TLT"),
             ("KR", "069500"), ("KR", "360750")]

# 원본 함수 참조 — 아래 흔들기 함수들이 이걸 감싸 쓴다(패치 후 재귀 방지).
_ACTUAL_PRICES = V.actual_prices
_COMPARABLE = V.comparable_peers
_RECENT_ROE = V._recent_roe
_BAND = V._band


# ── 흔들기 도구 ──────────────────────────────────────────────────────
@contextmanager
def patched(**kw):
    """valuation 모듈 전역을 임시로 교체한다(순수 함수라 부작용이 안 남는다)."""
    old = {k: getattr(V, k) for k in kw}
    try:
        for k, v in kw.items():
            setattr(V, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(V, k, v)


def rim_with_center(w_center: float):
    """지속계수 중심을 w_center로 옮긴 _rim (식은 원본과 동일, 중심값만 이동)."""
    def _rim(bps, roe, r):
        if not bps or bps <= 0 or roe is None or roe <= 0 or r <= 0:
            return None, None
        vals = {}
        for w in (0.6, 0.8, 1.0):
            v = bps * roe / r if w >= 1.0 else bps + bps * (roe - r) * w / (1 + r - w)
            vals[w] = max(v, 0.0)
        # **중심은 표에서 꺼내지 말고 식으로 낸다.** 꺼내 쓰면 w_center가 0.6/0.8/1.0일
        # 때만 돌아가서, 문헌값 0.62 같은 값을 넣는 순간 KeyError로 죽는다.
        mid = (bps * roe / r if w_center >= 1.0
               else max(bps + bps * (roe - r) * w_center / (1 + r - w_center), 0.0))
        return V.FairValue("수익가치(RIM)", min(vals.values()), mid, max(vals.values()),
                           note=f"지속계수 중심 {w_center}"), (mid / bps if bps else None)
    return _rim


def prices_window(years: float):
    """역사적 밴드(②)의 관측창을 years년으로 자른 가격 계열을 돌려준다."""
    def f(d):
        px = _ACTUAL_PRICES(d)
        if px is None or len(px) == 0:
            return px
        cutoff = px.index.max() - pd.Timedelta(days=int(365.25 * years))
        return px[px.index >= cutoff]
    return f


def peers_with_factor(factor: float):
    """피어 규모 필터(시총 1/factor~factor배)의 배율만 바꾼다."""
    return lambda peers, mcap, **_: _COMPARABLE(peers, mcap, factor=factor)


def roe_rule(kind: str, peer_roe: float | None = None):
    """RIM에 넣는 ROE 규칙을 바꾼다 (#40 — 과거 ROE를 미래 ROE로 그대로 쓰는 문제)."""
    def f(d, ttm_roe):
        fin = d.financials
        eq = fin["total_equity"]
        avg_eq = ((eq + eq.shift(1)) / 2).fillna(eq)
        s = (fin["net_income"] / avg_eq).dropna().tail(3)
        hist = float(s.mean()) if len(s) else None
        if kind == "hist":            # 최근 3개년 평균만 (TTM 제외)
            return hist if hist is not None else ttm_roe
        if kind == "ttm":             # TTM만
            return ttm_roe if ttm_roe is not None else hist
        if kind == "revert":          # 업종(피어) 중앙값 ROE로 절반 수렴
            cur = _RECENT_ROE(d, ttm_roe)
            if cur is None or peer_roe is None:
                return cur
            return 0.5 * cur + 0.5 * peer_roe
        return _RECENT_ROE(d, ttm_roe)
    return f


def verdict_at(gap: float | None, m: float) -> str | None:
    """로그 문턱을 `m`으로 바꿨을 때의 3등급 판정 (ADR-0042).

    **기준값은 이 함수로 만들지 않는다** — `V._verdict`를 그대로 부른다. 예전에는 여기서
    현행 임계(0.10/0.30)를 손으로 다시 적었는데, 그러면 판정 규칙이 바뀔 때 이 스크립트의
    '기준'만 조용히 옛 규칙으로 남는다. 흔든 값만 여기서 만든다.
    """
    if gap is None:
        return None
    if gap <= -1:
        return V.VERDICTS[2]
    lg = float(np.log1p(gap))
    return (V.VERDICTS[0] if lg >= m else
            V.VERDICTS[2] if lg <= -m else V.VERDICTS[1])


def aggregate(estimates, weights: dict, price: float):
    """방법별 중심값을 주어진 가중치로 종합 → (적정가, 괴리율, 판정)."""
    if not estimates or not price:
        return None, None, None
    w = np.array([weights.get(e.method, 0.25) for e in estimates], dtype=float)
    if w.sum() <= 0:
        return None, None, None
    w = w / w.sum()
    mid = float(np.dot(w, [e.mid for e in estimates]))
    gap = mid / price - 1
    return mid, gap, V._verdict(gap)


def confidence_of(mids) -> str | None:
    """방법 간 변동계수 → 신뢰도 등급 (valuation.compute_valuation과 같은 식)."""
    if len(mids) < 2:
        return "낮음"
    disp = float(np.std(mids) / abs(np.mean(mids)))
    return "높음" if disp < 0.15 else "중간" if disp < 0.35 else "낮음"


def k_e_from(prices, index_prices, rf, mrp, weeks=None, clip=True):
    """베타 추정창·클리핑을 바꿔 다시 구한 자기자본비용."""
    px = prices if weeks is None else prices[prices.index >= prices.index.max()
                                             - pd.Timedelta(weeks=weeks)]
    beta, _r2, n, _label, _pts = estimate_beta(px, index_prices)
    if beta is None:
        return None, n
    b = float(np.clip(beta, *BETA_CLIP)) if clip else float(beta)
    return rf + b * mrp, n


# ── 종목 하나에 대한 축별 흔들기 ─────────────────────────────────────
def shake(d, ind, cc, val, rf, mrp) -> list[dict]:
    """[{축, 변형, 적정가, 괴리율, 판정, 뒤집힘}] — 기준은 val(현행 가정)."""
    price, base_v = d.price, val.verdict
    rows = []

    def add(axis, label, mid, gap, verd):
        rows.append({"axis": axis, "label": label, "mid": mid, "gap": gap,
                     "verdict": verd,
                     "flip": bool(verd and base_v and verd != base_v)})

    def rerun(axis, label, r_equity=None, **patches):
        with patched(**patches):
            v2 = V.compute_valuation(d, ind, r_equity=r_equity if r_equity is not None
                                     else cc.k_e)
        add(axis, label, v2.fair_mid, v2.gap, v2.verdict)
        return v2

    # ① 방법 가중치 — 기준(val.verdict)이 ①②③ 종합이므로 흔드는 대상도 그 집합이다.
    # ADR-0006 이전에는 여기서 ④를 포함한 네 방법을 흔들었는데, 그러면 기준과 비교 대상이
    # 서로 다른 방법 집합이라 '가중치 때문에 뒤집혔다'가 사실은 '④를 넣어서 뒤집혔다'가 된다.
    core = [e for e in val.estimates if e.method in V.FUNDAMENTAL_METHODS]
    add("가중치", "동일가중 ①②③", *aggregate(core, {}, price))
    for m, base_w in (("업종 상대가치", 0.25), ("역사적 밴드", 0.25), ("수익가치(RIM)", 0.15)):
        for delta in (-0.10, +0.10):
            w = dict(V.METHOD_WEIGHTS)
            w[m] = round(base_w + delta, 2)
            add("가중치", f"{m} {base_w:.2f}→{w[m]:.2f}", *aggregate(core, w, price))
    # ④를 얹으면 어떻게 되는가 — 이제 '가중치'가 아니라 '방법 추가' 축이다(ADR-0006).
    if len(core) < len(val.estimates):
        add("방법 추가", "④ 컨센서스 포함 (병기값)",
            *aggregate(val.estimates, V.METHOD_WEIGHTS, price))
        for delta in (-0.10, +0.10):
            w = dict(V.METHOD_WEIGHTS)
            w["선행 이익(컨센서스)"] = round(w["선행 이익(컨센서스)"] + delta, 2)
            add("방법 추가", f"④ 포함 · 0.35→{w['선행 이익(컨센서스)']:.2f}",
                *aggregate(val.estimates, w, price))
    # 방법 하나를 통째로 빼 본다 — 그 방법이 판정을 혼자 끌고 있는지 드러난다
    for drop, name in (("업종 상대가치", "① 제외"), ("역사적 밴드", "② 제외"),
                       ("수익가치(RIM)", "③ 제외")):
        if any(e.method != drop for e in core):
            add("방법 제외", name, *aggregate([e for e in core if e.method != drop],
                                            V.METHOD_WEIGHTS, price))
    # ADR-0006으로 판정에서 ④가 빠지면서 '자기 5년 PER 중앙값' 의존은 판정 안에서는 ② 하나로
    # 줄었다. 그래도 ②를 빼면 그 배수 의존이 완전히 사라지므로 축은 남긴다(이름만 정정).
    if any(e.method != "역사적 밴드" for e in core):
        add("방법 제외", "② 제외 (PER 중앙값 의존 제거)",
            *aggregate([e for e in core if e.method != "역사적 밴드"],
                       V.METHOD_WEIGHTS, price))

    # ② RIM 지속계수 (현행 중심 0.8)
    # 0.62는 Fama & French(2000)의 수익성 평균회귀 연 38%를 지속으로 뒤집은 값이다
    # — 문헌에서 온 유일한 후보라 흔들기 목록에 넣어 둔다(2026-08-11 계획).
    for w_c in (0.6, 0.62, 1.0):
        rerun("RIM 지속계수", f"중심 0.8→{w_c}", _rim=rim_with_center(w_c))

    # ③ 자본비용 r (RIM 할인율) — R_f·MRP·베타가 모두 여기로 모인다
    # 라벨에 종목별 수치를 넣으면 축별 집계가 쪼개지므로 변형 이름은 공통으로 둔다.
    for delta in (-0.01, +0.01, +0.02):
        rerun("자본비용 r", f"k_e {delta:+.0%}p", r_equity=cc.k_e + delta)

    # ④ 베타 추정창·클리핑
    for weeks, name in ((104, "2년"), (156, "3년")):
        ke, _n = k_e_from(d.prices, d.index_prices, rf, mrp, weeks=weeks)
        if ke:
            rerun("베타 추정창", f"5년→{name}", r_equity=ke)
    ke_raw, _ = k_e_from(d.prices, d.index_prices, rf, mrp, clip=False)
    if ke_raw and cc.beta_l_raw is not None and cc.beta_l != cc.beta_l_raw:
        rerun("베타 클리핑", "0.4~2.5 해제", r_equity=ke_raw)

    # ⑤ 역사적 밴드 관측창 (현행: 확보된 전 구간 ≈ 5년)
    for yrs in (3, 7):
        rerun("밴드 관측창", f"5년→{yrs}년", actual_prices=prices_window(yrs))

    # ⑥ 피어 규모 필터 (현행 ×20)
    for f in (10.0, 40.0):
        rerun("피어 규모필터", f"×20→×{f:.0f}", comparable_peers=peers_with_factor(f))

    # ⑦ 판정 임계 (현행 로그 ±0.671 · ADR-0042)
    # ADR-0042가 "0.671이 최적이라 고른 것이 아니다"라고 못박았으므로, 이 축이 재는 것은
    # **그 값이 얼마나 안 중요한가**다. 위아래로 넉넉히 흔든다.
    base_m = V.VERDICT_LOG_THRESHOLD
    for m in (base_m * 0.7, base_m * 1.3):
        add("판정 임계", f"log±{base_m:.2f}→±{m:.2f}", val.fair_mid, val.gap,
            verdict_at(val.gap, m))

    # ⑧ RIM ROE 규칙 (#40)
    peer_roe = None
    if d.peers is not None and "roe" in getattr(d.peers, "columns", []):
        s = pd.to_numeric(d.peers.loc[~d.peers["is_self"], "roe"], errors="coerce").dropna()
        peer_roe = float(s.median()) if len(s) >= 3 else None
    for kind, name in (("hist", "최근 3년 평균만"), ("ttm", "TTM만"),
                       ("revert", "업종 중앙값으로 절반 수렴")):
        if kind == "revert" and peer_roe is None:
            continue
        rerun("RIM ROE 규칙", name, _recent_roe=roe_rule(kind, peer_roe))

    return rows


# ── #42: ②와 ④가 같은 값(자기 5년 PER 중앙값)에 함께 매달려 있는가 ──
def check_coupling(d, ind, val) -> dict:
    """PER 중앙값을 +10% 흔들어 ②·④·종합이 각각 얼마나 움직이는지 본다."""
    out = {"name": d.name}
    base = {e.method: e.mid for e in val.estimates}
    if "역사적 밴드" not in base or "선행 이익(컨센서스)" not in base:
        out["skip"] = "②·④ 중 하나가 없음"
        return out

    # 밴드 배수 전체를 +10% — _band가 돌려주는 분위 배수를 부풀린 것과 같은 효과
    def band_up(dd, cur, kind):
        b, pct, fair, q = _BAND(dd, cur, kind)
        if fair:
            fair = tuple(x * 1.10 for x in fair)
        if q:
            q = {k: (v * 1.10 if isinstance(k, int) else v) for k, v in q.items()}
        return b, pct, fair, q

    with patched(_band=band_up):
        v2 = V.compute_valuation(d, ind, r_equity=val.rim_r or 0.10)
    up = {e.method: e.mid for e in v2.estimates}
    for m in ("업종 상대가치", "역사적 밴드", "수익가치(RIM)", "선행 이익(컨센서스)"):
        if m in base and m in up:
            out[m] = up[m] / base[m] - 1
    out["종합"] = (v2.fair_mid / val.fair_mid - 1) if val.fair_mid else None

    # 신뢰도: ②·④를 한 덩어리로 세면 등급이 어떻게 달라지나
    mids = [e.mid for e in val.estimates]
    merged = [e.mid for e in val.estimates
              if e.method not in ("역사적 밴드", "선행 이익(컨센서스)")]
    pair = [e.mid for e in val.estimates
            if e.method in ("역사적 밴드", "선행 이익(컨센서스)")]
    merged.append(float(np.mean(pair)))
    out["conf_now"], out["conf_merged"] = confidence_of(mids), confidence_of(merged)

    # ②·④가 '배수를 공유'하는 정도인지, 아니면 **같은 식에 EPS만 다른 것**인지 가른다.
    #   ② = q50 × TTM EPS,  ④ = q50 × 선행 EPS  →  ④÷② 가 선행EPS÷TTM EPS와 같으면 후자다.
    eps, c = d.latest("eps"), d.consensus
    out["ratio_45"] = base["선행 이익(컨센서스)"] / base["역사적 밴드"]
    out["ratio_eps"] = (c.forward_eps / eps) if (c and c.forward_eps and eps and eps > 0) else None
    return out


# ── ④ 사이클 위상: 밴드 배수와 선행 EPS가 같은 국면을 보고 있는가 ──
def check_cycle_phase(d, val) -> dict:
    from src.data.models import currency_mismatch
    c = d.consensus
    # 통화가 섞이면 선행EPS(주가 통화)와 TTM EPS(본국 통화)의 비율이 환율배만큼 틀어진다(R1 발견 3)
    eps = None if currency_mismatch(d) else d.latest("eps")
    q50 = (val.per_q or {}).get(50)
    fv4 = next((e.mid for e in val.estimates if e.method == "선행 이익(컨센서스)"), None)
    street = (c.target_mean / c.forward_eps) if (c and c.target_mean and c.forward_eps) else None
    return {
        "name": d.name,
        "fwd_over_ttm": (c.forward_eps / eps - 1) if (c and c.forward_eps and eps and eps > 0) else None,
        "q50": q50, "street_mult": street,
        "mult_gap": (q50 / street - 1) if (q50 and street) else None,
        "fv4_over_price": (fv4 / d.price - 1) if (fv4 and d.price) else None,
        "target_over_price": (c.target_mean / d.price - 1) if (c and c.target_mean) else None,
    }


# ── ETF 임계 민감도 ─────────────────────────────────────────────────
def shake_etf():
    from src.analysis.etf import _dividend_band, _relative_ratio_pct, classify
    from src.data.bonds import current_riskfree
    from src.analysis.etf import compute_etf

    print("\n" + "=" * 78)
    print("ETF — 분위 임계(65/35)와 상대비율 5년 창")
    print("=" * 78)
    print(f"{'ETF':<22}{'유형':<12}{'배당분위':>8}{'현행':>8}{'60/40':>8}{'70/30':>8}"
          f"{'상대분위(5y)':>13}{'(3y)':>8}")
    flips = 0
    total = 0
    win_flips: list[str] = []
    for market, sym in ETF_PANEL:
        try:
            if market == "KR":
                from src.data.kr_etf_provider import fetch_kr_etf
                d = fetch_kr_etf(sym)
            else:
                from src.data.etf_provider import fetch_etf
                d = fetch_etf(sym)
            rf, _ = current_riskfree(market)
            compute_etf(d, rf=rf)   # 크래시 없이 도는지까지 함께 확인
            _cur, pct, _med, _gap = _dividend_band(d)
            rel5 = _relative_ratio_pct(d)
            # 3년 창으로 자른 상대비율 분위
            rel3 = None
            if d.prices is not None and d.index_prices is not None and d.benchmark_name:
                df = pd.concat([d.prices.rename("e"), d.index_prices.rename("b")],
                               axis=1).dropna().tail(756)
                if len(df) >= 252:
                    ratio = (df["e"] / df["b"]).replace([np.inf, -np.inf], np.nan).dropna()
                    rel3 = float((ratio < float(ratio.iloc[-1])).mean() * 100)

            def band_at(p, hi, lo):
                if p is None:
                    return "N/A"
                return "싼" if p >= hi else "비싼" if p <= lo else "중간"

            total += 1
            now, alt1, alt2 = (band_at(pct, 65, 35), band_at(pct, 60, 40),
                               band_at(pct, 70, 30))
            if pct is not None and (now != alt1 or now != alt2):
                flips += 1
            # 상대 위치(stance)는 관측창을 5년→3년으로 줄이면 라벨이 바뀌는가
            if rel5 is not None and rel3 is not None and \
                    band_at(rel5, 65, 35) != band_at(rel3, 65, 35):
                win_flips.append(f"{d.name[:14]} {rel5:.0f}→{rel3:.0f}")
            print(f"{d.name[:20]:<22}{classify(d)[1]:<12}"
                  f"{(f'{pct:.0f}' if pct is not None else 'N/A'):>8}"
                  f"{now:>8}{alt1:>8}{alt2:>8}"
                  f"{(f'{rel5:.0f}' if rel5 is not None else 'N/A'):>13}"
                  f"{(f'{rel3:.0f}' if rel3 is not None else 'N/A'):>8}")
        except Exception as e:  # noqa: BLE001
            print(f"{sym:<22}[err] {type(e).__name__}: {e}")
    if total:
        print(f"\n  배당밴드 라벨이 임계(65/35 → 60/40·70/30) 이동에 바뀐 ETF: {flips}/{total}")
        print(f"  상대 위치 라벨이 관측창(5년→3년) 축소에 바뀐 ETF: {len(win_flips)}/{total}"
              + (f"  — {', '.join(win_flips)}" if win_flips else ""))


# ── 출력 ────────────────────────────────────────────────────────────
def g(v, kind="pct"):
    if v is None:
        return "N/A"
    return f"{v:+.1%}" if kind == "pct" else f"{v:,.0f}"


def run_panel(panel):
    loaded, all_rows = [], []
    print("종목 로드 중…")
    for market, q, tag in panel:
        try:
            rf, mrp = _defaults(market)
            d, ind, _sc, cc, val = _pipeline(market, q, 9, rf, mrp)
            loaded.append((tag, d, ind, cc, val, rf, mrp))
            print(f"  ✓ {tag}: {d.name} {d.price:,.0f} {d.currency} · "
                  f"적정가 {g(val.fair_mid, 'num')} · 괴리 {g(val.gap)} → {val.verdict} "
                  f"(신뢰도 {val.confidence}, 방법 {len(val.estimates)}개)")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {tag}: {type(e).__name__}: {e}")

    print("\n" + "=" * 78)
    print("축별 민감도 — 가정을 흔들면 판정(5단계)이 뒤집히는 종목 수")
    print("=" * 78)
    for tag, d, ind, cc, val, rf, mrp in loaded:
        rows = shake(d, ind, cc, val, rf, mrp)
        for r in rows:
            r["stock"] = d.name
        all_rows += rows

    df = pd.DataFrame(all_rows)
    if df.empty:
        return loaded, df
    base_mid = {d.name: val.fair_mid for _t, d, _i, _c, val, _r, _m in loaded}
    print(f"{'축':<14}{'변형':<28}{'판정 뒤집힘':>12}{'중앙 |Δ적정가|':>15}{'최대 |Δ적정가|':>15}")
    for (axis, label), grp in df.groupby(["axis", "label"], sort=False):
        deltas = [abs(r["mid"] / base_mid[r["stock"]] - 1) for _, r in grp.iterrows()
                  if r["mid"] and base_mid.get(r["stock"])]
        flips = int(grp["flip"].sum())
        med = f"{np.median(deltas):.1%}" if deltas else "N/A"
        mx = f"{max(deltas):.1%}" if deltas else "N/A"
        print(f"{axis:<14}{label:<28}{f'{flips}/{len(grp)}':>12}{med:>15}{mx:>15}")

    # 종목 관점 — "몇 %의 종목이 어느 한 축에서라도 뒤집히나"(이슈 #51이 요구한 지표)
    per_stock = df.groupby("stock")["flip"].sum()
    shaken = int((per_stock > 0).sum())
    print(f"\n  최소 한 축에서 판정이 뒤집힌 종목: {shaken}/{len(per_stock)} "
          f"({shaken / len(per_stock):.0%})")
    for name, k in per_stock.sort_values(ascending=False).items():
        if k:
            axes = ", ".join(sorted(set(df[(df["stock"] == name) & df["flip"]]["axis"])))
            print(f"    - {name}: {int(k)}개 변형에서 뒤집힘 ({axes})")
    return loaded, df


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only_etf = "--etf" in sys.argv

    if only_etf:
        shake_etf()
        return

    panel = PANEL
    if len(args) >= 2:
        panel = [(args[0].upper(), args[1], f"{args[0].upper()} {args[1]}")]

    loaded, df = run_panel(panel)

    if len(panel) == 1 and not df.empty:
        print("\n[축별 상세]")
        print(f"{'축':<14}{'변형':<28}{'적정가':>14}{'괴리율':>10}{'판정':>12}{'뒤집힘':>8}")
        for _, r in df.iterrows():
            print(f"{r['axis']:<14}{r['label']:<28}{g(r['mid'], 'num'):>14}"
                  f"{g(r['gap']):>10}{str(r['verdict']):>12}"
                  f"{('예' if r['flip'] else ''):>8}")

    print("\n" + "=" * 78)
    print("#42 — ②(역사적 밴드)와 ④(선행 이익)가 같은 배수에 매달려 있는가")
    print("=" * 78)
    print("  PER 밴드 분위를 +10% 흔들었을 때 각 방법의 중심값 이동")
    print(f"{'종목':<16}{'①상대':>9}{'②밴드':>9}{'③RIM':>9}{'④선행':>9}{'종합':>9}"
          f"{'신뢰도(현행→②④병합)':>22}{'④÷②':>8}{'선행EPS÷TTM':>12}")
    for tag, d, ind, cc, val, rf, mrp in loaded:
        c = check_coupling(d, ind, val)
        if "skip" in c:
            print(f"{d.name[:14]:<16}{'— ' + c['skip']:>60}")
            continue
        conf = f"{c['conf_now']} → {c['conf_merged']}"
        r45 = f"{c['ratio_45']:.2f}배"
        reps = f"{c['ratio_eps']:.2f}배" if c["ratio_eps"] else "N/A"
        print(f"{d.name[:14]:<16}{g(c.get('업종 상대가치')):>9}{g(c.get('역사적 밴드')):>9}"
              f"{g(c.get('수익가치(RIM)')):>9}{g(c.get('선행 이익(컨센서스)')):>9}"
              f"{g(c.get('종합')):>9}{conf:>22}{r45:>8}{reps:>12}")
    print("  ④÷②와 선행EPS÷TTM이 같으면, 두 방법은 '배수를 공유'하는 게 아니라 "
          "같은 식(q50 × EPS)에 EPS만 다른 것이다.")

    print("\n" + "=" * 78)
    print("④ 사이클 위상 — 밴드 배수(과거)와 선행 EPS(미래)가 같은 국면인가")
    print("=" * 78)
    print(f"{'종목':<16}{'선행EPS/TTM':>12}{'5년PER중앙':>11}{'목표가 내재배수':>14}"
          f"{'배수 괴리':>10}{'④/현재가':>10}{'목표가/현재가':>13}")
    gaps = []
    for tag, d, ind, cc, val, rf, mrp in loaded:
        c = check_cycle_phase(d, val)
        q50 = f"{c['q50']:.1f}배" if c["q50"] else "N/A"
        street = f"{c['street_mult']:.1f}배" if c["street_mult"] else "N/A"
        print(f"{d.name[:14]:<16}{g(c['fwd_over_ttm']):>12}{q50:>11}{street:>14}"
              f"{g(c['mult_gap']):>10}{g(c['fv4_over_price']):>10}"
              f"{g(c['target_over_price']):>13}")
        if c["mult_gap"] is not None:
            gaps.append(c["mult_gap"])
    if gaps:
        # ADR-0003·_forward_value 도크스트링은 "목표주가 내재 멀티플과 +2% 이내 일치"를 근거로 든다.
        # 그 문장이 중앙값 이야기인지, 개별 종목에서도 성립하는지를 여기서 갈라 본다.
        print(f"\n  배수 괴리(자기5년PER중앙 ÷ 목표가 내재배수 − 1) n={len(gaps)}: "
              f"중앙값 {np.median(gaps):+.1%} · 절대값 중앙 {np.median(np.abs(gaps)):.1%} · "
              f"범위 {min(gaps):+.0%} ~ {max(gaps):+.0%}")

    shake_etf()


if __name__ == "__main__":
    main()
