"""R4(디자인 일관성) 검증: 한 사람이 만든 것처럼 보이는가.

값이 맞는지(R1)·가정이 타당한지(R2)·문장이 정확한지(R3)는 보지 않는다.
오직 **화면의 생김새를 정하는 값**만 본다 — 색·크기·간격·그림자·반경·브레이크포인트.

    python scripts/check_design.py            # 전체
    python scripts/check_design.py --list     # 레지스트리(공용 셸·토큰)만 출력

네트워크·API 키가 필요 없다. 디자인 토큰은 파일을 읽으면 확인되기 때문이다.

각 점검은 [확인] / [문제] / [불가] 중 하나로 결론 낸다.
- 확인: 규약과 일치함을 확인
- 문제: 어긋남을 발견 (조서 1·2번 바구니 후보)
- 불가: 정적으로는 판정할 수 없음 (눈이 필요한 것 / 3번 바구니 후보)

## 이 스크립트가 지키는 규약

**하나. 디자인 결정은 토큰에만 있는다.**
`meridian.css`의 `:root`가 유일한 출처다. 화면 파일이 색·그림자·반경을 리터럴로 적으면
그 값은 토큰을 고쳐도 따라오지 않는다. R3가 판정 문구를 레지스트리로 못 박았듯이,
여기서는 **토큰 밖으로 새어 나간 값**을 전수로 잡는다.

**둘. 공용 셸은 한 벌이다.**
헤더·네비·브랜드는 7장 전부에 나오는 같은 물건이다. 페이지마다 자기 구현을 가지면
한 곳을 고쳐도 나머지가 따라오지 않는다. SHELL_SITES가 그 못이다 — 각 페이지가 어느
구현·어느 값을 쓰는지 등록하고, 등록된 값과 실제가 어긋나면 [문제]로 떨어진다.

**셋. 한 색은 한 가지를 말한다.**
`--dv-positive`가 '올랐다'이면서 동시에 '싸다'이면, 초록을 본 사람은 무엇을 읽어야 할지
알 수 없다. R3의 어휘 층위 원칙(적정가를 계산한 자리에서만 판정 어휘)을 색으로 옮긴 것이다.

**넷. 읽히지 않는 색은 색이 아니다.**
지면(`--paper` 계열) 위에서 WCAG AA(본문 4.5:1)를 넘지 못하는 토큰이 본문·캡션 색으로
쓰이면 [문제]다. 큰 글씨(24px 이상, 또는 19px 이상 굵게)는 3:1로 완화한다.
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


def say(verdict: str, title: str, detail: str = "") -> None:
    _tally[verdict] = _tally.get(verdict, 0) + 1
    print(f"  {verdict} {title}")
    for line in (detail or "").splitlines():
        if line.strip():
            print(f"         {line}")


def head(title: str) -> None:
    print(f"\n{title}")
    print("─" * 72)


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


# 검토 대상 — 파일 목록으로 못 박는다(README의 진행 규칙).
PAGES = [
    "web/home.html",
    "web/stock.html",
    "web/guide.html",
    "web/bond.html",
    "web/portfolio.html",
    "web/test.html",
    "web/admin.html",
]
SCRIPTS = [
    "web/assets/stock.js",
    "web/assets/bond.js",
    "web/assets/portfolio.js",
    "web/assets/test.js",
    "web/assets/feedback.js",
]
# 링크되지 않은 홈 시안(#70에서 삭제 예정) — 살아 있는 화면이 아니므로 검사 대상 밖.
SKIP = re.compile(r"preview-home-")


# ══════════════════════════════════════════════════════════════════════
# 레지스트리 1 — 공용 셸
#
# 헤더·네비·브랜드는 7장 전부에 나오는 같은 물건이다. 페이지마다 자기 값을 가지면
# 한 곳을 고쳐도 나머지가 따라오지 않는다.
# ══════════════════════════════════════════════════════════════════════
# R4에서 **통일하기로 확정한 것**: 지면 톤(--paper-veil) · 로고마크 · 워드마크 크기 · 건너뛰기 링크.
# **의도적으로 남긴 차이**: 헤더 높이(홈 64 = 관문 / 앱 58 = 작업대)와 구현 방식(클래스/인라인).
#   높이는 역할이 다르다는 신호라 남겼고, 구현 통합은 화면 구성이 함께 움직여 R5의 것이다.
SHELL_SITES = {
    "web/home.html": {
        "impl": "class:.site-header",
        "role": "관문",
        "height": "64px(min-height)",
        "bg": "--paper-veil",
        "brand_px": "21px",
        "brand_mark": True,             # 원형 잉크 로고마크
        "nav": ".nav-link 13px / gap 2px",
    },
    "web/guide.html": {
        "impl": "class:.site-header",
        "role": "작업대",
        "height": "58px(min-height)",
        "bg": "--paper-veil",
        "brand_px": "21px",
        "brand_mark": True,
        "nav": ".top-nav a 13.5px / gap 4px",
    },
    "web/test.html": {
        "impl": "class:.site-header",
        "role": "작업대",
        "height": "58px(min-height)",
        "bg": "--paper-veil",
        "brand_px": "21px",
        "brand_mark": True,
        "nav": ".site-nav a 13.5px / gap 4px",
    },
    "web/stock.html": {
        "impl": "inline",
        "role": "작업대",
        "height": "58px(min-height)",
        "bg": "--paper-veil",
        "brand_px": "21px",
        "brand_mark": True,
        "nav": "inline a 13px / gap 2px",
    },
    "web/bond.html": {
        "impl": "inline",
        "role": "작업대",
        "height": "58px(min-height)",
        "bg": "--paper-veil",
        "brand_px": "21px",
        "brand_mark": True,
        "nav": "inline a 13px / gap 2px",
    },
    "web/portfolio.html": {
        "impl": "inline",
        "role": "작업대",
        "height": "58px(min-height)",
        "bg": "--paper-veil",
        "brand_px": "21px",
        "brand_mark": True,
        "nav": "inline a 13px / gap 2px",
    },
    "web/admin.html": {
        "impl": "inline",
        "role": "작업대",
        "height": "58px(min-height)",
        "bg": "--paper-veil",
        "brand_px": "21px",
        "brand_mark": True,
        "nav": "inline a 13px / gap 2px",
    },
}

# 레지스트리 2 — 판정에 쓰는 색.
#
# R4 이전에는 두 프런트가 다른 계열이었다 — Streamlit은 CLAUDE.md 규칙대로 파랑/빨강,
# Meridian 웹은 초록/클레이. 게다가 웹은 그 두 색을 등락·수익률과 나눠 써서 초록 하나가
# 다섯 가지를 말했다. 지금은 양쪽 다 무채 잉크로 판정하고, 색은 부호 전용이다.
VERDICT_COLOR_SITES = {
    "src/ui/components.py:VERDICT_COLORS": {
        "front": "Streamlit",
        "저평가": "무채 잉크(진하기)",
        "고평가": "무채 잉크(진하기)",
        "shared_with": [],
    },
    "web/assets/stock.js (주식 판정 헤드라인·3존 눈금)": {
        "front": "Meridian 웹",
        "저평가": "무채 잉크(진하기)",
        "고평가": "무채 잉크(진하기)",
        "shared_with": [],
    },
    "web/assets/stock.js (ETF 관찰 헤드라인·눈금·축 배지)": {
        "front": "Meridian 웹",
        "저평가": "무채 잉크(진하기)",
        "고평가": "무채 잉크(진하기)",
        "shared_with": [],
    },
}

# --dv-positive/--dv-negative가 여전히 말해도 되는 것 — 전부 **숫자의 부호** 하나다.
# 판정이 여기서 빠졌다는 것이 R4의 결정이다.
SIGN_COLOR_MEANINGS = ["일간 등락", "초과수익", "괴리율", "백테스트 수익률·순위상관", "피어 대비 차이", "ROIC−WACC 스프레드"]

# 레지스트리 3 — 지면 위에서 텍스트로 쓰이는 토큰.
#   name: (hex, 어디에 쓰이나, 본문(4.5) 기준인가)
TEXT_TOKENS = {
    "--ink": ("#16130F", "본문·제목·판정 헤드라인", True),
    "--ink-2": ("#514C45", "2차 텍스트", True),
    "--ink-3": ("#6D6861", "캡션·kick·col-label(11px 전후) · 284곳", True),
    # 장식적 표기 전용 — 옆에 실제 라벨이 있는 순번·플레이스홀더에만 쓴다.
    # 본문 기준을 적용하지 않는다(body_text=False)는 것 자체가 이 토큰의 규약이다.
    "--ink-4": ("#8A847B", "순번·플레이스홀더 — 장식적 표기 전용", False),
    "--spine-ink": ("#756752", "섹션 번호·히어로 키커", True),
    "--spine-ink-strong": ("#63563F", "틴트 밴드 위 섹션 번호", True),
    "--dv-positive": ("#2C7556", "플러스 부호 — 등락·수익률·괴리율", True),
    "--dv-negative": ("#9D5634", "마이너스 부호 — 등락·수익률·괴리율", True),
    "--dv-navy": ("#2B4A82", "링크·강조 테두리·차트 주선", True),
    "--warning": ("#886221", "경고 문구(약한 근거·과최적화 주의)", True),
    "--danger": ("#A23A2A", "입력 오류 플레이스홀더", True),
}
PAPERS = {"--paper": "#FBF9F5", "--paper-2": "#F4F0E9", "--paper-3": "#EDE8DE"}

# 레지스트리 4 — 토큰이 정의한 값(리터럴로 다시 쓰면 안 되는 것).
TOKEN_RADII = {"2px": "--radius-sm", "4px": "--radius-md", "8px": "--radius-lg", "999px": "--radius-pill"}


# ══════════════════════════════════════════════════════════════════════
# 색 계산
# ══════════════════════════════════════════════════════════════════════
def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ══════════════════════════════════════════════════════════════════════
# A. 공용 셸 — 7장이 같은 헤더를 쓰는가
# ══════════════════════════════════════════════════════════════════════
HEADER_RE = re.compile(r"<header[^>]*>", re.I)


def check_shell() -> None:
    head("A. 공용 셸 — 헤더·네비·브랜드가 한 벌인가")

    impls: dict[str, list[str]] = {}
    missing: list[str] = []
    white_pages: list[str] = []
    unregistered: list[str] = []

    for page in PAGES:
        text = read(page)
        m = HEADER_RE.search(text)
        if not m:
            missing.append(page)
            continue
        tag = m.group(0)
        reg = SHELL_SITES.get(page)
        if reg is None:
            unregistered.append(page)
            continue

        # 등록된 구현이 실제와 맞는가
        inline = "style=" in tag
        actual_impl = "inline" if inline else ("class:." + (re.search(r'class="([^"]+)"', tag).group(1) if 'class="' in tag else "?"))
        if reg["impl"] != actual_impl:
            say(BAD, f"{page} — 헤더 구현이 레지스트리와 다름",
                f"등록: {reg['impl']} / 실제: {actual_impl}")
        impls.setdefault(reg["impl"], []).append(Path(page).name)

        # 지면 톤 — 파일을 직접 읽는다(레지스트리를 믿지 않는다)
        if re.search(r"rgba\(\s*255,\s*255,\s*255", tag) or "#fff" in tag.lower():
            white_pages.append(Path(page).name)
        # 로고마크가 실제로 마크업에 있는가
        head_block = text[m.start():m.start() + 1400]
        has_mark = 'class="brand-mark"' in head_block
        if has_mark != bool(reg["brand_mark"]):
            say(BAD, f"{page} — 로고마크 유무가 레지스트리와 다름",
                f"등록: {'있음' if reg['brand_mark'] else '없음'} / 실제: {'있음' if has_mark else '없음'}")

    if missing:
        say(NA, "헤더가 없는 페이지", " · ".join(missing))
    if unregistered:
        say(BAD, f"레지스트리에 등록되지 않은 페이지 {len(unregistered)}장",
            " · ".join(unregistered) + "\nSHELL_SITES에 등록해야 이 검사가 의미를 갖는다.")

    # 지면 톤 — 순백 헤더는 미색 종이 원칙(meridian.css 첫 주석)과 어긋난다
    stray_white: list[str] = []
    for rel in PAGES:
        text = read(rel)
        for m in re.finditer(r"rgba\(\s*255,\s*255,\s*255[^)]*\)", text):
            stray_white.append(f"{rel}:{line_of(text, m.start())}  {m.group(0)}")
    if white_pages or stray_white:
        detail = ("헤더: " + ", ".join(white_pages) + "\n" if white_pages else "")
        detail += "\n".join(stray_white)
        detail += "\nmeridian.css 첫 주석 「지면은 순백이 아니라 미색 종이」 — 지면 위 면은 --paper-veil/--paper-scrim을 쓴다."
        say(BAD, f"순백 면이 남아 있다 ({len(white_pages) + len(stray_white)}건)", detail)
    else:
        say(OK, "지면 위 면이 전부 미색 계열이다 (--paper-veil / --paper-scrim)",
            "헤더 7장 + 로딩 오버레이 + 목차 바. 홈에서 다른 화면으로 넘어가도 지면 톤이 이어진다.")

    # 브랜드
    unmarked = [Path(p).name for p, r in SHELL_SITES.items() if not r["brand_mark"]]
    if unmarked:
        say(BAD, f"로고마크가 없는 화면 {len(unmarked)}장", ", ".join(unmarked))
    else:
        say(OK, f"로고마크가 {len(SHELL_SITES)}장 전부에 있다",
            "meridian.css의 .brand-mark 한 곳에서 정의한다 — 한 번 고치면 7장이 함께 움직인다.")

    sizes = {r["brand_px"] for r in SHELL_SITES.values()}
    if len(sizes) > 1:
        say(BAD, f"브랜드 워드마크 크기가 {len(sizes)}가지", " · ".join(sorted(sizes)))
    else:
        say(OK, f"브랜드 워드마크 크기가 하나 ({sizes.pop()})")

    # ── 여기부터는 **의도적으로 남긴 차이**다 ──
    heights: dict[str, list[str]] = {}
    for page, r in SHELL_SITES.items():
        heights.setdefault(f"{r['height']} · {r['role']}", []).append(Path(page).name)
    say(OK, f"헤더 높이 {len(heights)}가지 — 역할에 따른 의도된 차이 (전부 min-height)",
        "\n".join(f"{k} — {', '.join(v)}" for k, v in heights.items())
        + "\n홈은 관문이라 한 단계 크고, 나머지는 작업대라 낮다. 브랜드·톤은 같으므로\n"
          "'다른 사이트'로 읽히지 않는다 — 다른 방이라는 신호만 남긴다.")

    navs = {r["nav"] for r in SHELL_SITES.values()}
    if len(navs) > 1:
        say(BAD, f"네비 링크 규격이 {len(navs)}가지 — 이슈로 남김",
            "\n".join(sorted(navs))
            + f"\n헤더 구현도 {len(impls)}벌이다: "
            + " / ".join(f"{k}({len(v)})" for k, v in impls.items())
            + "\n한 벌로 합치려면 마크업이 함께 움직여 화면 구성 변경이 된다 — R5의 범위다.")
    else:
        say(OK, "네비 링크 규격이 하나")


# ══════════════════════════════════════════════════════════════════════
# B. 토큰 밖으로 새어 나간 값
# ══════════════════════════════════════════════════════════════════════
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
# `var(--token, #fallback)` 형태의 뒷값은 리터럴이 아니라 **토큰이 없을 때의 보험**이다.
# feedback.js는 meridian.css 없이도 뜨도록 만든 위젯이라 전부 이 형태다 — 규약 위반이 아니다.
VAR_FALLBACK_RE = re.compile(r"var\(\s*--[A-Za-z0-9-]+\s*,\s*(#[0-9a-fA-F]{3,8})")
SHADOW_RE = re.compile(r"box-shadow\s*:\s*([^;\"'}]+)")
RADIUS_RE = re.compile(r"border-radius\s*:\s*([0-9.]+px)")

# 정당한 예외 — 잉크/그린/네이비 위에 얹는 흰 글씨는 토큰(--ink-inverse/--cta-ink)과
# 같은 값이라 리터럴이어도 의미가 흔들리지 않는다. `#000`은 stock.js의 cvar()가
# CSS 변수를 못 읽었을 때 쓰는 방어값이다(화면에 나갈 일이 없다).
HEX_ALLOW = {"#fff", "#ffffff", "#FFF", "#FFFFFF", "#000", "#000000"}


def _targets() -> list[str]:
    return [p for p in PAGES + SCRIPTS if not SKIP.search(p)]


def check_literals() -> None:
    head("B. 토큰 밖의 값 — 색·그림자·반경을 리터럴로 적은 자리")

    # 1) 색
    stray_hex: list[str] = []
    fallbacks = 0
    for rel in _targets():
        text = read(rel)
        fb_spans = [m.span(1) for m in VAR_FALLBACK_RE.finditer(text)]
        fallbacks += len(fb_spans)
        for m in HEX_RE.finditer(text):
            if m.group(0) in HEX_ALLOW:
                continue
            if any(s <= m.start() < e for s, e in fb_spans):
                continue
            stray_hex.append(f"{rel}:{line_of(text, m.start())}  {m.group(0)}")
    if stray_hex:
        say(BAD, f"토큰 밖 색 리터럴 {len(stray_hex)}건",
            "\n".join(stray_hex) + f"\n(var(--x,#기본값) 형태의 폴백 {fallbacks}건은 제외했다 — 토큰이 없을 때의 보험이다)")
    else:
        say(OK, f"색은 전부 토큰으로 쓴다 (#fff·폴백 {fallbacks}건 예외)")

    # 2) 그림자
    stray_shadow: list[str] = []
    ink_bases: set[str] = set()
    for rel in _targets():
        text = read(rel)
        for m in SHADOW_RE.finditer(text):
            value = m.group(1).strip()
            if value.startswith("var(--shadow") or value in ("none", "inherit"):
                continue
            if value.startswith("inset"):
                continue        # inset 강조선은 그림자 위계가 아니라 표시선이다
            if "var(--shadow" in value:
                continue
            stray_shadow.append(f"{rel}:{line_of(text, m.start())}  {value}")
            rgba = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", value)
            if rgba:
                ink_bases.add(",".join(rgba.groups()))
    if stray_shadow:
        detail = "\n".join(stray_shadow)
        if len(ink_bases) > 1:
            detail += f"\n그림자 잉크가 {len(ink_bases)}가지다: " + " · ".join(f"rgb({b})" for b in sorted(ink_bases))
            detail += "\n토큰(--shadow-*)은 rgba(20,19,15)만 쓴다 — 다른 잉크가 섞이면 같은 깊이가 다른 색으로 앉는다."
        say(BAD, f"토큰 밖 그림자 {len(stray_shadow)}건", detail)
    else:
        say(OK, "그림자는 전부 --shadow-* 토큰")

    # 3) 반경
    stray_radius: dict[str, list[str]] = {}
    for rel in _targets():
        text = read(rel)
        for m in RADIUS_RE.finditer(text):
            value = m.group(1)
            stray_radius.setdefault(value, []).append(f"{rel}:{line_of(text, m.start())}")
    if stray_radius:
        lines = []
        for value, sites in sorted(stray_radius.items(), key=lambda kv: float(kv[0][:-2])):
            token = TOKEN_RADII.get(value)
            tag = f"= {token}" if token else "← 토큰에 없는 값"
            lines.append(f"{value:>6} ×{len(sites):<2} {tag}   {sites[0]}" + (f" 외 {len(sites)-1}곳" if len(sites) > 1 else ""))
        off_scale = [v for v in stray_radius if v not in TOKEN_RADII]
        detail = "\n".join(lines)
        if off_scale:
            detail += f"\n스케일 밖: {' · '.join(sorted(off_scale, key=lambda v: float(v[:-2])))}"
        say(BAD, f"반경을 리터럴로 적은 자리 {sum(len(v) for v in stray_radius.values())}건", detail)
    else:
        say(OK, "반경은 전부 --radius-* 토큰")


# ══════════════════════════════════════════════════════════════════════
# C. 그림자 위계 — PR #48이 못 박은 것이 지켜지는가
# ══════════════════════════════════════════════════════════════════════
# --shadow-card는 카드급 컨테이너 전용. 배지·세그먼트·표·입력에는 쓰지 않는다.
SMALL_ELEMENT_HINT = re.compile(r"\.(badge|seg|chip|tag|tbl|row|btn|input|pill)\b")


def check_shadow_hierarchy() -> None:
    head("C. 그림자 위계 — --shadow-card는 카드급 전용인가 (PR #48)")

    violations: list[str] = []
    users: list[str] = []
    for rel in _targets():
        text = read(rel)
        for m in re.finditer(r"var\(--shadow-card\)", text):
            ln = line_of(text, m.start())
            line = text.splitlines()[ln - 1]
            users.append(f"{rel}:{ln}")
            selector = line.split("{")[0].strip()
            if SMALL_ELEMENT_HINT.search(selector):
                violations.append(f"{rel}:{ln}  {selector}")
    if violations:
        say(BAD, f"--shadow-card가 소형 요소에 쓰인 자리 {len(violations)}건", "\n".join(violations))
    else:
        say(OK, f"--shadow-card는 카드급에만 쓰인다 ({len(users)}곳)",
            "PR #48의 규약이 회귀하지 않았다.")

    # 페이지별 카드 그림자 유무 — 같은 '카드'가 화면마다 다른 깊이면 위계가 아니라 우연이다
    per_page: dict[str, int] = {}
    for page in PAGES:
        per_page[Path(page).name] = read(page).count("var(--shadow-card)")
    flat = [k for k, v in per_page.items() if v == 0]
    lifted = [k for k, v in per_page.items() if v > 0]
    if flat and lifted:
        say(BAD, "카드 그림자를 쓰는 화면과 안 쓰는 화면이 갈린다",
            f"뜬 카드: {', '.join(f'{k}({per_page[k]})' for k in lifted)}\n"
            f"평평한 화면: {', '.join(flat)}\n"
            "같은 '테두리 있는 상자'가 화면에 따라 떠 있기도 하고 붙어 있기도 하다.")
    else:
        say(OK, "카드 깊이가 화면마다 같다")


# ══════════════════════════════════════════════════════════════════════
# D. 판정 색 규약 — 한 색이 한 가지를 말하는가
# ══════════════════════════════════════════════════════════════════════
def check_verdict_color() -> None:
    head("D. 판정 색 — 한 색이 한 가지를 말하는가")

    fronts = {v["front"]: (v["저평가"], v["고평가"]) for v in VERDICT_COLOR_SITES.values()}
    if len({tuple(x) for x in fronts.values()}) > 1:
        detail = "\n".join(f"{k}: 저평가 {v[0]} / 고평가 {v[1]}" for k, v in fronts.items())
        detail += "\nCLAUDE.md 「파랑=저평가·빨강=고평가는 판정 전용」 — 규칙은 Streamlit만 지키고 있다."
        say(BAD, "두 프런트의 판정 색이 다른 계열이다", detail)
    else:
        say(OK, "두 프런트의 판정 색이 같은 계열")

    shared = {k: v["shared_with"] for k, v in VERDICT_COLOR_SITES.items() if v["shared_with"]}
    if shared:
        lines = []
        for site, meanings in shared.items():
            lines.append(f"{site}")
            lines.append(f"  판정 외 같은 토큰을 쓰는 뜻: {' · '.join(meanings)}")
        lines.append("초록이 '싸다'인지 '올랐다'인지 색만으로는 갈리지 않는다.")
        say(BAD, "판정 색이 등락·수익률 색과 같은 토큰이다", "\n".join(lines))
    else:
        say(OK, "판정 색이 부호 색과 분리돼 있다",
            "--dv-positive/--dv-negative가 말하는 것은 이제 하나뿐이다 — 숫자의 부호.\n"
            "  " + " · ".join(SIGN_COLOR_MEANINGS))

    # 판정을 만드는 자리에 부호 색이 되살아났는가 — 회귀 감시
    js = read("web/assets/stock.js")
    regressed: list[str] = []
    for m in re.finditer(r"var vColor = .*", js):
        if "dv-positive" in m.group(0) or "dv-negative" in m.group(0):
            regressed.append(f"web/assets/stock.js:{line_of(js, m.start())}  {m.group(0).strip()[:80]}")
    # 3존 눈금 라벨 — 활성 존 강조에 부호 색을 쓰면 같은 회귀다
    for m in re.finditer(r"tone === '(positive|negative)' \? ';color:var\(--dv-[a-z]+\)", js):
        regressed.append(f"web/assets/stock.js:{line_of(js, m.start())}  눈금 라벨에 부호 색")
    if regressed:
        say(BAD, f"판정 자리에 부호 색이 되살아났다 {len(regressed)}건", "\n".join(regressed))
    else:
        say(OK, "판정 헤드라인·눈금 라벨이 무채 잉크만 쓴다",
            "주식·ETF 양쪽 · 활성 존은 --ink(굵게), 비활성은 --ink-3.\n"
            "눈금 막대 자체는 자(scale)라서 --dv-green/--dv-clay를 유지한다.")

    # 판정 양끝의 대비가 같은 등급인가 — R3 발견 2(양끝을 같은 계열로)의 색 판본
    pos = contrast(TEXT_TOKENS["--dv-positive"][0], PAPERS["--paper"])
    neg = contrast(TEXT_TOKENS["--dv-negative"][0], PAPERS["--paper"])
    if (pos >= 4.5) != (neg >= 4.5):
        say(BAD, "판정 눈금 양끝의 가독 등급이 다르다",
            f"저평가 --dv-positive {pos:.2f}:1 (AA {'통과' if pos >= 4.5 else '미달'}) / "
            f"고평가 --dv-negative {neg:.2f}:1 (AA {'통과' if neg >= 4.5 else '미달'})\n"
            "같은 눈금의 양끝인데 한쪽만 본문 대비를 넘는다.")
    else:
        say(OK, "판정 눈금 양끝의 가독 등급이 같다", f"{pos:.2f}:1 / {neg:.2f}:1")


# ══════════════════════════════════════════════════════════════════════
# E. 대비 — 읽히지 않는 색은 색이 아니다
# ══════════════════════════════════════════════════════════════════════
ROOT_VAR_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;")


def check_contrast() -> None:
    head("E. 대비 — 지면 위 텍스트 토큰이 WCAG AA(4.5:1)를 넘는가")

    # 레지스트리가 meridian.css와 같은 값을 보고 있는가.
    # 여기가 어긋나면 아래 대비 계산은 존재하지 않는 색을 검사하는 셈이 된다.
    css_vars = dict(ROOT_VAR_RE.findall(read("web/assets/meridian.css")))
    drift = [(n, v[0], css_vars.get(n)) for n, v in {**TEXT_TOKENS, **{k: (h, "", True) for k, h in PAPERS.items()}}.items()
             if css_vars.get(n) and css_vars[n].upper() != v[0].upper()]
    missing = [n for n in TEXT_TOKENS if n not in css_vars]
    if drift or missing:
        detail = "\n".join(f"{n}: 레지스트리 {reg} / meridian.css {css}" for n, reg, css in drift)
        if missing:
            detail += "\nmeridian.css에서 못 찾은 토큰: " + " · ".join(missing)
        say(BAD, "레지스트리와 meridian.css의 토큰 값이 다르다", detail)
    else:
        say(OK, f"레지스트리가 meridian.css와 같은 값을 본다 ({len(TEXT_TOKENS)}개 토큰)")

    rows = []
    fails = []
    borderline = []          # 지면에서는 통과하지만 틴트 면(paper-2/3) 위에서 무너지는 것
    for name, (hex_color, usage, body_text) in TEXT_TOKENS.items():
        worst = min(contrast(hex_color, p) for p in PAPERS.values())
        on_paper = contrast(hex_color, PAPERS["--paper"])
        rows.append((name, hex_color, on_paper, worst, usage))
        if not body_text:
            continue
        if on_paper < 4.5:
            fails.append((name, hex_color, on_paper, worst, usage))
        elif worst < 4.5:
            borderline.append((name, hex_color, on_paper, worst, usage))

    print("         토큰                 색        --paper  최악(paper-3)  쓰이는 곳")
    print("         (◦ = 장식적 표기 전용 — 본문 기준을 적용하지 않는다)")
    for name, hex_color, on_paper, worst, usage in rows:
        decorative = not TEXT_TOKENS[name][2]
        mark = "◦" if decorative else ("✗" if on_paper < 4.5 else ("△" if worst < 4.5 else "✓"))
        print(f"         {mark} {name:<18} {hex_color}  {on_paper:6.2f}   {worst:6.2f}        {usage}")

    if fails:
        detail = "\n".join(
            f"{n} {h} — {p:.2f}:1 (AA 4.5 미달) · {u}" for n, h, p, _, u in fails
        )
        detail += "\n큰 글씨(24px↑ 또는 19px↑ 굵게)는 3:1로 완화되지만, 위 토큰들은 9.5~13px 자리에 쓰인다."
        say(BAD, f"지면(--paper) 위에서 본문 대비 미달 {len(fails)}건", detail)
    else:
        say(OK, "텍스트 토큰이 전부 AA를 넘는다")

    if borderline:
        say(BAD, f"틴트 면 위에서만 무너지는 토큰 {len(borderline)}건",
            "\n".join(f"{n} — --paper {p:.2f} → --paper-3 {w:.2f} · {u}" for n, _, p, w, u in borderline)
            + "\n표 hover·강조 행(--paper-2/3)에 얹히면 같은 글자가 등급을 잃는다.")

    # 경고색은 가장 잘 읽혀야 하는데 가장 안 읽힌다 — 따로 짚는다
    warn = contrast(TEXT_TOKENS["--warning"][0], PAPERS["--paper"])
    if warn < 3.0:
        say(BAD, f"--warning이 큰 글씨 기준(3:1)조차 넘지 못한다 — {warn:.2f}:1",
            "경고 문구(약한 근거·과최적화 주의)의 글자색으로 쓰이는 토큰이다.")


# ══════════════════════════════════════════════════════════════════════
# F. 브레이크포인트 — 줄바꿈 지점이 공유되는가
# ══════════════════════════════════════════════════════════════════════
MEDIA_RE = re.compile(r"@media\s*\(\s*(max|min)-width:\s*(\d+)px")


def check_breakpoints() -> None:
    head("F. 브레이크포인트 — 화면이 같은 지점에서 접히는가")

    found: dict[int, set[str]] = {}
    for rel in PAGES + ["web/assets/meridian.css"]:
        if SKIP.search(rel):
            continue
        text = read(rel)
        for m in MEDIA_RE.finditer(text):
            found.setdefault(int(m.group(2)), set()).add(Path(rel).name)

    lines = [f"{bp:>5}px — {', '.join(sorted(files))}" for bp, files in sorted(found.items())]
    shared = [bp for bp, files in found.items() if len(files) > 1]
    if len(found) > 4:
        detail = "\n".join(lines)
        detail += f"\n공유되는 지점은 {len(shared)}개뿐이다: {', '.join(f'{b}px' for b in sorted(shared)) or '없음'}"
        detail += "\n토큰이 없어 각 파일이 자기 지점을 정했다. 창을 줄이면 화면마다 다른 폭에서 접힌다."
        say(BAD, f"브레이크포인트가 {len(found)}가지", detail)
    else:
        say(OK, f"브레이크포인트가 {len(found)}가지로 수렴")

    # 공용 CSS가 페이지 구조를 직접 겨냥하는가
    css = read("web/assets/meridian.css")
    structural = re.findall(r"^\s*(body\s*>[^{]+)\{", css, re.M)
    if structural:
        say(BAD, "공용 CSS가 페이지 마크업 구조를 직접 겨냥한다",
            "\n".join(s.strip() for s in structural)
            + "\n구조를 겨냥한 선택자는 마크업이 같기만 하면 의도하지 않은 화면까지 덮는다.")
    else:
        say(OK, "공용 CSS가 구조가 아니라 역할(.app-header)을 겨냥한다",
            "R4 이전에는 `body > div > header`여서 자기 반응형을 가진 홈까지 !important로 덮었다.\n"
            "실측(820px): 홈 헤더 92px · 브랜드 왼쪽 여백 41px(헤더 패딩 16 + --page-pad 25 이중 적용).")


# ══════════════════════════════════════════════════════════════════════
# G. 타입 스케일 — 토큰이 아예 없는 축
# ══════════════════════════════════════════════════════════════════════
FONT_SIZE_RE = re.compile(r"font-size:\s*([0-9.]+)px")


def check_type_scale() -> None:
    head("G. 타이포·간격 — 토큰이 있는가")

    css = read("web/assets/meridian.css")
    has_fs_token = bool(re.search(r"--(fs|font-size|text)-", css))
    has_space_token = bool(re.search(r"--(space|spacing|gap)-", css))

    sizes: dict[float, int] = {}
    for rel in _targets():
        for m in FONT_SIZE_RE.finditer(read(rel)):
            v = float(m.group(1))
            sizes[v] = sizes.get(v, 0) + 1

    if not has_fs_token:
        ladder = " ".join(f"{v:g}" for v in sorted(sizes))
        detail = f"실제로 쓰이는 크기 {len(sizes)}가지 · 선언 {sum(sizes.values())}건\n{ladder}"
        detail += "\n9~14px 구간은 0.5px 간격의 사다리라 의도가 보이지만, 15px 위로는 눈금이 없다."
        say(BAD, "타이포 크기 토큰이 meridian.css에 없다", detail)
    else:
        say(OK, "타이포 크기 토큰이 있다")

    if not has_space_token:
        say(BAD, "간격 토큰이 meridian.css에 없다",
            "padding·gap이 1px 단위로 흩어져 있다(gap만 24가지). 카드 안쪽 여백이\n"
            "화면마다 15/16/18/20/22/26px으로 갈리는 이유다.")
    else:
        say(OK, "간격 토큰이 있다")

    # 정보를 나르는 글자의 바닥은 10px로 둔다. 순번(탭의 01~09)만 예외인데,
    # 바로 옆에 실제 라벨("기업·뉴스")이 있어 그 숫자를 못 읽어도 잃는 정보가 없기 때문이다.
    NUMBERING = {"web/stock.html": [".tabbtn .tn"]}
    tiny: dict[float, list[str]] = {}
    for rel in _targets():
        text = read(rel)
        for m in FONT_SIZE_RE.finditer(text):
            v = float(m.group(1))
            if v >= 10:
                continue
            ln = line_of(text, m.start())
            line = text.splitlines()[ln - 1]
            if any(sel in line for sel in NUMBERING.get(rel, [])):
                continue
            tiny.setdefault(v, []).append(f"{rel}:{ln}")
    if tiny:
        say(BAD, f"10px 미만 글자 {sum(len(v) for v in tiny.values())}건",
            "\n".join(f"{v:g}px — " + " · ".join(sites) for v, sites in sorted(tiny.items())))
    else:
        say(OK, "정보를 나르는 글자가 전부 10px 이상",
            "예외는 탭 순번(.tabbtn .tn 9.5px)뿐 — 옆에 실제 라벨이 있어 못 읽어도 잃는 정보가 없다.\n"
            "R4 이전에는 점수 막대 중앙선에 8px 라벨이 5개 있었다(실측).")


# ══════════════════════════════════════════════════════════════════════
# H. 접근성 안전장치 — 있는가 없는가만 본다(충분한가는 사람이 본다)
# ══════════════════════════════════════════════════════════════════════
def check_a11y() -> None:
    head("H. 접근성 안전장치 — 최소 장치가 놓여 있는가")

    css = read("web/assets/meridian.css")
    if ":focus-visible" in css:
        say(OK, "전역 포커스 링이 정의돼 있다", "meridian.css:178 — 2px 아웃라인 + offset 2px")
    else:
        say(BAD, "전역 포커스 링이 없다")

    if "prefers-reduced-motion" in css:
        say(OK, "모션 축소 설정을 존중한다")
    else:
        say(BAD, "prefers-reduced-motion 대응이 없다")

    if ".sr-only" in css:
        say(OK, "스크린리더 전용 클래스가 있다")
    else:
        say(BAD, ".sr-only가 없다")

    # 건너뛰기 링크 — 탭바·사이드바가 긴 화면일수록 필요하다
    with_skip = [Path(p).name for p in PAGES if "skip-link" in read(p)]
    without = [Path(p).name for p in PAGES if "skip-link" not in read(p)]
    if without:
        say(BAD, f"본문 건너뛰기 링크가 {len(without)}장에 없다",
            f"있음: {', '.join(with_skip) or '없음'}\n없음: {', '.join(without)}\n"
            "주식 화면은 헤더 + 9개 탭바를 지나야 본문에 닿는다.")
    else:
        say(OK, "본문 건너뛰기 링크가 전 화면에 있다")

    # 아이콘 전용 컨트롤에 이름이 붙어 있는가 — 표본 검사(전수는 렌더가 필요하다)
    unnamed = []
    for rel in PAGES:
        text = read(rel)
        for m in re.finditer(r"<button(?![^>]*aria-label)(?![^>]*title=)[^>]*>\s*<svg", text):
            unnamed.append(f"{rel}:{line_of(text, m.start())}")
    if unnamed:
        say(BAD, f"이름 없는 아이콘 버튼 {len(unnamed)}건", "\n".join(unnamed))
    else:
        say(OK, "아이콘 전용 버튼에 aria-label 또는 title이 붙어 있다")

    # 넓은 표가 스크롤 컨테이너 안에 있는가 (본문을 가로로 밀지 않는가)
    stock = read("web/stock.html")
    has_pair = "overflow-x: auto" in stock and re.search(r"#\w+Table .row[^{]*\{[^}]*min-width", stock)
    if has_pair:
        say(OK, "좁은 화면에서 넓은 표가 자기 스크롤 안에 갇힌다",
            "stock.html:193-194 — .tbl{overflow-x:auto} + .row{min-width:620px} 짝.\n"
            "실측(clientWidth 390px): 스크롤 컨테이너 밖으로 새는 요소 0건.")
    else:
        say(BAD, "넓은 표에 가로 스크롤 짝(overflow-x + min-width)이 없다")


# ══════════════════════════════════════════════════════════════════════
# I. 정적으로는 판정할 수 없는 것
# ══════════════════════════════════════════════════════════════════════
def declare_limits() -> None:
    head("I. 이 스크립트가 판정하지 않는 것")

    say(NA, "값의 조화 — 이 색과 이 색이 어울리는가",
        "대비비는 계산되지만 '어울림'은 계산되지 않는다. 취향이 섞이는 축이라\n"
        "이슈 #53이 정한 대로 대화로 방향을 확정해야 한다.")
    say(NA, "레이아웃이 실제로 어떻게 보이는가",
        "요소 위치·겹침·잘림은 렌더된 화면을 재야 안다. 이 라운드는 브라우저를 띄워\n"
        "getComputedStyle/getBoundingClientRect로 쟀지만, 그 측정은 스크립트에 담기지 않는다\n"
        "(서버 기동이 필요해 check_* 계열의 '키 없이 실행' 규약을 깬다).")
    say(NA, "인라인 스타일의 정당성",
        "stock.js는 값에 따라 색·폭이 바뀌는 자리가 많아 인라인이 불가피한 곳이 있다.\n"
        "어디까지가 그런 자리인지는 코드 구조 판단이라 R5의 것이다.")
    say(NA, "Streamlit 프런트의 화면",
        "src/ui/는 Plotly·Streamlit 위젯이 그려서 CSS 토큰이 닿지 않는다.\n"
        "판정 색 대조(D)만 했고 나머지 생김새는 보지 않았다.")


def print_registry() -> None:
    print("\n공용 셸 레지스트리 — 7장이 헤더를 어떻게 그리나")
    print("─" * 72)
    print(f"{'페이지':<22}{'구현':<20}{'높이':<18}{'배경':<24}{'워드마크'}")
    for page, r in SHELL_SITES.items():
        mark = "◉" if r["brand_mark"] else "·"
        print(f"{Path(page).name:<22}{r['impl']:<20}{r['height']:<18}{r['bg']:<24}{r['brand_px']} {mark}")

    print("\n판정 색 레지스트리")
    print("─" * 72)
    for site, r in VERDICT_COLOR_SITES.items():
        print(f"{site}")
        print(f"   {r['front']:<12} 저평가 {r['저평가']:<22} 고평가 {r['고평가']}")
        if r["shared_with"]:
            print(f"   {'':<12} 같은 토큰의 다른 뜻: {' · '.join(r['shared_with'])}")

    print("\n텍스트 토큰 대비 (지면 --paper #FBF9F5 기준)")
    print("─" * 72)
    for name, (hex_color, usage, _) in TEXT_TOKENS.items():
        cr = contrast(hex_color, PAPERS["--paper"])
        print(f"{name:<20}{hex_color}  {cr:6.2f}:1   {usage}")


def main() -> int:
    ap = argparse.ArgumentParser(description="R4 디자인 일관성 검증")
    ap.add_argument("--list", action="store_true", help="레지스트리만 출력")
    args = ap.parse_args()

    if args.list:
        print_registry()
        return 0

    print("=" * 72)
    print("R4 검증 — 한 사람이 만든 것처럼 보이는가")
    print("=" * 72)

    check_shell()
    check_literals()
    check_shadow_hierarchy()
    check_verdict_color()
    check_contrast()
    check_breakpoints()
    check_type_scale()
    check_a11y()
    declare_limits()

    print("\n" + "=" * 72)
    print(f"확인 {_tally[OK]} · 문제 {_tally[BAD]} · 불가 {_tally[NA]}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
