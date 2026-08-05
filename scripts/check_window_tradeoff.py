"""창을 5년에서 8년으로 늘리면 무엇을 잃고 무엇을 얻나 — 바꾸기 전에 재는 것.

    python scripts/check_window_tradeoff.py --limit 20

`check_dart_depth.py`가 *"6년은 DART가 아니라 쿼리가 정한 값"*임을 보였다(연수 중앙 13년).
그래서 창을 늘릴 수 **있다**는 것까지는 안다. 이 스크립트는 늘리는 것이 **값어치가 있나**를
잰다 — 인계문이 남긴 경고 넷이 전부 측정 가능한 주장이기 때문이다.

재는 것 넷:

  A. 사다리 비용 — 보고서를 3년 간격으로 받으면 몇 년치가 되고, 몇 번 부르고, 몇 초 걸리나.
     `check_dart_depth.py`는 연도마다 하나씩 12번 불렀지만 **보고서 하나에 3년이 들어 있어**
     3년 간격이면 그 1/3로 같은 깊이가 나온다. 경고 1의 "2회 → 12회"가 그대로인지 본다.
  B. 캐시 — 두 번째 조회가 실제로 공짜인가. 느려지는 자리가 '매 조회'인지 '하루 첫 조회'인지.
  C. 창 5 대 8 — ⑤ 정규화 이익과 EPV 정상 영업이익이 각각 어떻게 바뀌나.
     **커버리지가 나빠지는 원인을 둘로 갈라 센다:**
       (가) 이력이 짧아 창을 못 채움 → `NORMALIZE_MIN_YEARS`(3)는 그대로이므로 이론상 0이다
       (나) 창이 길어져 옛 적자가 평균에 들어와 **정상 이익이 음수**가 됨 → 이쪽이 진짜 대가
     경고 3이 *"커버리지는 반드시 나빠진다"*고 적었는데, 그 문장이 (가)를 뜻한다면 틀렸다.
  D. 값의 이동 — 창을 바꾸면 적정가가 얼마나 움직이나. 안 움직이면 할 이유가 없다.
  E. **창이 길수록 정상 이익이 덜 흔들리나** — 경고 3을 부분적으로 우리 측정으로 바꾼다.
     A&B가 보고한 것은 *수익률 프리미엄*이라 백테스트(작업 B) 없이는 못 잰다. 그러나
     정규화가 **하겠다고 말한 일**은 "사이클 위치에 덜 휘둘리는 이익을 만든다"이고,
     그것은 잴 수 있다 — 창을 한 해씩 뒤로 밀어 세 번 재서 값이 얼마나 튀는지 본다.
     기준 연도가 한 해 달라졌다고 정상 이익이 크게 바뀌면 그것은 '정상'이 아니다.
     **이 검사는 8년이 5년보다 수익률이 낫다는 것을 증명하지 않는다.** 흔들림만 잰다.

**제품 코드는 한 줄도 안 바꾼다.** 창 상수는 측정 중에만 모듈 속성으로 갈아 끼운다
(`valuation.NORMALIZE_WINDOW`). `epv.py`는 그 값을 `from ... import`로 **한 번 묶어 두므로**
그쪽도 따로 갈아 끼워야 한다 — 그 사실 자체가 경고 4가 말한 결합이다.

네트워크와 `OPENDART_API_KEY`가 필요하다. CI가 아니라 `check_dart_depth.py`와 같은 수동 계열.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis import epv as epv_mod                              # noqa: E402
from src.analysis import valuation as val_mod                        # noqa: E402
from src.data.opendart import (_dart_financials_df, _fetch_report,   # noqa: E402
                               _parse_report, get_api_key,
                               get_corp_code_map)
from src.data.universe import get_kr_listing                         # noqa: E402

# OpenDART 구조화 재무의 시작 연도. 2015년 보고서가 2013년치를 담는다(당기·전기·전전기).
FIRST_YEAR = 2015
WINDOWS = (5, 8)


def _ladder(base: int) -> list[int]:
    """받을 사업연도 — 보고서 하나가 3년을 담으므로 3년 간격이면 빈 해가 없다."""
    years = list(range(base, FIRST_YEAR - 1, -3))
    if FIRST_YEAR not in years:      # 사다리 끝과 시작 연도 사이의 구멍을 막는다
        years.append(FIRST_YEAR)
    return years


def _deep_frame(key: str, corp: str) -> tuple[pd.DataFrame, int, float]:
    """(깊은 재무 프레임, 호출 횟수, 초). 현행 `_dart_financials_df`의 사다리 확장판."""
    t0 = time.perf_counter()
    calls, reports, base = 0, {}, None
    for y in (_dt.date.today().year - 1, _dt.date.today().year - 2):
        calls += 1
        j = _fetch_report(key, corp, y)
        if j:
            base, reports[y] = y, j
            break
    if base is None:
        return pd.DataFrame(), calls, time.perf_counter() - t0

    fs_div = reports[base].get("_fs", "CFS")
    for y in _ladder(base)[1:]:
        calls += 1
        j = _fetch_report(key, corp, y, fs_div=fs_div)   # 연결/별도 기준을 섞지 않는다
        if j:
            reports[y] = j

    data: dict[int, dict] = {}
    for ry in sorted(reports, reverse=True):             # 최신 보고서 우선(재작성 반영)
        for yr, vals in _parse_report(reports[ry], ry).items():
            slot = data.setdefault(int(yr), {})
            for c, v in vals.items():
                if (c not in slot or pd.isna(slot.get(c))) and not pd.isna(v):
                    slot[c] = v
    df = pd.DataFrame.from_dict(data, orient="index").sort_index() if data else pd.DataFrame()
    if not df.empty:
        mask = pd.Series(False, index=df.index)
        for c in ("revenue", "total_assets", "net_income"):
            if c in df.columns:
                mask = mask | df[c].notna()
        df = df[mask] if mask.any() else df
    return df, calls, time.perf_counter() - t0


def _measure(fin: pd.DataFrame, window: int) -> dict:
    """창 하나에서 ⑤와 EPV의 정상값. 상수를 갈아 끼우고 **실제 함수**를 부른다."""
    val_mod.NORMALIZE_WINDOW = window
    epv_mod.NORMALIZE_WINDOW = window     # import로 묶인 별도 이름이라 따로 바꿔야 한다
    ni, yrs = val_mod._normalized_earnings(fin)
    op, oyrs = epv_mod.normalized_operating_income(fin)
    return {"ni": ni, "ni_years": yrs, "op": op, "op_years": oyrs}


SHIFTS = 3          # 창을 뒤로 미는 횟수. 3이면 '올해·작년·재작년 기준'을 비교한다.


def _stability(fin: pd.DataFrame, window: int) -> float | None:
    """기준 연도를 한 해씩 뒤로 밀며 정상 이익을 재고 그 **변동계수**를 낸다.

    낮을수록 '정상'이라는 이름값을 한다 — 기준이 한 해 움직였다고 값이 크게 바뀌면
    그 값은 사이클 위치를 그대로 물려받고 있다는 뜻이다. 이력이 `window + SHIFTS - 1`년
    미만이면 비교 자체가 창 부족과 섞이므로 재지 않는다(None).
    """
    s = pd.to_numeric(fin.get("net_income"), errors="coerce")
    if s is None or len(s) < window + SHIFTS - 1:
        return None
    vals = []
    for k in range(SHIFTS):
        end = len(s) - k
        win = s.iloc[end - window:end]
        win = win[np.isfinite(win)]
        if len(win) < val_mod.NORMALIZE_MIN_YEARS:
            return None
        vals.append(float(win.mean()))
    m = float(np.mean(vals))
    if not np.isfinite(m) or abs(m) <= 0:
        return None
    return float(np.std(vals) / abs(m))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="표본 종목 수 (시총 5분위 층화)")
    args = ap.parse_args()

    key = get_api_key()
    if not key:
        print("OPENDART_API_KEY가 없다 — 이 진단은 키가 필요하다.")
        return 1
    cmap = get_corp_code_map()

    L = get_kr_listing()
    pool = L[L["is_common"] & (L["Marcap"] > 0)].copy()
    pool["층"] = pd.qcut(pool["Marcap"], 5, labels=["Q1 최소형", "Q2", "Q3", "Q4", "Q5 최대형"])
    per = max(1, args.limit // 5)
    samp = (pool.groupby("층", observed=True)
                .apply(lambda g: g.sample(min(per, len(g)), random_state=11),
                       include_groups=False)
                .reset_index())

    print(f"표본 {len(samp)}종목 · 사다리 {_ladder(_dt.date.today().year - 1)}\n")

    rows, calls_tot, secs_tot = [], 0, 0.0
    warm_secs, cold_secs = [], []
    for r in samp.itertuples():
        code = r.Code
        if code not in cmap.index:
            continue
        deep, calls, secs = _deep_frame(key, cmap.at[code, "corp_code"])
        if deep.empty or "net_income" not in deep.columns:
            continue
        calls_tot += calls
        secs_tot += secs
        cold_secs.append(secs)

        # B. 현행 경로(캐시 있음)를 두 번 불러 캐시가 실제로 공짜인지 본다.
        try:
            t0 = time.perf_counter()
            cur = _dart_financials_df(code)
            _dart_financials_df(code)
            warm_secs.append(time.perf_counter() - t0)
            cur_years = len(cur)
        except Exception:
            cur_years = 0

        m = {w: _measure(deep, w) for w in WINDOWS}
        rows.append({
            "code": code, "name": r.Name, "층": r.층,
            "깊이": len(deep), "현행": cur_years, "호출": calls, "초": secs,
            **{f"ni{w}": m[w]["ni"] for w in WINDOWS},
            **{f"y{w}": m[w]["ni_years"] for w in WINDOWS},
            **{f"op{w}": m[w]["op"] for w in WINDOWS},
            **{f"cv{w}": _stability(deep, w) for w in WINDOWS},
        })

    if not rows:
        print("응답한 종목이 없다 — 키나 네트워크를 확인하라.")
        return 1
    res = pd.DataFrame(rows)
    n = len(res)

    # ── A. 사다리 비용 ────────────────────────────────────────────
    print("A. 사다리 비용 — 3년 간격으로 받으면")
    print(f"    {'종목':<14}{'층':<10}{'깊이':>5}{'현행':>5}{'호출':>5}{'초':>7}")
    print("    " + "─" * 48)
    for r in res.itertuples():
        print(f"    {str(r.name)[:12]:<14}{str(r.층):<10}{r.깊이:>5}{r.현행:>5}"
              f"{r.호출:>5}{r.초:>7.1f}")
    print(f"\n    깊이 중앙 {res['깊이'].median():.0f}년 (현행 중앙 {res['현행'].median():.0f}년) · "
          f"종목당 호출 {calls_tot/n:.1f}회 · {secs_tot/n:.1f}초")
    print("    인계문 경고 1은 '2회 → 12회'였다. 연도마다 부르면 그렇지만 **3년 간격이면**")
    print(f"    보고서 하나가 3년을 담으므로 {calls_tot/n:.1f}회로 같은 깊이가 나온다.")

    # ── B. 캐시 ───────────────────────────────────────────────────
    if warm_secs:
        print(f"\nB. 캐시 — 현행 경로 2회 연속 조회 평균 {np.mean(warm_secs):.2f}초")
        print(f"    깊은 조회 1회 평균 {np.mean(cold_secs):.1f}초. `@file_cache(ttl_hours=24)`라")
        print("    느려지는 것은 **하루 첫 조회 한 번**이고 그 뒤는 parquet에서 읽는다.")

    # ── C. 창 5 대 8 ──────────────────────────────────────────────
    print("\nC. 창 5 대 8 — ⑤ 정규화 이익")
    cov = {}
    for w in WINDOWS:
        ok = res[f"ni{w}"].notna() & (res[f"ni{w}"] > 0)
        cov[w] = float(ok.mean())
        short = int((res[f"y{w}"] < val_mod.NORMALIZE_MIN_YEARS).sum())
        print(f"    창 {w}년: 축이 섬 {ok.sum()}/{n} ({cov[w]:.0%}) · "
              f"평균에 쓴 연수 중앙 {res[f'y{w}'].median():.0f} · 이력 부족 {short}종목")
    lost = res[(res["ni5"] > 0) & ~(res["ni8"] > 0)]
    gained = res[~(res["ni5"] > 0) & (res["ni8"] > 0)]
    print(f"\n    (가) 이력이 짧아 빠진 것: {int((res['y8'] < val_mod.NORMALIZE_MIN_YEARS).sum())}종목"
          " — `NORMALIZE_MIN_YEARS`가 그대로라 창 길이와 무관하다")
    print(f"    (나) 옛 적자가 평균을 음수로 끌어내려 빠진 것: {len(lost)}종목  ← 이쪽이 진짜 대가")
    print(f"    반대로 8년에서 새로 서는 것: {len(gained)}종목")
    if len(lost):
        print("        " + " ".join(f"{r.name}({r.code})" for r in lost.itertuples()))

    print("\n   EPV 정상 영업이익 (같은 상수를 쓴다 — 경고 4)")
    for w in WINDOWS:
        ok = res[f"op{w}"].notna() & (res[f"op{w}"] > 0)
        print(f"    창 {w}년: {ok.sum()}/{n} ({ok.mean():.0%})")

    # ── D. 값의 이동 ──────────────────────────────────────────────
    both = res[(res["ni5"] > 0) & (res["ni8"] > 0)].copy()
    print(f"\nD. 값의 이동 — 두 창에서 모두 서는 {len(both)}종목")
    if len(both):
        both["Δ"] = both["ni8"] / both["ni5"] - 1
        q = both["Δ"].quantile([0.1, 0.25, 0.5, 0.75, 0.9])
        print(f"    정상 이익 변화율 중앙 {q[0.5]:+.1%} · "
              f"4분위 {q[0.25]:+.1%}~{q[0.75]:+.1%} · 10~90분위 {q[0.1]:+.1%}~{q[0.9]:+.1%}")
        big = int((both["Δ"].abs() > 0.10).sum())
        print(f"    10% 넘게 움직인 종목 {big}/{len(both)} ({big/len(both):.0%}) — "
              "안 움직이면 창을 바꿀 이유가 없다.")
        far = both.reindex(both["Δ"].abs().sort_values(ascending=False).index).head(5)
        for r in far.itertuples():
            print(f"      {str(r.name)[:12]:<14}{r.Δ:+8.1%}  "
                  f"({r.y5}년 평균 → {r.y8}년 평균)")

    # ── E. 흔들림 ─────────────────────────────────────────────────
    pair = res[res[[f"cv{w}" for w in WINDOWS]].notna().all(axis=1)]
    print(f"\nE. 흔들림 — 기준 연도를 {SHIFTS}번 뒤로 밀었을 때 정상 이익의 변동계수 "
          f"(둘 다 잴 수 있는 {len(pair)}종목)")
    if len(pair):
        for w in WINDOWS:
            print(f"    창 {w}년: 중앙 {pair[f'cv{w}'].median():.3f} · "
                  f"평균 {pair[f'cv{w}'].mean():.3f} · 3분위 {pair[f'cv{w}'].quantile(0.75):.3f}")
        better = int((pair["cv8"] < pair["cv5"]).sum())
        print(f"    8년이 더 안정적인 종목 {better}/{len(pair)} ({better/len(pair):.0%})")
        print("    낮을수록 좋다 — 기준 연도가 한 해 달라졌다고 크게 바뀌면 '정상'이 아니다.")

        # **평균이 중앙값보다 크게 나쁘면 꼬리가 두꺼워진 것이다.** 누가 다치는지 이름을
        # 대야 한다 — 창을 늘리면 8년 전 회사와 지금 회사가 같은 회사가 아닐 수 있고,
        # 그것이 ADR-0015가 이미 적은 '회계 변경·합병에 취약' 한계와 같은 자리다.
        ratio = (pair["cv8"] / pair["cv5"].replace(0, np.nan)).dropna()
        worse2 = int((ratio > 2).sum())
        better2 = int((ratio < 0.5).sum())
        print(f"\n    흔들림 비(8년÷5년) 중앙 {ratio.median():.2f} — "
              f"2배 넘게 나빠진 종목 {worse2} · 절반 이하로 좋아진 종목 {better2}")
        hurt = pair.reindex(ratio.sort_values(ascending=False).index).head(5)
        print("    가장 크게 나빠진 5곳 (8년 창이 다른 회사를 같은 회사로 본 자리일 수 있다):")
        for r in hurt.itertuples():
            print(f"      {str(r.name)[:12]:<14}{r.cv5:>7.3f} → {r.cv8:>7.3f}   (이력 {r.깊이}년)")
        print("    **이것은 흔들림이지 수익률이 아니다.** A&B의 프리미엄은 작업 B에서만 잰다.")
    else:
        print("    비교 가능한 종목이 없다 — 이력이 창+2년을 넘는 표본이 필요하다.")

    print("\n창을 바꾸기 전에 이 표를 ADR에 옮겨 적어라. 8년이 낫다는 것은 아직 문헌 주장이고,")
    print("여기서 나오는 것은 **그 교환의 크기**다 — 무엇을 잃는지 모르고 바꾸지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
