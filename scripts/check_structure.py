"""R5(코드 구조) 검증: 남이 열어서 읽을 수 있는가.

값이 맞는지(R1)·가정이 타당한지(R2)·문장이 정확한지(R3)·생김새가 고른지(R4)는 보지 않는다.
오직 **코드를 여는 사람이 무엇을 만나는가**만 본다 — 파일 크기·중복·경계·죽은 코드.

    python scripts/check_structure.py            # 전체
    python scripts/check_structure.py --list     # 레지스트리(예산·헬퍼·경계)만 출력

네트워크·API 키가 필요 없다. 구조는 파일을 읽으면 확인되기 때문이다.

각 점검은 [확인] / [문제] / [불가] 중 하나로 결론 낸다.
- 확인: 규약과 일치함을 확인
- 문제: 어긋남을 발견 (조서 1·2번 바구니 후보)
- 불가: 정적으로는 판정할 수 없음 (사람이 읽어야 하는 것 / 3번 바구니 후보)

## 이 스크립트가 지키는 규약

**하나. 파일 크기는 재되, 관문이 아니다.**
`FILE_SIZES`가 파일마다 '이 파일이 무엇을 담는가'를 적어 두고, 실제 줄 수를 함께 출력한다.
예전에는 파일마다 줄 수 상한(`SIZE_BUDGET`)을 두고 넘으면 [문제]로 떨어뜨렸다 —
그 상한을 없앴다. 줄 수는 '읽기 어려움'의 대리 지표일 뿐이라, 상한을 지키려고 경계가
어색한 자리에서 파일을 자르면 파일 수만 늘고 의존 방향이 꼬인다(아래 I의 두 번째 [불가]가
바로 그 이야기다). 크기는 계속 **보여주되** 판정하지 않는다. 분할은 읽는 사람이 실제로
헤맬 때 그 이유를 근거로 한다.

**둘. 한 헬퍼는 한 벌이다.**
`esc`·`el`·`$`가 파일마다 복사돼 있으면, 한 곳을 고쳐도 나머지가 따라오지 않는다.
`SHARED_HELPERS`가 그 못이다 — 이름마다 '몇 벌까지 허용하는가'를 등록하고, 실제 벌 수가
그보다 많으면 [문제]다. 벌끼리 본문이 **다르면** 같은 이름이 다른 뜻이라 더 무겁게 본다.

**셋. 계산은 `src/analysis`에 산다.**
CLAUDE.md의 규칙이다. 그런데 실제 실행 진입점은 Meridian 웹(`server.py`+`web/`)이고,
브라우저는 파이썬을 실행하지 못한다. 그래서 몇몇 계산은 JS로 이식됐다 — `CLIENT_MATH`가
그 자리를 전수 등록한다. 등록의 핵심은 **파이썬 쌍둥이가 있는가**와 **CI가 어느 쪽을
돌리는가**다. 쌍둥이가 있는데 CI가 파이썬만 돌리면, **테스트되는 구현과 사용자가 보는
구현이 다르다**.

**넷. 인라인 스타일은 예산 안에 산다.**
값이 CSS가 아니라 JS 문자열 안에 있으면 R4가 만든 토큰이 화면에 닿지 않는다.
`INLINE_BUDGET`도 내려가기만 한다.

**다섯. 검증 스크립트는 CI에 걸려 있어야 한다.**
R1~R5가 만든 검증 스크립트는 사람이 기억해서 돌리는 것이 아니라 PR마다 자동으로 돌아야
회귀를 막는다. 네트워크가 필요 없는 스크립트가 `quality.yml`에 없으면 [문제]다.
덤으로 **실패 시 0이 아닌 값을 반환하는가**(관문인가, 보고서인가)도 함께 본다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

OK, BAD, NA = "[확인]", "[문제]", "[불가]"
_tally = {OK: 0, BAD: 0, NA: 0}

# 지금 열려 있는 문제의 수 — 전부 R5 조서 2번 바구니의 것이고 이슈로 등록해 순서대로 닫는다.
#   1 CSS의 67%가 페이지 안        (#74의 선행 조건)
#   2 stock.js 인라인 밀도          (#82가 타일 6벌을 tiles()로 모았지만 형제 대비 2배는 남았다)
#   3 serialize.py의 계산 3자리    (F — 시장 σ 추정·벤치마크 리샘플. #84와 별건이라 남긴다)
# 이 수보다 늘어날 때만 실패한다. **이슈를 닫으면 이 값을 함께 내린다.**
#   7 → 6: #83이 공용 헬퍼를 common.js 한 벌로 모았다(esc 6벌·$ 5벌·el 3벌·wireSeg 3벌·tiles 3벌).
#   6 → 5: 파일 크기 예산(㉮ stock.js 분할)을 관문에서 뺐다. 크기는 A에서 표로만 보여준다.
#   5 → 3: #84 — 이식된 수식을 finmath.js 한 벌로 모으고, 브라우저가 받는 그 파일을 Node로
#          실행해 파이썬 쌍둥이와 전 격자 대조하게 했다(ADR-0019). '두 언어에 4곳'과
#          'bond_math를 웹이 안 씀'이 함께 닫힌다 — 쌍둥이가 실행 가능한 명세가 됐다.
KNOWN_OPEN = 3

WEB_JS = ["common.js", "finmath.js", "stock.js", "stock-price-chart.js", "portfolio.js",
          "bond.js", "test.js", "feedback.js", "analytics.js"]
PAGES = ["home.html", "stock.html", "guide.html", "test.html", "bond.html",
         "portfolio.html", "admin.html"]


def say(verdict: str, title: str, detail: str = "") -> None:
    _tally[verdict] = _tally.get(verdict, 0) + 1
    print(f"  {verdict} {title}")
    for line in (detail or "").splitlines():
        if line.strip():
            print(f"         {line}")


def head(title: str) -> None:
    print(f"\n{title}")
    print("─" * 76)


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def nlines(rel: str) -> int:
    return len(read(rel).splitlines())


# ── 레지스트리 ───────────────────────────────────────────────────────
# 큰 파일들이 각각 무엇을 담는가. **상한은 없다** — 예전에는 여기에 줄 수 예산을 적고
# 넘으면 [문제]로 떨어뜨렸지만(stock.js 2,001줄 규칙), 그 관문을 없앴다. 줄 수는 계속
# 출력해서 "지금 어디가 커지고 있나"는 보이게 하되, 그 수 자체로 실패시키지 않는다.
FILE_SIZES = {
    "web/assets/common.js": "공용 헬퍼 한 벌(#83). 여기 밖에 사본이 생기면 B 검사가 잡는다",
    "web/assets/stock.js": "8탭 렌더 + ETF 뷰 + SVG 차트가 한 IIFE에 산다",
    "web/assets/stock-price-chart.js": "주가 캔버스 차트. (container, D, state, fmt)만 받는다",
    "web/assets/portfolio.js": "차트 3종 + 바스켓 관리",
    "web/assets/bond.js": "채권 수학 이식 + 차트 3종",
    "web/assets/test.js": "성향진단 위저드",
    "web/assets/feedback.js": "전 페이지 공용 위젯",
    "web/assets/analytics.js": "추적 스니펫 주입",
    "src/web/serialize.py": "8탭 + ETF + 채권 + 포트폴리오 직렬화가 한 파일",
    "server.py": "정적 서빙 + API 라우팅",
    "src/ui/pages/stock.py": "Streamlit 8탭 렌더러",
    "src/ui/charts.py": "Plotly 차트 모음",
}

# 공용이어야 하는 헬퍼 — (허용 벌 수, 정본이 있어야 할 곳, 왜)
# 지금 값은 '현실'이 아니라 '규약'이다. 1을 적어 두었으면 지금 3벌이어도 1을 적는다 —
# 그래야 [문제]로 떨어지고 조서에 남는다.
SHARED_HELPERS = {
    "$": (1, "web/assets/common.js", "getElementById 줄임말. 한때 5벌이었다(인라인 스크립트 포함)"),
    "esc": (1, "web/assets/common.js", "HTML 이스케이프. 벌마다 규칙이 다르면 같은 이름이 다른 안전성을 뜻한다"),
    "el": (1, "web/assets/common.js", "SVG/HTML 문자열 빌더. var() 폴백 처리 같은 판단이 벌마다 갈린다"),
    "kebab": (1, "web/assets/common.js", "el()의 부속"),
    "styleStr": (1, "web/assets/common.js", "el()의 부속"),
    "niceStep": (1, "web/assets/common.js", "축 눈금 간격 — 차트가 같은 눈금을 써야 한다"),
    "wireSeg": (1, "web/assets/common.js", "세그먼트 컨트롤 배선. aria-pressed 관리가 벌마다 갈린다"),
    "tiles": (1, "web/assets/common.js", "지표 타일 스트립 — 이미 두 파일이 같은 본문을 쓴다"),
    "loadBasket": (1, "web/assets/common.js", "localStorage 'invportfolio' 읽기 — 세 화면이 같은 키를 쓴다"),
    "saveBasket": (1, "web/assets/common.js", "localStorage 'invportfolio' 쓰기"),
}

# 인라인 스타일 예산 — 값이 JS 문자열 안에 있으면 R4의 토큰이 닿지 않는다(R4 한계 3).
# 내려가기만 한다.
INLINE_BUDGET = {
    "web/assets/common.js": 2,
    # #82가 타일 6벌을 tiles()로 바꾸며 19건이 빠졌다(343 → 324). 이 PR이 공식·출처 접기와
    # 병기 블록을 CSS 클래스로 올리며 다시 내려갔다: 324 → 308.
    # HANDOFF-AXES 1단계가 근거 넷(기대는 곳·오차·민감도·재정규화)을 접힘 안으로 옮기며
    # 그 상자들의 인라인 스타일이 통째로 빠졌다: 308 → 303. 이어서 요약 표의 '근거' 칸이
    # 통째로 접힘이 되며 그 칸의 글자 스타일도 CSS로 올라갔다: 303 → 301.
    "web/assets/stock.js": 301,
    "web/assets/stock-price-chart.js": 1,
    "web/assets/portfolio.js": 30,
    "web/assets/bond.js": 19,
    "web/assets/test.js": 17,
    "web/assets/feedback.js": 1,
    "web/assets/analytics.js": 0,
}

# 브라우저에서 계산하는 금융 수식 — (JS 자리, 파이썬 쌍둥이, CI가 돌리는 것, 왜 JS에 있나)
# python_twin이 None이면 JS가 유일한 구현이다.
# `parity`는 **양쪽을 함께 돌리는** 검사다. 파이썬 쪽만 도는 테스트는 여기에 적지 않는다 —
# 그것은 "쌍둥이가 자기 자신과 같다"만 지키고 사용자가 받는 구현에 대해서는 아무 말도 않는다.
CLIENT_MATH = {
    "finmath.js:채권 (cashflowPV·bondPrice·bondMetrics·rateScenarios)": {
        "js": "web/assets/finmath.js",
        "python_twin": "src/analysis/bond_math.py",
        "ci_runs": "scripts/check_client_math.py (Node로 JS 실행 + 파이썬) · scripts/check_bond.py",
        "parity": "scripts/check_client_math.py",
        "why": "슬라이더를 움직일 때마다 왕복하지 않으려고 이식. serialize.bond_data() 독스트링이 "
               "'시나리오 계산은 프런트(JS)에서 수행'이라고 밝혀 둔 의도된 예외다",
    },
    "finmath.js:tangency": {
        "js": "web/assets/finmath.js",
        "python_twin": "src/analysis/risk_profile.py::tangency_point",
        "ci_runs": "scripts/check_client_math.py (양쪽) · tests/test_risk_profile.py",
        "parity": "scripts/check_client_math.py",
        "why": "서버가 CML 가정(rf·er_m·sigma_m)만 보내고 접점은 프런트가 그린다",
    },
    "finmath.js:scenarioCasePrice": {
        "js": "web/assets/finmath.js",
        "python_twin": "src/analysis/scenario.py::case_price",
        "ci_runs": "scripts/check_client_math.py (양쪽) · tests/test_forward_scenario.py",
        "parity": "scripts/check_client_math.py",
        "why": "EPS·멀티플 슬라이더가 움직일 때 케이스 가격을 즉시 다시 계산한다",
    },
    "finmath.js:peerMedian": {
        "js": "web/assets/finmath.js",
        "python_twin": "src/analysis/scoring.py::peer_median",
        "ci_runs": "scripts/check_client_math.py (양쪽) · tests/test_peer_sample_robustness.py",
        "parity": "scripts/check_client_math.py",
        "why": "사분면 십자선용 업종 중앙값. **표본 정의가 서버와 갈렸던 자리다** — 브라우저는 "
               "자사를 포함해 계산하고 서버의 peer_median은 제외했다(삼성전자: 12.77× vs 11.66×). "
               "같은 개념을 두 곳에서 계산하면 조용히 갈라진다는 것의 실제 사례",
    },
    "stock.js:backtestScatter(OLS)": {
        "js": "web/assets/stock.js",
        "python_twin": None,
        "ci_runs": None,
        "parity": None,
        "why": "산점도 추세선. 화면 장식이라 서버가 계수를 보내지 않는다",
    },
}

# 배수 비교표를 그리는 자리 — 「현재」와 「업종 중앙값」이 **같은 자**로 잰 값이어야 한다
# (ADR-0020 · #86). 한 회사의 같은 배수가 두 출처로 존재했고(우리 계산 vs 제공자 공시),
# 그 둘을 나란히 놓고 비교해서 5종목 중 2종목의 판정이 정의 차이만으로 뒤집혔다.
# 정의를 고르는 자리는 `scoring.screen_multiple` 하나여야 한다.
SCREEN_MULTIPLE_SITES = {
    "src/web/serialize.py": "Meridian 웹 — _multiples() · tiles",
    "src/ui/pages/stock.py": "Streamlit — 배수 비교표 · 헤더 지표",
}
# 옛 방식: indicators 값을 곧장 비교에 넣던 자리. 되살아나면 잡는다.
SCREEN_MULTIPLE_BANNED = r"cur\s*=\s*ind\.valuation\.get\("

# 네트워크·키 없이 도는 검증 스크립트 — PR마다 자동으로 돌아야 회귀를 막는다.
STATIC_CHECKS = {
    "scripts/check_design.py": "R4 디자인 일관성 — 토큰·셸·대비",
    "scripts/check_screen_language.py": "R3 화면 언어 — 판정 어휘·층위",
    "scripts/check_structure.py": "R5 코드 구조 — 이 파일",
    "scripts/check_client_math.py": "#84 두 언어 수식 대조 — 브라우저 구현을 Node로 실행",
}

# 계산이 새어 나오면 안 되는 층 — 직렬화는 값을 옮기는 일이지 만드는 일이 아니다.
ANALYSIS_SMELLS = [
    (r"\.std\(\)", "표준편차 계산"),
    (r"np\.sqrt\(", "제곱근 — 연율화·변동성 계산의 표식"),
    (r"pct_change\(", "수익률 계산"),
    (r"\.resample\(", "리샘플링 — 시계열 가공"),
]


# ── A. 파일 크기 (참고 표 · 판정하지 않음) ───────────────────────────
def report_file_size() -> None:
    """줄 수를 보여만 준다. [확인]/[문제]를 찍지 않으므로 집계에도 들어가지 않는다.

    상한을 두던 시절에는 주석 한 줄만 늘어도 CI가 빨개졌고, 그걸 피하려고 예산을 올려
    적는 일이 반복됐다(1,990 → 2,001). 상한이 실제로 지킨 것은 '읽기 쉬움'이 아니라
    '숫자'였다. 크기는 신호로 남기고 판정은 사람이 한다.
    """
    head("A. 파일 크기 — 참고 (관문 아님)")
    rows = sorted(FILE_SIZES.items(), key=lambda kv: -nlines(kv[0]))
    for rel, why in rows:
        print(f"  {rel:<34}{nlines(rel):>6}줄   {why}")
    print("\n         상한은 두지 않는다. 분할은 줄 수가 아니라 '읽는 사람이 어디서 헤매는가'로 정한다.")


# ── B. 중복 헬퍼 ─────────────────────────────────────────────────────
def _extract_fn(src: str, name: str) -> tuple[str, str] | None:
    """function NAME( ... ) 본문을 중괄호 짝으로 잘라 (주석 포함, 주석 제거) 두 벌로 돌려준다.

    코드가 같은데 주석만 다른 경우와 로직이 갈린 경우는 무게가 다르다 — 그래서 나눠 잰다.
    """
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", src)
    if not m:
        return None
    i = src.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    raw = src[i:j + 1]
    sig = src[m.end():src.index(")", m.end())]
    stripped = re.sub(r"//[^\n]*", "", raw)
    return _norm(raw), _alpha_rename(_norm(stripped), sig)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _alpha_rename(body: str, outer_params: str) -> str:
    """인자 이름을 자리 이름으로 바꿔, 이름만 다른 같은 로직이 다르게 보이지 않게 한다.

    같은 헬퍼를 파일마다 다른 인자 이름으로 적어 두는 일이 흔하다(test.js는 value·char,
    나머지는 s·m). 이름 차이는 '로직이 갈렸다'가 아니므로 비교 전에 지운다.
    """
    names = [p.strip() for p in outer_params.split(",") if p.strip()]
    for inner in re.findall(r"function\s*\(([^)]*)\)", body):
        names += [p.strip() for p in inner.split(",") if p.strip()]
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    for idx, n in enumerate(seen):
        body = re.sub(r"\b" + re.escape(n) + r"\b", f"_p{idx}", body)
    return body


def _inline_scripts(html: str) -> str:
    """페이지 안 <script>…</script> 본문만 이어붙인다(src= 태그는 본문이 없다)."""
    return "\n".join(m.group(1) for m in
                     re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S))


def check_duplicate_helpers() -> None:
    head("B. 중복 헬퍼 — 한 헬퍼가 한 벌인가")
    # 자산 파일뿐 아니라 **페이지 안 인라인 스크립트**도 센다.
    # 처음에는 web/assets/*.js만 훑었는데, 그 사이 home.html·admin.html의 인라인
    # 스크립트에 esc·$ 사본이 각각 살아 있었다(#83에서 발견). 세는 범위가 좁으면
    # 레지스트리가 "한 벌"이라고 말해도 실제로는 여러 벌이다.
    sources = {f: read("web/assets/" + f) for f in WEB_JS}
    for page in PAGES:
        body = _inline_scripts(read("web/" + page))
        if body.strip():
            sources[page + " (인라인)"] = body
    rows = []
    for name, (allowed, home, why) in SHARED_HELPERS.items():
        raws, codes = {}, {}
        for f, src in sources.items():
            got = _extract_fn(src, name)
            if got is not None:
                raws[f], codes[f] = got
        # 설명이 아예 없는 벌 — 주석 문구가 벌마다 다른 것은 문제가 아니고,
        # 어떤 벌에는 설명이 있고 어떤 벌에는 없는 것이 문제다.
        undocumented = [f for f, raw in raws.items() if "//" not in raw]
        rows.append({"name": name, "files": list(raws), "copies": len(raws),
                     "code_kinds": len(set(codes.values())),
                     "undocumented": undocumented,
                     "allowed": allowed, "home": home, "why": why})

    dup = [r for r in rows if r["copies"] > r["allowed"]]
    if dup:
        detail = []
        for r in dup:
            tag = ("로직이 갈림" if r["code_kinds"] > 1
                   else "로직 동일·설명 없는 벌 있음" if r["undocumented"] else "로직 동일")
            detail.append(f"{r['name']}: {r['copies']}벌 ({tag}) — {' · '.join(r['files'])}")
            detail.append(f"    → {r['home']}로 올려야 함. {r['why']}")
        say(BAD, f"허용 벌 수를 넘은 헬퍼 {len(dup)}개", "\n".join(detail))
    else:
        say(OK, f"공용 헬퍼 {len(rows)}개가 모두 한 벌")

    # 로직이 갈린 벌이 가장 무겁다 — 같은 이름이 다른 동작을 한다.
    split = [r for r in rows if r["code_kinds"] > 1]
    if split:
        detail = [f"{r['name']}: {r['copies']}벌 중 서로 다른 로직 {r['code_kinds']}종 — "
                  f"{' · '.join(r['files'])}" for r in split]
        say(BAD, f"같은 이름인데 로직이 다른 헬퍼 {len(split)}개",
            "\n".join(detail) + "\n호출하는 쪽은 이름만 보고 같은 동작을 기대한다.")
    else:
        say(OK, "중복된 헬퍼끼리 로직이 어긋난 곳은 없음")

    # 어떤 벌에는 설명이 있고 어떤 벌에는 없는 경우 — 그 코드가 왜 그런지가 한쪽에만 남는다.
    # (문구가 벌마다 다른 것은 따지지 않는다. 있는가 없는가만 본다.)
    half_doc = [r for r in rows if r["copies"] > 1 and r["undocumented"]
                and len(r["undocumented"]) < r["copies"]]
    if half_doc:
        detail = [f"{r['name']}: 설명 없는 벌 — {' · '.join(r['undocumented'])} "
                  f"(전체 {r['copies']}벌)" for r in half_doc]
        say(BAD, f"복사된 벌 중 설명이 빠진 헬퍼 {len(half_doc)}개",
            "\n".join(detail) +
            "\n복사할 때 코드는 따라갔지만 이유는 따라가지 않았다 — 나중에 그 벌을 고치는 사람은\n"
            "그 처리가 왜 있는지 모른다.")
    else:
        say(OK, "복사된 헬퍼에서 설명만 빠진 벌은 없음")


# ── C. CSS의 거처 ────────────────────────────────────────────────────
def _style_block_lines(html: str) -> int:
    n = 0
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.S):
        n += len(m.group(1).splitlines())
    return n


def check_css_home() -> None:
    head("C. CSS의 거처 — 공용 파일에 사는가, 페이지마다 사는가")
    shared = nlines("web/assets/meridian.css")
    per_page = {p: _style_block_lines(read("web/" + p)) for p in PAGES}
    inpage = sum(per_page.values())
    total = shared + inpage
    ratio = inpage / total if total else 0
    detail = "\n".join(f"{p:<18}{n:>5}줄" for p, n in sorted(per_page.items(), key=lambda kv: -kv[1]))
    detail += f"\n{'meridian.css(공용)':<18}{shared:>5}줄"
    if ratio > 0.5:
        say(BAD, f"CSS의 {ratio * 100:.0f}%가 페이지 안에 산다 ({inpage} / {total}줄)",
            detail + "\n타이포·간격 눈금(#74)을 만들 자리가 없는 이유다 — 대부분의 CSS가 공용이 아니다.")
    else:
        say(OK, f"CSS의 {(1 - ratio) * 100:.0f}%가 공용 파일에 산다 ({shared} / {total}줄)", detail)


# ── D. 인라인 스타일 밀도 ────────────────────────────────────────────
def check_inline_density() -> None:
    head("D. 인라인 스타일 — 값이 CSS에 있는가, JS 문자열에 있는가")
    over, rows = [], []
    for rel, budget in INLINE_BUDGET.items():
        src = read(rel)
        n = len(re.findall(r'style="', src))
        per100 = n / max(len(src.splitlines()), 1) * 100
        rows.append((rel, n, budget, per100))
        if n > budget:
            over.append(f"{rel}: {n}건 > 예산 {budget}건")
    if over:
        say(BAD, f"인라인 스타일 예산을 넘은 파일 {len(over)}개", "\n".join(over))
    else:
        say(OK, f"{len(rows)}개 파일이 인라인 스타일 예산 안",
            "\n".join(f"{r:<28}{n:>4}건  100줄당 {p:5.1f}" for r, n, _b, p in
                      sorted(rows, key=lambda x: -x[3])))

    # 같은 저장소 안에 더 나은 방식이 이미 있으면 그것을 근거로 말한다.
    dense = max(rows, key=lambda r: r[3])
    thin = [r for r in rows if r[1] and r[3] < dense[3] / 2]
    if thin and dense[3] > 10:
        say(BAD, f"{dense[0]}의 밀도가 형제 파일의 2배 이상 (100줄당 {dense[3]:.1f})",
            "형제: " + " · ".join(f"{Path(r[0]).name} {r[3]:.1f}" for r in thin) +
            "\n밀도가 낮은 쪽은 tiles() 같은 헬퍼 + CSS 클래스를 쓴다 — 고칠 방법이 이미 저장소 안에 있다.")
    else:
        say(OK, "파일 간 인라인 스타일 밀도가 고르다")


# ── E. 계산 경계 ─────────────────────────────────────────────────────
def check_client_math() -> None:
    head("E. 계산 경계 — 파이썬 쌍둥이가 있는데 웹이 다시 구현한 곳")
    guarded, unguarded, orphan = [], [], []
    for site, r in CLIENT_MATH.items():
        if not (ROOT / r["js"]).exists():
            say(BAD, f"등록된 JS 자리가 사라졌다: {site}", f"{r['js']} 없음 — 레지스트리를 갱신할 것")
            continue
        twin = r["python_twin"]
        if twin is None:
            orphan.append(f"{site} — 파이썬 쌍둥이 없음. {r['why']}")
            continue
        twin_path = twin.split("::")[0]
        if not (ROOT / twin_path).exists():
            say(BAD, f"등록된 파이썬 쌍둥이가 사라졌다: {twin}", "레지스트리를 갱신할 것")
            continue
        parity = r.get("parity")
        if parity and (ROOT / parity).exists():
            guarded.append(f"{site}\n    쌍둥이 {twin}\n    양쪽을 함께 도는 검사 {parity}")
        else:
            if parity:
                say(BAD, f"등록된 대조 검사가 사라졌다: {parity}", f"{site} — 레지스트리를 갱신할 것")
            unguarded.append(f"{site}\n    쌍둥이 {twin}\n    CI가 도는 쪽 {r['ci_runs']}")

    if unguarded:
        say(BAD, f"두 언어에 살면서 대조되지 않는 곳 {len(unguarded)}개",
            "\n".join(unguarded) +
            "\n두 구현이 어긋나면 Streamlit과 Meridian 웹이 같은 종목에 다른 숫자를 보여준다.\n"
            "파이썬 쪽만 도는 테스트는 '쌍둥이가 자기 자신과 같다'만 지킨다 —\n"
            "사용자가 실제로 보는 구현은 검증되지 않는 쪽에 남는다.")
    elif guarded:
        say(OK, f"두 언어에 사는 수식 {len(guarded)}곳이 모두 양쪽 대조로 묶여 있음",
            "\n".join(guarded) +
            "\n대조는 손으로 옮겨 적은 참조 구현이 아니라 브라우저가 받는 파일을 Node로 실행한다.")
    else:
        say(OK, "두 언어에 같은 수식이 사는 곳 없음")

    if orphan:
        say(NA, f"JS가 유일한 구현인 계산 {len(orphan)}개",
            "\n".join(orphan) +
            "\n정적으로는 '이 수식이 맞는가'를 판정할 수 없다 — 파이썬 쪽에 대조할 값이 없다.")

    # 파이썬 구현이 있는데 웹 진입점이 아예 임포트하지 않는 모듈. 이것이 [문제]인 이유는
    # '임포트가 없다'가 아니라 **규칙을 지키는 구현은 테스트되고 사용자가 보는 구현은
    # 테스트되지 않는다**는 것이었다. 대조 검사가 둘을 묶으면 그 사유가 사라진다 —
    # 쌍둥이가 실행 가능한 명세가 되고, 화면이 그 명세에서 벗어나는 순간 CI가 막는다.
    web_side = read("server.py") + read("src/web/serialize.py")
    unused, spec_only = [], []
    for site, r in CLIENT_MATH.items():
        twin = r["python_twin"]
        if not twin:
            continue
        mod = twin.split("::")[0].replace("/", ".").removesuffix(".py")
        if mod in web_side:
            continue
        (spec_only if r.get("parity") else unused).append(f"{mod} — {site}가 대신 계산한다")
    if unused:
        say(BAD, f"웹 진입점이 임포트하지 않고 대조도 없는 분석 모듈 {len(unused)}개",
            "\n".join(unused) +
            "\nCLAUDE.md는 '분석 로직은 src/analysis의 순수 함수로'라고 적었다.\n"
            "규칙을 지키는 구현이 있는데, 실제 실행 진입점(대시보드실행.bat → Meridian 웹)은 그것을 쓰지 않는다.")
    else:
        say(OK, "등록된 파이썬 분석 모듈이 모두 웹에서 쓰이거나 대조로 묶여 있다",
            ("\n".join(spec_only) + "\n웹이 임포트하지는 않지만 브라우저 구현과 전 격자 대조된다 — "
             "실행 가능한 명세로 남아 있다.") if spec_only else "")

    # 같은 지표를 두 **출처**로 재는 것도 같은 종류의 결함이다(ADR-0020 · #86).
    # 두 언어(E 위쪽)가 아니라 한 화면 안에서 갈리는 경우다.
    bad = []
    for rel, what in SCREEN_MULTIPLE_SITES.items():
        if not (ROOT / rel).exists():
            bad.append(f"{rel} — 등록된 파일이 없다. 레지스트리를 갱신할 것")
            continue
        src = read(rel)
        if "screen_multiple(" not in src:
            bad.append(f"{rel} ({what}) — screen_multiple()을 쓰지 않는다")
        elif "BASIS_DISCLOSED" not in src:
            bad.append(f"{rel} ({what}) — 근거를 보지 않는다. 자체계산 폴백에서도 비교 판정이 나간다")
        if re.search(SCREEN_MULTIPLE_BANNED, src):
            bad.append(f"{rel} ({what}) — indicators 값을 곧장 비교에 넣는 옛 방식이 남아 있다")
    if bad:
        say(BAD, f"「현재」와 「업종 중앙값」의 자가 어긋난 자리 {len(bad)}곳",
            "\n".join(bad) +
            "\n업종 중앙값은 피어 프레임(공시값)에서 나온다. 「현재」가 다른 출처면 그 비교는\n"
            "기울어져 있고, 그 비율이 그대로 '싸다/비싸다' 판정이 된다.")
    else:
        say(OK, f"배수 비교표 {len(SCREEN_MULTIPLE_SITES)}곳이 모두 같은 자를 쓴다",
            "\n".join(f"{rel} — {what}" for rel, what in SCREEN_MULTIPLE_SITES.items()) +
            "\n정의를 고르는 자리는 scoring.screen_multiple 하나다.")


# ── F. 직렬화 층위 ───────────────────────────────────────────────────
def check_serialize_layer() -> None:
    head("F. 직렬화 층위 — serialize.py가 값을 옮기는가, 만드는가")
    src = read("src/web/serialize.py")
    hits = []
    for pattern, what in ANALYSIS_SMELLS:
        for m in re.finditer(pattern, src):
            line = src.count("\n", 0, m.start()) + 1
            hits.append((line, what, src.splitlines()[line - 1].strip()[:88]))
    if hits:
        say(BAD, f"직렬화 층에서 계산하는 자리 {len(hits)}건",
            "\n".join(f"serialize.py:{ln}  {what}\n    {code}" for ln, what, code in sorted(hits)) +
            "\n직렬화는 분석 결과를 JSON으로 옮기는 층이다. 여기서 값을 만들면\n"
            "그 값은 순수 함수 테스트의 사정권 밖에 있다.")
    else:
        say(OK, "serialize.py가 계산하지 않는다 — 분석 모듈에 위임")


# ── G. 검증 스크립트가 CI에 걸려 있나 ────────────────────────────────
def check_ci_gates() -> None:
    head("G. 검증 스크립트 — PR마다 자동으로 도는가")
    ci = read(".github/workflows/quality.yml")
    missing, present = [], []
    for rel, what in STATIC_CHECKS.items():
        (present if rel in ci else missing).append(f"{rel} — {what}")
    if missing:
        say(BAD, f"CI에 걸려 있지 않은 정적 검증 스크립트 {len(missing)}개",
            "\n".join(missing) +
            "\n네트워크·키가 필요 없어 CI에서 그대로 돌 수 있는데 quality.yml에 없다.\n"
            "사람이 기억해서 돌리는 검증은 회귀를 막지 못한다.")
    else:
        say(OK, f"정적 검증 스크립트 {len(present)}개가 모두 CI에 있음", "\n".join(present))

    # 관문인가 보고서인가 — 문제를 찾고도 0을 반환하면 CI에 넣어도 아무것도 막지 못한다.
    report_only = []
    for rel in STATIC_CHECKS:
        body = read(rel)
        if not re.search(r"return\s+1|sys\.exit\(1\)|SystemExit\(1\)", body):
            report_only.append(rel)
    if report_only:
        say(BAD, f"실패해도 0을 반환하는 스크립트 {len(report_only)}개",
            "\n".join(report_only) +
            "\n[문제]를 찍고도 종료코드가 0이면 CI에 넣어도 머지를 막지 못한다 — 보고서이지 관문이 아니다.")
    else:
        say(OK, "정적 검증 스크립트가 모두 실패 시 0이 아닌 값을 반환한다")


# ── H. 죽은 코드 ─────────────────────────────────────────────────────
def check_dead_code() -> None:
    head("H. 죽은 코드 — 읽는 사람이 헛걸음하는 자리")
    dead_fn = []
    for f in WEB_JS:
        src = read("web/assets/" + f)
        for m in re.finditer(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", src, re.M):
            name = m.group(1)
            if len(re.findall(r"\b" + re.escape(name) + r"\b", src)) <= 1:
                say_line = src.count("\n", 0, m.start()) + 1
                dead_fn.append(f"web/assets/{f}:{say_line} {name}()")
    if dead_fn:
        say(BAD, f"정의만 있고 부르는 곳이 없는 함수 {len(dead_fn)}개", "\n".join(dead_fn))
    else:
        say(OK, "정의만 있고 안 쓰이는 함수 없음")

    # CSS 클래스 — 동적으로 조립하는 이름('badge-' + tone)은 grep으로 못 잡는다.
    css = read("web/assets/meridian.css")
    classes = sorted(set(re.findall(r"^\s*\.([a-zA-Z][\w-]*)", css, re.M)))
    corpus = "".join(read("web/" + p) for p in PAGES) + \
             "".join(read("web/assets/" + f) for f in WEB_JS)
    unused = [c for c in classes if not re.search(r"\b" + re.escape(c) + r"\b", corpus)]
    # 런타임 조립('badge-' + tone · ' badge-' + tone)의 접두를 모아 둔다 — 이 접두로 시작하는
    # 클래스는 grep으로 안 잡혀도 살아 있을 수 있어 '판정 보류'로 내린다.
    composed = [p.strip() for p in re.findall(r"['\"]\s*([a-zA-Z][\w-]*-)['\"]\s*\+", corpus)]
    if unused:
        maybe = [c for c in unused if any(c.startswith(p) for p in composed)]
        real = [c for c in unused if c not in maybe]
        detail = ""
        if real:
            detail += "안 쓰임: " + " · ".join("." + c for c in real) + "\n"
        if maybe:
            detail += ("동적 조립일 수 있음(판정 보류): " + " · ".join("." + c for c in maybe) +
                       "\n    " + " · ".join(f"'{p}' + …" for p in sorted(set(composed))))
        say(BAD if real else NA,
            f"공용 CSS에서 참조되지 않는 클래스 {len(unused)}개", detail)
    else:
        say(OK, f"공용 CSS 클래스 {len(classes)}개가 모두 쓰인다")

    # 저장소에 없는데 서버가 서빙할 수 있는 파일 — 시안 6장 계열.
    previews = sorted(p.name for p in (ROOT / "web").glob("preview-*.html"))
    blocked = "preview-" in read("server.py")
    if previews and blocked:
        say(OK, f"로컬 시안 {len(previews)}장이 있지만 서버가 차단한다",
            " · ".join(previews) + "\nserver.py가 preview- 접두 요청을 거절한다(#70의 수정). "
            "저장소에 커밋된 적은 없다(.gitignore).")
    elif previews:
        say(BAD, f"로컬 시안 {len(previews)}장이 서빙된다", " · ".join(previews))
    else:
        say(OK, "로컬 시안 파일 없음")


# ── I. 정적으로 판정할 수 없는 것 ────────────────────────────────────
def declare_limits() -> None:
    head("I. 이 스크립트가 판정할 수 없는 것")
    say(NA, "읽고 이해하는 데 걸리는 시간",
        "줄 수·중복 수는 세지만 '이 파일을 처음 여는 사람이 얼마나 헤매는가'는 세지 못한다.\n"
        "1,990줄이 200줄 10개보다 나쁘다는 것은 숫자가 아니라 판단이다.")
    say(NA, "분할이 옳은 경계로 갈렸는가",
        "파일이 몇 줄인지는 A가 보여주지만, 렌더/차트/포맷 경계가 자연스러운지는\n"
        "사람이 읽어야 안다. 잘못 자르면 파일 수만 늘고 의존 방향이 꼬인다.\n"
        "줄 수 상한을 관문에서 뺀 이유이기도 하다 — 상한은 이 판단을 대신해 주지 못한다.")
    say(NA, "테스트가 회귀를 실제로 잡는가",
        "테스트 개수와 임포트하는 모듈은 세지만, 각 단정이 의미 있는 회귀를 잡는지는\n"
        "테스트를 읽어야 안다. 커버리지 도구도 '실행됐다'만 말하고 '검증됐다'는 말하지 않는다.")
    say(NA, "동적으로 조립되는 이름",
        "'badge-' + tone처럼 런타임에 만들어지는 클래스·아이디는 정적으로 추적할 수 없다.\n"
        "죽은 CSS 판정이 여기서 막힌다(H 검사의 '판정 보류').")


# ── 레지스트리 출력 ──────────────────────────────────────────────────
def print_registry() -> None:
    print("\n파일 크기 (참고 · 상한 없음)")
    print("─" * 76)
    for rel, why in FILE_SIZES.items():
        print(f"{rel:<32}{nlines(rel):>6}줄")
        print(f"{'':<32}{why}")

    print("\n공용 헬퍼 (허용 벌 수)")
    print("─" * 76)
    sources = {f: read("web/assets/" + f) for f in WEB_JS}
    for name, (allowed, home, why) in SHARED_HELPERS.items():
        got = {f: _extract_fn(s, name) for f, s in sources.items()}
        live = {f: v[1] for f, v in got.items() if v}
        distinct = len(set(live.values()))
        print(f"{name:<14}{len(live)}벌 (허용 {allowed}) · 로직 {distinct}종 · {' · '.join(live) or '없음'}")
        print(f"{'':<14}→ {home}  {why}")

    print("\n브라우저에서 도는 금융 수식")
    print("─" * 76)
    for site, r in CLIENT_MATH.items():
        print(f"{site}")
        print(f"   쌍둥이   {r['python_twin'] or '없음 (JS가 유일한 구현)'}")
        print(f"   CI       {r['ci_runs'] or '없음'}")
        print(f"   이유     {r['why']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="R5 코드 구조 검증")
    ap.add_argument("--list", action="store_true", help="레지스트리만 출력")
    args = ap.parse_args()

    if args.list:
        print_registry()
        return 0

    print("=" * 76)
    print("R5 검증 — 남이 열어서 읽을 수 있는가")
    print("=" * 76)

    report_file_size()
    check_duplicate_helpers()
    check_css_home()
    check_inline_density()
    check_client_math()
    check_serialize_layer()
    check_ci_gates()
    check_dead_code()
    declare_limits()

    print("\n" + "=" * 76)
    print(f"확인 {_tally[OK]} · 문제 {_tally[BAD]} · 불가 {_tally[NA]}")
    print("=" * 76)

    if _tally[BAD] > KNOWN_OPEN:
        print(f"\n실패 — 문제 {_tally[BAD]}건이 기준선 {KNOWN_OPEN}건을 넘었다.")
        print("새로 생긴 어긋남을 고치거나, 의도된 변경이면 KNOWN_OPEN을 갱신할 것.")
        return 1
    if _tally[BAD] < KNOWN_OPEN:
        print(f"\n문제가 기준선({KNOWN_OPEN}건)보다 줄었다 — KNOWN_OPEN을 {_tally[BAD]}로 내릴 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
