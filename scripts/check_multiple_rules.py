# -*- coding: utf-8 -*-
"""④ 선행 이익의 타깃 멀티플 규칙 실증 비교: python scripts/check_multiple_rules.py

2026-07-19 실행 결과(11종목): 자기 5년 PER 중앙값이 가격오차 0.26으로 최소,
목표가 내재 멀티플과 중앙값 +0.02 일치 → valuation._forward_value의 규칙 근거.
피어 선행PER 원본은 AI 피어에 소형주가 섞이면 체계적 과소(오차 0.65).

**2026-08-10 — 후보 둘(F1·F2)을 더하고 채점을 둘로 갈랐다.**
그 시합에 ①의 회귀 적정 PER이 없었다. 회귀는 ADR-0014로 이 측정 **뒤에** 들어왔다.
그냥 교체하면 측정으로 이긴 규칙을 측정 없이 빼는 모양이 된다.
사전등록: docs/superpowers/specs/2026-08-10-forward-multiple-design.md

    ㉠ |log(예측가 / 컨센서스 목표주가)|   ← **채택 문턱은 여기에만 건다**
    ㉡ |log(예측가 / 현재가)| (LNT 2002)   ← 함께 보고하되 채택 기준으로 쓰지 않는다

㉡에는 구조적 함정이 있다. 자기 과거 PER 중앙값은 **현재가에 붙는다** — PER이
평균회귀하면 `자기중앙값 × 현재EPS ≈ 현재가`이기 때문이다. 적정가 방법을 ㉡으로
고르면 "현재가를 가장 잘 맞히는 것"을 뽑게 되고, 그러면 ④는 늘 "적정"이라고 말한다.
체온계를 고르면서 "지금 체온과 똑같이 나오는 것"을 고르는 것과 같다.

㉡을 함께 내는 것은 현재가에서 얼마나 떨어졌는지를 **숨기지 않기 위해서**지 고르기
위해서가 아니다. 어느 지표에 문턱을 걸지를 결과 보고 정하면 사전등록이 아니다.

기존 후보 다섯(A·B·C·E·G)은 **지우지 않는다.** 재실행에서 B가 여전히 0.26 근처인지가
이 측정의 위생 검사다 — 크게 움직였으면 후보 비교보다 그것을 먼저 봐야 한다.
"""
import math
import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.scoring import peer_median, sanitize_peer_frame  # noqa: E402
from src.analysis.warranted import warranted_multiple             # noqa: E402
from src.data.universe_multiples import coefficients_or_none      # noqa: E402
from src.web.serialize import _defaults, _pipeline                # noqa: E402

STOCKS = [("KR", "005930"), ("KR", "035420"), ("KR", "005380"), ("KR", "000660"),
          ("KR", "035720"), ("KR", "051910"),
          ("US", "AAPL"), ("US", "MSFT"), ("US", "KO"), ("US", "JNJ"),
          ("US", "WMT"), ("US", "GOOGL")]

SIZE_FACTOR = 20.0   # 시총이 자사 대비 1/20~20배 밖이면 비교 부적격

BASE_KEY = "B_자기5년중앙"        # 기준선 — 현행 규칙
NEW_KEYS = ("F1_회귀PER_현ROE", "F2_회귀PER_선행ROE")
KEYS = ("A_피어원본", BASE_KEY, "C_min(A,B)", "E_피어사이즈필터", "G_중간(E,B)") + NEW_KEYS

# 사전등록한 성공선 — **측정 뒤에 고치지 않는다.**
ADOPT_GAP = 0.05   # ㉠이 B 대비 이만큼 낮아야 채택. 미달이면 '구별되지 않는다'
GUARD_GAP = 0.30   # ㉠은 통과했는데 ㉡이 이만큼 나빠지면 보류하고 원인부터 본다
PHASE_SHIFT = 0.5  # |선행EPS/TTM − 1| 이 값 이상이면 '국면 전환' (R2 실측에서 온 문턱)


def size_filtered_fwd(sp, self_mcap):
    m = (~sp["is_self"].astype(bool)) & sp["market_cap"].notna() \
        & (sp["market_cap"] >= self_mcap / SIZE_FACTOR) \
        & (sp["market_cap"] <= self_mcap * SIZE_FACTOR)
    v = sp.loc[m, "forward_per"].dropna()
    return (float(v.median()), int(len(v))) if len(v) >= 2 else (None, int(len(v)))


