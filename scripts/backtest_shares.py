"""주식수 드리프트 측정 — 시총 근사가 결론을 만들고 있지 않은지 확인할 재료.

    python scripts/backtest_shares.py

사전등록 §1이 약속한 측정이다. 패널은 `시총_t = 현재주식수 × 수정주가_t`로 두는데,
**이것은 방향이 있는 근사다**:

    나중에 크게 증자한 회사는 과거 시총이 부풀려진다 → 과거에 '비싸' 보인다
    → 그리고 실제로 증자 많은 회사는 이후 수익률이 나쁜 경향이 있다(net share issuance)

즉 **근사가 예측력을 지어낼 수 있다.** 크기를 재지 않으면 결론을 믿을 수 없다.

DART `stockTotqySttus.json`으로 연도별 보통주 발행총수를 받아 드리프트를 낸다.
공시값은 **액면분할 전 기준**이라 그대로는 수정주가와 못 맞춘다 — 연도 간 비율이
깔끔한 분할비(2·3·5·10·20·50·100배와 그 역수)에 가까우면 분할로 보고 제거하고,
남은 것만 증자·감자로 센다.

전 종목 3개 연도(2017·2021·2025) = 종목당 3콜. 수집기(6콜)와 합쳐도 일일 한도 안이다.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import src.data.opendart as od                                        # noqa: E402
from src.data import ca_bundle                                        # noqa: E402

ca_bundle.install()   # ADR-0027

OUT = ROOT / "data" / "backtest"
RAW = OUT / "raw"
YEARS = (2016, 2020, 2024)          # 사업연도 — 2017·2021·2025 리밸런싱일에 알려진 값
WORKERS = 6
# 흔한 액면분할·병합 비율. 이 근방이면 주식수 변화가 아니라 기준 변경으로 본다.
SPLIT_RATIOS = (2, 2.5, 3, 4, 5, 8, 10, 20, 25, 50, 100)
SPLIT_TOL = 0.03


def _issued(key: str, corp: str, year: int) -> float | None:
    import requests
    try:
        r = requests.get(f"{od.BASE}/stockTotqySttus.json", params={
            "crtfc_key": key, "corp_code": corp, "bsns_year": str(year),
            "reprt_code": "11011"}, timeout=30)
        j = r.json()
    except Exception:
        return None
    if j.get("status") != "000":
        return None
    for it in j.get("list", []):
        if "보통주" not in str(it.get("se", "")):
            continue
        v = od._num(str(it.get("istc_totqy", "")).replace(",", ""))
        if v and v > 0:
            return float(v)
    return None


def is_split(ratio: float) -> bool:
    """이 비율이 액면분할·병합으로 설명되나."""
    for r in SPLIT_RATIOS:
        for cand in (r, 1.0 / r):
            if abs(ratio / cand - 1.0) < SPLIT_TOL:
                return True
    return False


def main() -> int:
    key = od.get_api_key()
    if not key:
        print("OPENDART_API_KEY가 없다.")
        return 1
    metas = sorted(RAW.glob("meta_*.json"))
    if not metas:
        print("수집 데이터가 없다 — backtest_collect.py를 먼저 돌려라.")
        return 1
    cmap = od.get_corp_code_map()

    recs = []
    for p in metas:
        m = json.loads(p.read_text(encoding="utf-8"))
        if m["code"] in cmap.index:
            recs.append(m)
    print(f"{len(recs)}종목 × {len(YEARS)}개 연도 = 약 {len(recs) * len(YEARS):,}콜\n")

    def one(m):
        corp = cmap.at[m["code"], "corp_code"]
        if isinstance(corp, pd.Series):
            corp = corp.iloc[0]
        out = {"code": m["code"], "shares_now": m["shares"]}
        for y in YEARS:
            out[f"y{y}"] = _issued(key, corp, y)
        return out

    t0, rows, done = time.time(), [], 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = [ex.submit(one, m) for m in recs]
        for f in as_completed(futs):
            rows.append(f.result())
            done += 1
            if done % 200 == 0 or done == len(recs):
                print(f"  {done}/{len(recs)}  {time.time() - t0:.0f}초")

    df = pd.DataFrame(rows).set_index("code")
    for y in YEARS:
        raw = df["shares_now"] / df[f"y{y}"]
        split = raw.map(lambda x: is_split(x) if np.isfinite(x) else False)
        df[f"drift{y}"] = raw.where(~split)          # 분할로 설명되면 비운다
    df.to_parquet(OUT / "share_drift.parquet")

    print("\n주식수 드리프트 = 현재 주식수 ÷ 그 시점 발행총수 (분할비는 제거)")
    print(f"{'기준연도':<10}{'측정됨':>8}{'분할판정':>9}{'중앙':>9}"
          f"{'90분위':>9}{'>1.5배':>9}{'>3배':>8}")
    print("─" * 62)
    for y in YEARS:
        d = df[f"drift{y}"].dropna()
        nsplit = int(df[f"y{y}"].notna().sum() - len(d))
        if not len(d):
            continue
        print(f"{y:<10}{len(d):>8}{nsplit:>9}{d.median():>9.3f}"
              f"{d.quantile(0.90):>9.3f}{int((d > 1.5).sum()):>9}{int((d > 3).sum()):>8}")

    d17 = df["drift2016"].dropna()
    if len(d17):
        big = int((d17 > 1.5).sum())
        print(f"\n2017년 기준으로 주식수가 1.5배 넘게 늘어난 종목이 {big}곳"
              f"({big / len(d17):.1%})이다.")
        print("이들을 빼고 결론이 유지되는지는 check_backtest_combos.py --exclude-drift가 잰다.")
    print(f"\n저장 → {OUT / 'share_drift.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
