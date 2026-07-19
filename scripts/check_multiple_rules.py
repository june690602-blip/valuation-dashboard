# -*- coding: utf-8 -*-
"""④ 선행 이익의 타깃 멀티플 규칙 실증 비교: python scripts/check_multiple_rules.py

Liu·Nissim·Thomas(2002, JAR)의 기준을 따른다 — 좋은 멀티플은 현재 가격을 잘
설명한다(|log(예측가/현재가)| 최소). 예측가 = 규칙별 멀티플 × 컨센서스 선행 EPS.
보조 지표로 log(예측가/컨센서스 목표주가)도 본다 (0이면 증권가 내재 멀티플과 일치).

2026-07-19 실행 결과(11종목): 자기 5년 PER 중앙값이 가격오차 0.26으로 최소,
목표가 내재 멀티플과 중앙값 +0.02 일치 → valuation._forward_value의 규칙 근거.
피어 선행PER 원본은 AI 피어에 소형주가 섞이면 체계적 과소(오차 0.65).
"""
import math
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.scoring import peer_median, sanitize_peer_frame  # noqa: E402
from src.web.serialize import _defaults, _pipeline  # noqa: E402

STOCKS = [("KR", "005930"), ("KR", "035420"), ("KR", "005380"), ("KR", "000660"),
          ("KR", "035720"), ("KR", "051910"),
          ("US", "AAPL"), ("US", "MSFT"), ("US", "KO"), ("US", "JNJ"),
          ("US", "WMT"), ("US", "GOOGL")]

SIZE_FACTOR = 20.0   # 시총이 자사 대비 1/20~20배 밖이면 비교 부적격


def size_filtered_fwd(sp, self_mcap):
    m = (~sp["is_self"].astype(bool)) & sp["market_cap"].notna() \
        & (sp["market_cap"] >= self_mcap / SIZE_FACTOR) \
        & (sp["market_cap"] <= self_mcap * SIZE_FACTOR)
    v = sp.loc[m, "forward_per"].dropna()
    return (float(v.median()), int(len(v))) if len(v) >= 2 else (None, int(len(v)))


def main():
    rows = []
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
            sp = sanitize_peer_frame(d.peers)
            peer_all = peer_median(sp, "forward_per")
            peer_sz, n_sz = size_filtered_fwd(sp, d.market_cap)
            q50 = (val.per_q or {}).get(50)
            cands = {
                "A_피어원본": peer_all,
                "B_자기5년중앙": q50,
                "C_min(A,B)": min([x for x in (peer_all, q50) if x], default=None),
                "E_피어사이즈필터": peer_sz,
                "G_중간(E,B)": (0.5 * (peer_sz + q50)) if (peer_sz and q50) else (peer_sz or q50),
            }
            rec = {"종목": f"{d.name}({q})", "price": d.price,
                   "street_mult": (tgt / fwd) if tgt else None}
            for k, mult in cands.items():
                rec[k + "_err"] = abs(math.log(mult * fwd / d.price)) if mult else None
                rec[k + "_vs_tgt"] = math.log(mult * fwd / tgt) if (mult and tgt) else None
            rows.append(rec)
            print(f"[ok] {rec['종목']}: 목표가 내재 {rec['street_mult'] and round(rec['street_mult'], 1)}배 | "
                  + " ".join(f"{k}={v and round(v, 1)}" for k, v in cands.items()))
        except Exception as e:
            print(f"[err] {q}: {e}")
            traceback.print_exc(limit=1)

    if not rows:
        return
    import numpy as np
    print(f"\n===== 규칙별 요약 (n={len(rows)}) =====")
    print(f"{'규칙':<14}{'중앙값|log오차| vs 현재가':>24}{'중앙값 log(예측/목표가)':>24}")
    for k in ("A_피어원본", "B_자기5년중앙", "C_min(A,B)", "E_피어사이즈필터", "G_중간(E,B)"):
        errs = [r[k + "_err"] for r in rows if r.get(k + "_err") is not None]
        vst = [r[k + "_vs_tgt"] for r in rows if r.get(k + "_vs_tgt") is not None]
        if errs:
            print(f"{k:<14}{np.median(errs):>20.3f} (n={len(errs)})"
                  f"{np.median(vst):>18.3f} (n={len(vst)})")


if __name__ == "__main__":
    main()