def reg_per(coef, d, roe):
    """회귀 적정 PER. 계수가 없거나 규모가 학습 범위 밖이면 None(원 코드와 같은 규약)."""
    if not coef or roe is None:
        return None
    return warranted_multiple(coef.get("per"), d.market_cap, d.sector, roe)["multiple"]


def _num(v, sign=False):
    if v is None:
        return "—"
    return f"{v:+.3f}" if sign else f"{v:.3f}"


def summarize(rows, title):
    """후보별 ㉠·㉡ 중앙값 표를 찍고 통계를 돌려준다."""
    print(f"\n===== {title} (n={len(rows)}) =====")
    print(f"{'후보':<22}{'n':>4}{'㉠|log(예측/목표가)|':>22}{'vs B':>9}"
          f"{'㉡|log(예측/현재가)|':>22}{'㉠부호':>10}")
    print("─" * 89)
    stat = {}
    for k in KEYS:
        tgt = [r[k + "_tgt_abs"] for r in rows if r.get(k + "_tgt_abs") is not None]
        prc = [r[k + "_err"] for r in rows if r.get(k + "_err") is not None]
        sgn = [r[k + "_vs_tgt"] for r in rows if r.get(k + "_vs_tgt") is not None]
        stat[k] = {"n": len(tgt),
                   "tgt": float(np.median(tgt)) if tgt else None,
                   "prc": float(np.median(prc)) if prc else None,
                   "sgn": float(np.median(sgn)) if sgn else None}
    base = stat[BASE_KEY]["tgt"]
    for k in KEYS:
        s = stat[k]
        if k == BASE_KEY:
            d_txt = "기준"
        elif s["tgt"] is not None and base is not None:
            d_txt = f"{s['tgt'] - base:+.3f}"
        else:
            d_txt = "—"
        print(f"{k:<22}{s['n']:>4}{_num(s['tgt']):>22}{d_txt:>9}"
              f"{_num(s['prc']):>22}{_num(s['sgn'], sign=True):>10}")
    return stat


def verdict(stat):
    """사전등록한 성공선을 그대로 적용한다. 판단을 사람에게 미루지 않는다 —
    미루면 숫자를 보고 기준이 흔들린다(ADR-0003이 금지한 그 일이다)."""
    print("\n===== 사전등록 성공선 =====")
    print(f"채택 : ㉠이 B 대비 {ADOPT_GAP:.2f} 이상 낮을 것")
    print(f"보류 : ㉠은 통과했는데 ㉡이 B 대비 {GUARD_GAP:.2f} 이상 나빠지면 원인부터 본다")
    print("근거 : docs/superpowers/specs/2026-08-10-forward-multiple-design.md")
    print("한계 : 표본 10~12종목이라 통계 검정이 아니다 · 목표주가에는 낙관 편향이 있다")
    base = stat[BASE_KEY]
    if base["tgt"] is None:
        print("\n기준선 B의 ㉠을 내지 못했다 — 판정 불가. 원인부터 본다.")
        return
    for k in NEW_KEYS:
        s = stat[k]
        if s["tgt"] is None:
            print(f"\n{k}: 값 없음 — 판정 불가")
            continue
        gain = base["tgt"] - s["tgt"]
        drift = (s["prc"] - base["prc"]) if (s["prc"] is not None
                                             and base["prc"] is not None) else None
        if gain < ADOPT_GAP:
            call = f"미달 (문턱 {ADOPT_GAP:.2f}) — 구별되지 않는다. 현행 유지"
        elif drift is not None and drift >= GUARD_GAP:
            call = f"통과했으나 ㉡이 {drift:+.3f} — **보류**. 계수 오염부터 확인"
        else:
            call = "통과"
        print(f"\n{k}: ㉠ 개선 {gain:+.3f} → {call}")
        if s["n"] < base["n"]:
            print(f"    ⚠ 커버리지 {s['n']} < B {base['n']} — ④가 사라지면 "
                  "'컨센서스 반영' 병기값도 함께 사라진다(ADR-0006 불변식)")


