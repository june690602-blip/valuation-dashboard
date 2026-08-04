"""브라우저가 실제로 받는 JS를 실행해 파이썬 쌍둥이와 대조한다 (#84 · ADR-0019).

    python scripts/check_client_math.py           # 전부
    python scripts/check_client_math.py --list    # 무엇을 대조하는지만 보여준다

## 왜 이 검사가 있나

브라우저는 파이썬을 실행하지 못한다. 슬라이더를 움직일 때마다 서버를 왕복하지 않으려고
몇몇 계산이 JS로 이식돼 있는데, 그러면 같은 개념이 두 곳에서 계산된다. 실제로 한 번
갈렸다 — 업종 중앙값을 브라우저는 자사를 포함해 내고 서버는 빼고 내서 삼성전자 기준
12.77배 vs 11.66배였다(#78). 값이 갈린 것보다 **갈린 줄 아무도 몰랐다는 것**이 문제였다.

## 손으로 옮겨 적은 참조 구현을 두지 않는 이유

파이썬으로 옮겨 적은 참조 구현과 대조하면 "참조 구현과 쌍둥이가 같다"만 지켜진다.
정작 사용자가 받는 `web/assets/finmath.js`가 바뀌면 아무도 모른다. 그래서 이 검사는
**그 파일을 Node로 그대로 require해서 실행한다**(`scripts/client_math_probe.js`).
CI 러너에는 Node가 이미 있다 — `quality.yml`이 프런트 JS 문법 검사에 쓰고 있어
의존성을 새로 들이지 않는다.

## 무엇을 판정하나

1. `finmath.js`가 내보내는 이름과 아래 `SITES` 레지스트리가 일치하는가
   (쌍둥이 등록 없이 함수를 더하면 여기서 막힌다)
2. `DVMath`를 쓰는 자산 파일을 싣는 페이지가 `finmath.js`를 **그보다 먼저** 싣는가
3. UI가 만들 수 있는 입력 격자 전체에서 두 구현의 값이 같은가
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OK, BAD, NA = "[확인]", "[문제]", "[불가]"
_tally = {OK: 0, BAD: 0, NA: 0}

FINMATH = ROOT / "web" / "assets" / "finmath.js"
PROBE = ROOT / "scripts" / "client_math_probe.js"
PAGES = ["home.html", "stock.html", "guide.html", "test.html", "bond.html",
         "portfolio.html", "admin.html"]

# 두 구현이 같다고 볼 상대오차. 같은 연산을 같은 순서로 하므로 원래는 완전히 일치해야 하고,
# 마지막 자리 차이만 허용한다. (이 값을 올려야 통과한다면 그것은 수식이 갈렸다는 뜻이다.)
REL_TOL = 1e-12


def say(verdict: str, title: str, detail: str = "") -> None:
    _tally[verdict] = _tally.get(verdict, 0) + 1
    print(f"  {verdict} {title}")
    for line in (detail or "").splitlines():
        if line.strip():
            print(f"         {line}")


def head(title: str) -> None:
    print(f"\n{title}")
    print("─" * 76)


# ── 대조 격자 ────────────────────────────────────────────────────────
# 입력 범위는 상상이 아니라 **UI가 실제로 만들 수 있는 값**에서 가져온다.
#   채권   bond.html — YTM 0.1~20% · 쿠폰 0~20% · 잔존만기 0.5~50년(0.5 단위) · 연 1/2회
#   시나리오 stock.js — EPS 조정 -40~+40% · 멀티플 조정 -30~+30% (둘 다 5%p 단위)
BOND_COUPONS = (0.0, 0.0125, 0.03, 0.05, 0.08, 0.20)
BOND_YTMS = (0.001, 0.015, 0.04, 0.075, 0.12, 0.20)
BOND_YEARS = (0.5, 1.0, 2.5, 3.0, 5.0, 7.5, 10.0, 20.0, 30.0, 50.0)
BOND_FREQS = (1, 2)


def bond_grid() -> list[list]:
    return [[100.0, c, y, t, f]
            for c in BOND_COUPONS for y in BOND_YTMS for t in BOND_YEARS for f in BOND_FREQS]


def tangency_grid() -> list[list]:
    # A<=0 · σm<=0 은 화면이 걸러 주지만, 두 구현이 같은 방식으로 걸러야 한다.
    return [[er, rf, sig, a]
            for er in (0.05, 0.08, 0.11) for rf in (0.0, 0.025, 0.045)
            for sig in (0.0, 0.09, 0.15, 0.28) for a in (-1.0, 0.0, 1.5, 3.0, 6.0)]


def scenario_grid() -> list[list]:
    deltas = [round(d / 100, 2) for d in range(-40, 45, 5)]
    adjs = [round(a / 100, 2) for a in range(-30, 35, 5)]
    return [[eps, d, m, a]
            for eps in (0.37, 1234.0, 5432.1, 1.0e6)
            for d in deltas for m in (5.0, 12.34, 22.7) for a in adjs]


def peer_grid() -> list[list]:
    """피어 점 묶음 — 크기·홀짝·결측·자사 위치를 고루 섞는다.

    난수를 쓰지 않는다. 검사가 돌 때마다 다른 표본을 보면 '어제는 통과했는데'가 생긴다.
    """
    sets, base = [], [8.1, 12.4, 3.7, 19.0, 15.5, 6.2, 24.8, 11.1, 9.9, 30.2, 2.5, 17.6]
    for n in range(0, 13):                      # 0~12개 — 3개 미만(중앙값 없음) 구간 포함
        for self_at in (None, 0, n - 1):        # 자사가 없을 때 · 맨 앞 · 맨 뒤
            if self_at is not None and not (0 <= self_at < n):
                continue
            pts = []
            for i in range(n):
                per = base[i % len(base)] + i * 0.13
                roe = None if i % 5 == 4 else (base[(i + 3) % len(base)] - 6.0)  # 결측 섞기
                pts.append({"per": per, "roe": roe, "self": i == self_at})
            sets.append([pts, "per"])
            sets.append([pts, "roe"])
    return sets


# ── 레지스트리 — finmath.js의 수출 하나하나가 여기 등록돼 있어야 한다 ─────
def _py_bond():
    from src.analysis import bond_math as bm
    return bm


def _cashflow_pv_py(face, coupon, ytm, years, freq):
    k, pv = _py_bond()._cashflow_pv(face, coupon, ytm, years, freq)
    return {"k": [float(x) for x in k], "pv": [float(x) for x in pv], "n": len(k)}


def _tangency_py(er_m, rf, sigma_m, a):
    from src.analysis.risk_profile import tangency_point
    t = tangency_point(er_m, rf, sigma_m, a)
    # 파이썬은 utility·sharpe·mrs도 돌려주지만 화면이 쓰지 않아 이식하지 않았다 —
    # 대조는 프런트가 실제로 읽는 세 값만 한다.
    return {"y": t["y_star"], "sigma_p": t["sigma_p"], "er_p": t["er_p"]}


def _peer_median_py(points, col):
    import pandas as pd
    from src.analysis.scoring import peer_median
    if not points:
        return None
    df = pd.DataFrame(points).rename(columns={"self": "is_self"})
    df["is_self"] = df["is_self"].astype(bool)
    return peer_median(df, col)


SITES = [
    {"site": "채권 — 기간별 현금흐름 PV", "js": "cashflowPV",
     "twin": "src/analysis/bond_math.py::_cashflow_pv",
     "grid": bond_grid, "py": _cashflow_pv_py},
    {"site": "채권 — 가격", "js": "bondPrice",
     "twin": "src/analysis/bond_math.py::bond_price",
     "grid": bond_grid, "py": lambda *a: _py_bond().bond_price(*a)},
    {"site": "채권 — 듀레이션·볼록성·DV01", "js": "bondMetrics",
     "twin": "src/analysis/bond_math.py::bond_metrics",
     "grid": bond_grid, "py": lambda *a: _py_bond().bond_metrics(*a)},
    {"site": "채권 — 금리 시나리오 6행", "js": "rateScenarios",
     "twin": "src/analysis/bond_math.py::rate_scenarios",
     "grid": bond_grid, "py": lambda *a: _py_bond().rate_scenarios(*a)},
    {"site": "포트폴리오 — 접점(머튼 비율)", "js": "tangency",
     "twin": "src/analysis/risk_profile.py::tangency_point",
     "grid": tangency_grid, "py": _tangency_py},
    {"site": "주식 — 시나리오 케이스 가격", "js": "scenarioCasePrice",
     "twin": "src/analysis/scenario.py::case_price",
     "grid": scenario_grid,
     "py": lambda *a: __import__("src.analysis.scenario", fromlist=["x"]).case_price(*a)},
    {"site": "주식 — 업종 중앙값(자사 제외)", "js": "peerMedian",
     "twin": "src/analysis/scoring.py::peer_median",
     "grid": peer_grid, "py": _peer_median_py},
]


# ── A. 수출 ↔ 레지스트리 ─────────────────────────────────────────────
def exported_names() -> list[str]:
    """finmath.js가 내보내는 이름 — 파일 **끝** return 블록의 `이름:` 을 읽는다.

    함수마다 객체를 돌려주므로 return 블록은 여럿이다. 수출 블록은 언제나 마지막 것이다.
    """
    src = FINMATH.read_text(encoding="utf-8")
    blocks = re.findall(r"return\s*\{(.*?)\};", src, re.S)
    return re.findall(r"([A-Za-z_]\w*)\s*:", blocks[-1]) if blocks else []


def check_registry() -> None:
    head("A. 수출 ↔ 레지스트리 — 쌍둥이 없이 들어온 수식이 있는가")
    got, want = set(exported_names()), {s["js"] for s in SITES}
    if not got:
        say(BAD, "finmath.js의 return 블록을 읽지 못했다", "파일 끝 형식이 바뀌었는지 확인할 것")
        return
    unregistered, missing = sorted(got - want), sorted(want - got)
    if unregistered:
        say(BAD, f"레지스트리에 없는 수출 {len(unregistered)}개", " · ".join(unregistered) +
            "\nfinmath.js에 함수를 더했으면 파이썬 쌍둥이와 격자를 SITES에 함께 등록해야 한다.\n"
            "등록되지 않은 수식은 갈려도 아무도 모른다 — 이 파일이 있는 이유가 그것이다.")
    if missing:
        say(BAD, f"등록됐는데 finmath.js에 없는 이름 {len(missing)}개", " · ".join(missing) +
            "\n이름을 바꿨거나 지웠다면 레지스트리도 함께 갱신할 것.")
    if not unregistered and not missing:
        say(OK, f"수출 {len(got)}개가 모두 파이썬 쌍둥이와 함께 등록돼 있음",
            "\n".join(f"{s['js']:<20}↔ {s['twin']}" for s in SITES))


# ── B. 페이지가 finmath.js를 싣는가 ──────────────────────────────────
def check_page_wiring() -> None:
    head("B. 적재 순서 — DVMath를 쓰는 화면이 finmath.js를 먼저 싣는가")
    users = sorted(p.name for p in (ROOT / "web" / "assets").glob("*.js")
                   if p.name != "finmath.js" and "DVMath" in p.read_text(encoding="utf-8"))
    problems = []
    for page in PAGES:
        html = (ROOT / "web" / page).read_text(encoding="utf-8")
        order = re.findall(r'<script[^>]*src="assets/([^"]+)"', html)
        need = [u for u in users if u in order]
        if not need:
            continue
        if "finmath.js" not in order:
            problems.append(f"{page}: {' · '.join(need)}가 DVMath를 쓰는데 finmath.js를 싣지 않는다")
            continue
        at = order.index("finmath.js")
        late = [u for u in need if order.index(u) < at]
        if late:
            problems.append(f"{page}: finmath.js가 {' · '.join(late)}보다 늦게 실린다")
    if problems:
        say(BAD, f"적재가 어긋난 페이지 {len(problems)}개", "\n".join(problems) +
            "\n이 어긋남은 파이썬 검사로도 문법 검사로도 잡히지 않는다 — 화면을 열어야 보인다.\n"
            "DVMath가 undefined면 그 화면의 스크립트가 통째로 죽는다.")
    else:
        say(OK, f"DVMath를 쓰는 자산 {len(users)}개가 모든 페이지에서 뒤에 실린다",
            " · ".join(users))


# ── C. 전 격자 대조 ──────────────────────────────────────────────────
def run_probe(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError("node")
    p = subprocess.run([node, str(PROBE)], input=json.dumps(cases), capture_output=True,
                       text=True, encoding="utf-8", cwd=str(ROOT))
    if p.returncode != 0:
        raise RuntimeError(f"node 실행 실패(코드 {p.returncode})\n{p.stderr.strip()}")
    return json.loads(p.stdout)


def diff(js, py, path: str = "") -> tuple[str, float] | None:
    """두 값을 재귀 비교 — (어긋난 자리 설명, 상대오차) 또는 None."""
    if py is None or js is None:
        return None if (py is None and js is None) else (f"{path}: JS={js!r} · PY={py!r}", float("inf"))
    if isinstance(py, dict):
        for k in py:
            got = diff(js.get(k) if isinstance(js, dict) else None, py[k], f"{path}.{k}")
            if got:
                return got
        return None
    if isinstance(py, (list, tuple)):
        if not isinstance(js, list) or len(js) != len(py):
            n = len(js) if isinstance(js, list) else "―"
            return (f"{path}: 길이 JS={n} · PY={len(py)}", float("inf"))
        for i, v in enumerate(py):
            got = diff(js[i], v, f"{path}[{i}]")
            if got:
                return got
        return None
    a, b = float(js), float(py)
    scale = max(abs(a), abs(b), 1e-12)
    rel = abs(a - b) / scale
    return (f"{path}: JS={a!r} · PY={b!r}", rel) if rel > REL_TOL else None


def check_parity() -> None:
    head("C. 전 격자 대조 — 같은 입력에 같은 값을 내는가")
    cases, index = [], []
    for s in SITES:
        for args in s["grid"]():
            index.append((s, args))
            cases.append({"fn": s["js"], "args": args})
    try:
        results = run_probe(cases)
    except FileNotFoundError:
        say(NA, "Node가 없어 대조하지 못했다",
            "브라우저 구현을 실행할 수단이 없으면 두 구현이 같은지 판정할 수 없다.\n"
            "CI 러너에는 Node가 있다(quality.yml의 프런트 문법 검사가 이미 쓴다).")
        return
    except RuntimeError as e:
        say(BAD, "브라우저 구현을 실행하지 못했다", str(e))
        return

    SHOWN = 4                                   # 앞의 몇 건이면 원인은 충분히 보인다
    per_site: dict[str, dict] = {s["site"]: {"n": 0, "worst": 0.0, "off": 0, "bad": []}
                                 for s in SITES}
    for (s, args), got in zip(index, results):
        st = per_site[s["site"]]
        st["n"] += 1
        if "error" in got:
            st["off"] += 1
            if len(st["bad"]) < SHOWN:
                st["bad"].append(f"{args} → JS 오류: {got['error']}")
            continue
        try:
            expected = s["py"](*args)
        except Exception as e:                              # 파이썬만 거부하는 입력도 어긋남이다
            st["off"] += 1
            if len(st["bad"]) < SHOWN:
                st["bad"].append(f"{args} → PY 오류: {type(e).__name__}: {e}")
            continue
        d = diff(got["value"], expected)
        if d:
            where, rel = d
            st["off"] += 1
            if rel != float("inf"):
                st["worst"] = max(st["worst"], rel)
            if len(st["bad"]) < SHOWN:
                st["bad"].append(f"{args} → {where}  (상대오차 {rel:.2e})")

    broken = [s for s in SITES if per_site[s["site"]]["off"]]
    for s in broken:
        st = per_site[s["site"]]
        more = f"\n… 그 밖에 {st['off'] - len(st['bad'])}건" if st["off"] > len(st["bad"]) else ""
        say(BAD, f"{s['site']} — 격자 {st['n']}건 중 {st['off']}건이 어긋남",
            f"쌍둥이 {s['twin']}\n" + "\n".join(st["bad"]) + more)
    if not broken:
        worst = max(per_site[s["site"]]["worst"] for s in SITES)
        say(OK, f"등록된 {len(SITES)}곳이 격자 {len(cases)}건에서 모두 일치",
            "\n".join(f"{s['site']:<28}{per_site[s['site']]['n']:>5}건" for s in SITES) +
            f"\n최대 상대오차 {worst:.2e} (허용 {REL_TOL:.0e}) — 손으로 옮긴 참조 구현이 아니라 "
            f"브라우저가 받는\nfinmath.js를 그대로 실행해 얻은 값이다.")


def main() -> int:
    ap = argparse.ArgumentParser(description="브라우저 구현 ↔ 파이썬 쌍둥이 대조 (#84)")
    ap.add_argument("--list", action="store_true", help="대조 대상만 보여준다")
    args = ap.parse_args()

    print("두 언어에 사는 수식 대조 — web/assets/finmath.js ↔ src/analysis/*")
    if args.list:
        for s in SITES:
            print(f"  {s['js']:<20}↔ {s['twin']:<44}격자 {len(s['grid']()):>5}건")
        return 0

    check_registry()
    check_page_wiring()
    check_parity()

    print(f"\n{'─' * 76}")
    print(f"{OK} {_tally[OK]}   {BAD} {_tally[BAD]}   {NA} {_tally[NA]}")
    if _tally[BAD]:
        print("\n두 구현이 어긋나면 Streamlit과 Meridian 웹이 같은 종목에 다른 숫자를 보여준다.")
    return 1 if _tally[BAD] else 0


if __name__ == "__main__":
    sys.exit(main())
