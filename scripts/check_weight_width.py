"""가중치 폭이 실제로 얼마인가 — 화면에 적는 범위의 근거 (ADR-0041).

    python scripts/check_weight_width.py
    python scripts/check_weight_width.py --market KR

계획: `docs/superpowers/plans/2026-08-12-weight-width-in-fold.md`.

**이 스크립트는 화면에 박을 상수를 고르지 않는다.** 화면은 종목마다 자기 폭을
계산해 보여준다(`valuation._rank_allowed_span`). 여기서 재는 것은 그 폭이 **전 종목에서
어떻게 흩어지는가**뿐이다 — ADR과 인계문이 "대개 이 정도"라고 말할 때 쓸 근거고,
상수 하나를 박지 않기로 한 이유(중앙값과 꼬리가 두 배 넘게 갈린다)의 증거다.

무엇이 폭인가
--------------
LNT(2002/2007)는 순위 ④ > ①②⑤ > ③까지만 정하고 숫자는 정하지 않는다. 그러면 그
순위를 지키는 **모든** 가중치가 동등하게 정당하고, 그것들이 만드는 값의 폭이 곧
**문헌이 실제로 못 정한 만큼**이다. 칸 안에서는 순서가 없으므로 같게 두면 자유도는
아래 칸의 몫 ρ 하나뿐이고(w_아래 = ρ·w_위), f(ρ)가 ρ에 단조라 양 끝이 곧 최소·최대다.

    ρ = 0 → (①+⑤)/2      ρ = 0.6 → 현행       ρ = 1 → 동일가중

⚠ 인계문의 두 탐침은 이 범위 밖이다 — 재현도 안 됐다
------------------------------------------------------
`docs/HANDOFF-AXES.md` 4단계는 **동일가중 90분위 +10.9% · ③가중 2배 +15.3%**를 적어
두고 "다시 재라"고 했다. 두 가지가 문제였다.

1. **두 탐침 모두 순위를 어긴다.** 재정규화하면 동일가중은 (.333/.333/.333)으로 ③을
   **동률**로 만들고, ③가중 2배는 (.3125/**.375**/.3125)로 ③을 **최상위로 역전**시킨다.
   그 값을 "순위가 허용하는 범위"라고 화면에 적으면 거짓이 된다.
2. **그 숫자를 재현하지 못했다.** 같은 커밋된 패널에서 같은 이름의 값을 재면
   +4.09% / +5.75%가 나온다(아래 표가 매번 다시 낸다). 스테일 패널로 재도
   3.88% / 5.45%라 패널 탓이 아니다. 두 값의 비(×1.40)만 정확히 같다.

그래서 이 스크립트는 **인계문의 두 탐침도 함께 낸다** — 지우고 새 값만 적으면 왜
두 배 넘게 달라졌는지가 기록에서 사라진다. 다음 사람이 같은 데서 헤매지 않게 남긴다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.valuation import METHOD_WEIGHTS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DIRS = {"KR": "backtest", "US": "backtest_us"}
OK, BAD = "[확인]", "[문제]"

ONE, THREE, FIVE = "업종 상대가치", "수익가치(RIM)", "정규화 이익"
PCTS = (50, 75, 90, 99)


def load(market: str) -> pd.DataFrame:
    p = ROOT / "data" / DIRS[market] / "panel.parquet"
    if not p.exists():
        raise SystemExit(f"{BAD} 패널이 없다: {p}\n"
                         f"    python scripts/backtest_panel.py --market {market}")
    d = pd.read_parquet(p)
    # 세 축이 **전부 판정에 든** 행만 본다. n_methods는 `len(v.weights)`라
    # 게이트로 ③이 빠진 행은 여기서 자동으로 빠진다 — 그 행은 칸이 하나뿐이라
    # 문헌이 자유도를 남기지 않고, 폭도 정의되지 않는다.
    s = d[d["n_methods"] == 3]
    missing = [c for c in (ONE, THREE, FIVE) if c not in s.columns]
    if missing:
        raise SystemExit(f"{BAD} 패널에 축 열이 없다: {missing} — 옛 패널이다.")
    return s


def legs(s: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """패널의 축은 log(적정가/주가)로 실린다 — 주가로 되돌려 비(比)로 다룬다.

    전부 주가 대비 배수라 종목 규모가 섞이지 않는다.
    """
    f1, f3, f5 = (np.exp(s[c].to_numpy(dtype=float)) for c in (ONE, THREE, FIVE))
    w1, w3 = METHOD_WEIGHTS[ONE], METHOD_WEIGHTS[THREE]
    base = (w1 * f1 + w3 * f3 + w1 * f5) / (2 * w1 + w3)
    return f1, f3, f5, base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=tuple(DIRS))
    a = ap.parse_args()

    s = load(a.market)
    f1, f3, f5, base = legs(s)

    # 자기검사 — 우리가 재구성한 base가 패널의 fair_mid와 같은가.
    # 다르면 아래 폭은 전부 딴 값을 잰 것이다(실제로 이 검사가 축이 로그로
    # 실린다는 것을 잡아냈다 — 그전에는 100% 어긋나 있었다).
    got = base * s["price"].to_numpy(dtype=float)
    err = np.max(np.abs(got - s["fair_mid"].to_numpy(dtype=float))
                 / np.abs(s["fair_mid"].to_numpy(dtype=float)))
    mark = OK if err < 1e-9 else BAD
    print(f"{mark} {a.market} · {len(s):,}행 (①③⑤ 전부 판정에 든 행) · "
          f"fair_mid 재구성 최대오차 {err:.2e}")
    if err >= 1e-9:
        return 1

    lo = np.minimum((f1 + f5) / 2, (f1 + f3 + f5) / 3)     # ρ=0 과 ρ=1 중 작은 쪽
    hi = np.maximum((f1 + f5) / 2, (f1 + f3 + f5) / 3)

    print("\n■ 문헌 순위가 허용하는 폭 — 화면이 종목마다 보여주는 그 범위")
    print("  (현재 표시값 대비. 반폭 = (hi−lo)/2 ÷ 현행)")
    half = (hi - lo) / 2 / base
    print("    반폭        " + " · ".join(
        f"{p}분위 {np.nanpercentile(half, p) * 100:5.2f}%" for p in PCTS))
    for nm, v in (("아래 끝 (ρ=0)", lo), ("위 끝 (ρ=1)", hi)):
        rel = v / base - 1
        print(f"    {nm:<12}" + " · ".join(
            f"{p}분위 {np.nanpercentile(np.abs(rel), p) * 100:5.2f}%" for p in PCTS))

    # **상수 하나를 박지 않기로 한 이유가 이 두 줄이다.**
    print(f"\n  중앙값 {np.nanpercentile(half, 50) * 100:.2f}% 대 "
          f"90분위 {np.nanpercentile(half, 90) * 100:.2f}% — "
          f"{np.nanpercentile(half, 90) / np.nanpercentile(half, 50):.1f}배 갈린다.")
    print("  한 숫자를 화면에 박으면 대부분 종목에서 과장이 되고 꼬리에서는 과소가 된다.")

    print("\n■ 인계문의 두 탐침 — 순위를 어긴다(참고용으로만 낸다)")
    print(f"  {'':16}{'재정규화 가중 ①/③/⑤':<26}{'순위':<6}{'부호있는 90분위':>15}"
          f"{'|변화| 90분위':>14}")
    w1, w3 = METHOD_WEIGHTS[ONE], METHOD_WEIGHTS[THREE]
    probes = {
        "동일가중": ((f1 + f3 + f5) / 3, (1, 1, 1)),
        "③가중 2배": ((w1 * f1 + 2 * w3 * f3 + w1 * f5) / (2 * w1 + 2 * w3),
                    (w1, 2 * w3, w1)),
    }
    for nm, (v, raw) in probes.items():
        tot = sum(raw)
        ww = tuple(x / tot for x in raw)
        keeps = ww[1] < ww[0] and ww[1] < ww[2]
        rel = v / base - 1
        print(f"  {nm:<16}{ww[0]:.3f} / {ww[1]:.3f} / {ww[2]:.3f}{'':<8}"
              f"{'지킴' if keeps else '어김':<6}"
              f"{np.nanpercentile(rel, 90) * 100:>+14.2f}%"
              f"{np.nanpercentile(np.abs(rel), 90) * 100:>13.2f}%")
    print("\n  인계문이 적어 둔 값은 +10.9% / +15.3%였다. 위 표가 그것을 재현하지 못한다 —")
    print("  비(×1.40)만 같다. 원인은 밝히지 못했고 ADR-0041에 불일치로 남겼다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