def main():
    rows, coefs = [], {}
    for market, q in STOCKS:
        try:
            rf, mrp = _defaults(market)
            d, ind, scores, cc, val = _pipeline(market, q, 9, rf, mrp)
            c = d.consensus
            fwd = c.forward_eps if c else None
            tgt = c.target_mean if c else None
            if not fwd or fwd <= 0:
                print(f"[skip] {q}: 선행 EPS 없음")
                continue
            if market not in coefs:
                coefs[market] = coefficients_or_none(market)
            coef = coefs[market]

            sp = sanitize_peer_frame(d.peers)
            peer_all = peer_median(sp, "forward_per")
            peer_sz, _n_sz = size_filtered_fwd(sp, d.market_cap)
            q50 = (val.per_q or {}).get(50)

            # ①이 쓰는 것과 **같은** ROE 정의다 (valuation._relative_value: eps / bps).
            eps_ttm = d.latest("eps")
            equity = d.latest("total_equity")
            shares = d.shares_outstanding
            bps = (equity / shares) if (equity and shares) else None
            roe_now = (eps_ttm / bps) if (eps_ttm is not None and bps) else None
            # F2는 ⑤의 규칙을 그대로 옮긴 것이다 — 배수를 만드는 수익성도 곱해질 이익과
            # 같은 국면에서 낸다(valuation._normalized_value 도크스트링). ROE 정의상
            # 선행EPS × 주식수 ÷ 자기자본 = 선행EPS / bps로 같다.
            roe_fwd = (fwd / bps) if bps else None

            cands = {
                "A_피어원본": peer_all,
                BASE_KEY: q50,
                "C_min(A,B)": min([x for x in (peer_all, q50) if x], default=None),
                "E_피어사이즈필터": peer_sz,
                "G_중간(E,B)": (0.5 * (peer_sz + q50)) if (peer_sz and q50) else (peer_sz or q50),
                "F1_회귀PER_현ROE": reg_per(coef, d, roe_now),
                "F2_회귀PER_선행ROE": reg_per(coef, d, roe_fwd),
            }
            growth = (fwd / eps_ttm - 1) if (eps_ttm and eps_ttm > 0) else None
            rec = {"종목": f"{d.name}({q})", "price": d.price, "growth": growth,
                   "street_mult": (tgt / fwd) if tgt else None}
            for k, mult in cands.items():
                rec[k + "_err"] = abs(math.log(mult * fwd / d.price)) if mult else None
                v = math.log(mult * fwd / tgt) if (mult and tgt) else None
                rec[k + "_vs_tgt"] = v
                rec[k + "_tgt_abs"] = abs(v) if v is not None else None
            rows.append(rec)
            g_txt = f"{growth:+.0%}" if growth is not None else "—"
            street = rec["street_mult"]
            print(f"[ok] {rec['종목']}: 선행/TTM {g_txt} · 목표가 내재 "
                  f"{round(street, 1) if street else '—'}배 | "
                  + " ".join(f"{k}={round(v, 1) if v else None}" for k, v in cands.items()))
        except Exception as e:
            print(f"[err] {q}: {e}")
            traceback.print_exc(limit=1)

    if not rows:
        print("표본이 하나도 없다 — 캐시·네트워크를 먼저 본다.")
        return
    stat = summarize(rows, "규칙별 요약 — 전체")

    # 부 지표 — R2가 지목한 바로 그 자리다. 전체에서 좋아졌는데 여기서 안 좋아지면
    # 원인이 우리가 생각한 것(배수와 이익의 국면 불일치)이 아니라는 뜻이다.
    shifted = [r for r in rows
               if r.get("growth") is not None and abs(r["growth"]) >= PHASE_SHIFT]
    if shifted:
        summarize(shifted, f"국면 전환 종목만 (|선행/TTM−1| ≥ {PHASE_SHIFT:.0%})")
    else:
        print(f"\n국면 전환 종목(|선행/TTM−1| ≥ {PHASE_SHIFT:.0%})이 표본에 없다 — "
              "부 지표는 판정 불가. 그 사실을 ADR에 적을 것.")

    verdict(stat)


if __name__ == "__main__":
    main()
