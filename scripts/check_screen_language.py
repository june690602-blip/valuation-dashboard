"""R3(화면 언어) 검증: 계산 결과를 정확하게, 그리고 쉽게 말하고 있는가.

값이 맞는지(R1)·가정이 타당한지(R2)는 보지 않는다. 오직 **화면에 나가는 문장**만 본다.

    python scripts/check_screen_language.py           # 전체
    python scripts/check_screen_language.py --list    # 용어집(레지스트리)만 출력

네트워크·API 키가 필요 없다. 화면 언어는 파일을 읽으면 확인되기 때문이다.

각 점검은 [확인] / [문제] / [불가] 중 하나로 결론 낸다.
- 확인: 문구가 규약과 일치함을 확인
- 문제: 어긋남을 발견 (조서 1·2번 바구니 후보)
- 불가: 정적으로는 판정할 수 없음 (사람이 읽어야 하는 것 / 3번 바구니 후보)

## 이 스크립트가 지키는 규약 — 어휘의 '층위'

PR #47이 ETF 화면에서 확립한 원칙이다. 두 어휘는 **다른 층위의 말**이라 섞으면 안 된다.

- **저평가 / 고평가** — 우리가 계산한 **내재가치(적정가) 대비 판단**.
  근거는 적정가 4방법이고, 틀릴 수 있는 추정이다.
- **싼 구간 / 비싼 구간** — **관찰된 가격 수준**. 자기 역사·시장·NAV 같은
  이미 존재하는 기준 안에서 지금이 어디쯤인지를 말할 뿐, 가치 판단이 아니다.

역사 밴드 위치를 "저평가"라고 부르면, 적정가를 계산하지 않은 자리에서 적정가 판정을
한 것처럼 읽힌다. 아래 LAYER_SITES가 그 못이다 — 판정 문구를 만드는 지점을 전수
등록하고, 각 지점이 어느 층위인지 못 박는다. 새 문구를 추가하면 여기에 등록해야 통과한다.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

OK, BAD, NA = "[확인]", "[문제]", "[불가]"
_tally = {OK: 0, BAD: 0, NA: 0}


def say(verdict: str, title: str, detail: str = ""):
    _tally[verdict] = _tally.get(verdict, 0) + 1
    print(f"  {verdict} {title}")
    for line in (detail or "").splitlines():
        if line.strip():
            print(f"         {line}")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _outside_quotes(line: str, marker: str) -> str:
    """따옴표 밖에서 시작하는 주석을 잘라낸다(따옴표 안의 # 이나 // 는 보존)."""
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif not quote and line.startswith(marker, i):
            return line[:i]
        i += 1
    return line


def screen_text(text: str, path: str) -> str:
    """주석·독스트링을 걷어내고 **화면에 나갈 수 있는 문자열만** 남긴다.

    독스트링과 주석은 개발자가 읽는 글이지 사용자가 보는 문장이 아니다. 이걸 섞어
    세면 '설명이 정확한가'와 '주석이 정확한가'가 뒤엉킨다.

    줄 번호를 보존해야 하므로 지우는 대신 **줄 수를 유지한 채 비운다**."""
    def blank(m):
        return "\n" * m.group(0).count("\n")

    if path.endswith(".py"):
        text = re.sub(r'"""[\s\S]*?"""', blank, text)
        text = re.sub(r"'''[\s\S]*?'''", blank, text)
        marker = "#"
    elif path.endswith(".js"):
        text = re.sub(r"/\*[\s\S]*?\*/", blank, text)
        marker = "//"
    else:
        return text
    return "\n".join(_outside_quotes(ln, marker) for ln in text.splitlines())


# ── 어휘 층위 ────────────────────────────────────────────────────────────────
INTRINSIC = ("저평가", "고평가")                       # 내재가치 대비 판단
OBSERVED = ("싼 구간", "비싼 구간", "보통 구간", "싼 편", "비싼 편",
            "싼 쪽", "비싼 쪽", "싸게", "비싸게")        # 관찰된 가격 수준

# 판정·결론 문구를 만드는 모든 지점. (파일, 앵커, 앵커부터 볼 줄 수, 층위, 설명)
#   intrinsic   — 적정가(내재가치) 대비 판단이므로 저평가/고평가가 맞다
#   observed    — 관찰된 가격 수준이므로 싼/비싼 구간이 맞다
#   conditional — 한 자리에서 두 층위를 분기해 쓴다(적정가를 냈는지에 따라).
#                 지금은 해당 지점이 없다 — ETF 눈금이 여기 있었으나 R3에서 observed로 정리했다.
LAYER_SITES = [
    ("src/analysis/valuation.py", r"VERDICTS = \[", 1, "intrinsic",
     "주식 3등급 판정 — 적정가 가중 종합(①③⑤) 대비 (ADR-0042로 5등급에서 줄었다)"),
    ("src/analysis/etf.py", r"def _verdict_premium", 12, "observed",
     "ETF 괴리 — NAV는 우리가 추정한 값이 아니라 공표된 값이다"),
    ("src/analysis/etf.py", r"def _dividend_lead", 6, "observed",
     "ETF 배당 밴드 위치 — 자기 5년 분포 안에서의 위치"),
    ("src/analysis/etf.py", r"def _verdict_dividend", 12, "observed",
     "ETF 배당 밴드 판정 — 자기 역사 대비 위치이지 적정가가 아니다"),
    ("src/analysis/etf.py", r"r\.stance = \(", 4, "observed",
     "ETF 상대 위치 헤드라인 — 적정가 판정을 보류한 자리(PR #47)"),
    ("src/analysis/commentary.py", r"# 2\) 역사적 밴드 위치", 12, "observed",
     "주식 역사 밴드 해설 — 자기 5년 분포 안에서의 위치"),
    ("web/assets/stock.js", r"cap\.innerHTML = '밴드는", 1, "observed",
     "밸류에이션 탭 밴드 캡션 — 밴드 분위(관찰값)를 말하는 자리"),
    ("web/assets/stock.js", r"// 3존 라벨", 6, "intrinsic",
     "주식 판정 눈금 — 적정가 대비 위치"),
    ("web/assets/stock.js", r"ETF 눈금은 \*\*항상\*\* 관찰 어휘다", 11, "observed",
     "ETF 판정 눈금 — ETF는 적정가를 계산하지 않으므로 항상 관찰 어휘"),
]


def _slice(path: str, anchor: str, span: int) -> tuple[str, int] | None:
    src = read(path)
    m = re.search(anchor, src)
    if not m:
        return None
    start = line_of(src, m.start())
    lines = src.splitlines()
    return "\n".join(lines[start - 1:start - 1 + span]), start


def check_layers():
    print("\n■ A. 어휘 층위 — 저평가/고평가(내재가치 대비)와 싼/비싼 구간(관찰된 수준)")
    for path, anchor, span, layer, note in LAYER_SITES:
        got = _slice(path, anchor, span)
        if got is None:
            say(BAD, f"{path} — 앵커를 찾지 못함",
                f"앵커 `{anchor}` 가 사라졌습니다. 문구가 옮겨졌다면 레지스트리를 갱신하세요.")
            continue
        text, ln = got
        text = screen_text(text, path)
        ins = sorted({t for t in INTRINSIC if t in text})
        obs = sorted({t for t in OBSERVED if t in text})
        where = f"{path}:{ln} · {note}"
        if layer == "conditional":
            if ins and obs:
                say(OK, where, f"두 층위를 분기해 씁니다 — 내재 {ins} · 관찰 {obs}")
            else:
                say(BAD, where, f"분기 자리인데 한쪽 어휘만 있습니다 — 내재 {ins} · 관찰 {obs}")
        elif layer == "intrinsic":
            if obs:
                say(BAD, where, f"내재가치 판단 자리에 관찰 어휘 {obs} 가 섞였습니다.")
            else:
                say(OK, where, f"내재 어휘만 사용 {ins or '(판정어 없음)'}")
        else:  # observed
            if ins and obs:
                say(BAD, where,
                    f"한 문장 안에서 층위가 섞입니다 — 내재 {ins} · 관찰 {obs}. "
                    "같은 눈금의 양끝을 다른 어휘로 부르면 읽는 사람이 두 개의 자를 봅니다.")
            elif ins:
                say(BAD, where,
                    f"관찰된 가격 수준을 말하는 자리인데 내재 어휘 {ins} 를 씁니다 — "
                    "적정가를 계산하지 않은 곳에서 적정가 판정처럼 읽힙니다.")
            else:
                say(OK, where, f"관찰 어휘만 사용 {obs or '(판정어 없음)'}")


# ── B. 구어체 잔재 ───────────────────────────────────────────────────────────
# PR #47의 진단: 주장을 약하게 하려다 **말투까지** 캐주얼하게 내려갔다. 둘은 다른
# 손잡이다 — 주장은 약하게 유지하면서 격식은 올릴 수 있다.
COLLOQUIAL = re.compile(r"(싼|비싼|높은|낮은|큰|작은|좋은|나쁜|많은|적은)\s?(편|쪽)(?=[입이라의니\s.,'\"])")
COLLOQUIAL_SCOPE = [
    "src/analysis/commentary.py", "src/analysis/etf.py", "src/analysis/valuation.py",
    "web/assets/stock.js", "web/stock.html", "web/guide.html", "web/home.html",
]

# 격식 규약을 적용하지 않는 구간. `guide.html`의 쉬운 버전(`v-basic`)은 "올라가죠"처럼
# 말을 건네는 문체를 **의도적으로** 쓰는 자리다(눈높이 토글). 여기까지 격식을 올리면
# 두 버전을 나눈 이유가 없어진다. 어휘 층위(A)와 사실 주장(D)은 쉬운 버전에도 그대로
# 적용하되, 말투(B)만 예외로 둔다 — 정확함과 격식은 다른 손잡이이기 때문이다.
COLLOQUIAL_EXEMPT = [("web/guide.html", r'id="v-basic"', r'id="v-pro"')]


def _exempt_ranges(path: str) -> list[tuple[int, int]]:
    out = []
    for p, start_re, end_re in COLLOQUIAL_EXEMPT:
        if p != path:
            continue
        src = read(path)
        s, e = re.search(start_re, src), re.search(end_re, src)
        if s and e:
            out.append((line_of(src, s.start()), line_of(src, e.start())))
    return out


def check_colloquial():
    print("\n■ B. 구어체 잔재 — '~한 편/쪽'은 판정을 흐리지 않으면서 격식만 내린다")
    verdictish, general, exempt = [], [], []
    for path in COLLOQUIAL_SCOPE:
        src = screen_text(read(path), path)
        lines = src.splitlines()
        skip = _exempt_ranges(path)
        for m in COLLOQUIAL.finditer(src):
            ln = line_of(src, m.start())
            ctx = lines[ln - 1].strip()
            row = (path, ln, m.group(0), ctx[:100])
            if any(a <= ln < b for a, b in skip):
                exempt.append(row)
                continue
            # 값의 위치를 말하는 자리(싸다/비싸다·구간·밴드)인지, 일반 서술인지 나눈다.
            # 앞은 PR #47이 정리한 판정 어휘라 규약의 대상이고, 뒤는 문체 취향이 섞인다.
            (verdictish if re.search(r"싼|비싼|구간|밴드|분위", ctx) else general).append(row)
    if verdictish:
        say(BAD, f"판정 어휘 자리의 '~한 편/쪽' {len(verdictish)}곳",
            "\n".join(f"{p}:{ln}  「{g}」  {c}" for p, ln, g, c in verdictish))
    else:
        say(OK, "판정 어휘 자리에 '~한 편/쪽' 없음")
    if general:
        say(NA, f"일반 서술의 '~한 편/쪽' {len(general)}곳 — 문체 판단이 섞여 규칙으로 못 박지 않는다",
            "\n".join(f"{p}:{ln}  「{g}」  {c}" for p, ln, g, c in general))
    if exempt:
        say(OK, f"규약 예외 구간의 '~한 편/쪽' {len(exempt)}곳 — 말투를 의도적으로 낮춘 자리",
            "\n".join(f"{p}:{ln}  「{g}」  {c}" for p, ln, g, c in exempt)
            + "\nguide.html의 쉬운 버전(v-basic)은 말을 건네는 문체가 설계다. 격식만 예외이고 "
              "어휘 층위·사실 주장은 그대로 적용된다.")


# ── C. 파이썬이 만든 문장을 자바스크립트가 문자열로 판별하는 지점 ────────────
# 등급·강조를 **문자열 부분일치**로 정하면, 문장을 다듬는 순간 조용히 깨진다.
# 화면은 그대로 그려지고 강조만 사라지므로 눈으로는 알 수 없다.
STRING_CONTRACTS = [
    {"needle": "밸류트랩", "producer": "src/analysis/commentary.py",
     "consumer": "web/assets/stock.js", "consumer_anchor": r"indexOf\('밸류트랩'\)",
     "what": "핵심 해설 강조(.cmt.key 테두리)"},
    {"needle": "순수한 저평가", "producer": "src/analysis/commentary.py",
     "consumer": "web/assets/stock.js", "consumer_anchor": r"indexOf\('순수한 저평가'\)",
     "what": "핵심 해설 강조(.cmt.key 테두리)"},
    # 「주의 ·」 접두어 규약은 **없앴다**(#68). 노트의 등급은 이제 문장 접두어가 아니라
    # ValuationNote.kind가 데이터로 들고 있다 — 문장을 다듬어도 등급이 흔들리지 않는다.
    # 이 라운드가 잡아낸 취약성(발견 7)을 같은 종류의 자리에서 미리 없앤 셈이다.
]

# 등급·무리를 **데이터로** 나르는 자리. 문자열 규약을 없앤 대신 여기가 규약이 됐다 —
# 필드가 사라지거나 값이 바뀌면 화면이 조용히 한 무리를 통째로 잃는다.
DATA_CONTRACTS = [
    {"what": "노트 등급", "producer": "src/analysis/valuation.py",
     "producer_anchor": r"class ValuationNote", "field": "kind",
     "consumer": "src/analysis/commentary.py", "consumer_anchor": r"Comment\(n\.kind, n\.text"},
    {"what": "해설 무리(근거/읽는 법)", "producer": "src/analysis/commentary.py",
     "producer_anchor": r'GROUP_READING = "reading"', "field": "group",
     "consumer": "web/assets/stock.js", "consumer_anchor": r"c\.group === 'reading'"},
    {"what": "해설 주제(가격/품질)", "producer": "src/analysis/commentary.py",
     "producer_anchor": r'ABOUT_PRICE = "price"', "field": "about",
     "consumer": "src/analysis/commentary.py", "consumer_anchor": r"c\.about == ABOUT_PRICE"},
    {"what": "판정↔근거 충돌 문장", "producer": "src/analysis/commentary.py",
     "producer_anchor": r"def verdict_conflict", "field": "conflict",
     "consumer": "web/assets/stock.js", "consumer_anchor": r"v\.conflict && v\.conflict\.short"},
]


def check_data_contracts():
    print("\n■ C2. 데이터 규약 — 문자열 대신 필드로 나르는 자리")
    for c in DATA_CONTRACTS:
        psrc, csrc = read(c["producer"]), read(c["consumer"])
        made = re.search(c["producer_anchor"], psrc)
        used = re.search(c["consumer_anchor"], csrc)
        if made and used:
            say(OK, f"{c['what']} · 필드 `{c['field']}`",
                f"만드는 곳 {c['producer']}:{line_of(psrc, made.start())} → "
                f"읽는 곳 {c['consumer']}:{line_of(csrc, used.start())}")
        elif not made:
            say(BAD, f"{c['what']} — 만드는 지점이 사라졌습니다",
                f"{c['producer']}에서 `{c['producer_anchor']}`를 찾지 못했습니다.")
        else:
            say(BAD, f"{c['what']} — 읽는 지점이 사라졌습니다",
                f"{c['consumer']}에서 `{c['consumer_anchor']}`를 찾지 못했습니다.\n"
                "화면이 한 무리를 통째로 잃어도 오류 없이 그려집니다 — 눈으로는 알 수 없는 종류입니다.")


def check_string_contracts():
    print("\n■ C. 문자열 규약 — 한쪽이 문장을 다듬으면 조용히 깨지는 결합")
    for c in STRING_CONTRACTS:
        csrc = read(c["consumer"])
        cm = re.search(c["consumer_anchor"], csrc)
        if not cm:
            say(NA, f"{c['consumer']} — 판별 지점이 사라졌습니다({c['what']})",
                "규약이 없어졌다면 레지스트리에서도 지우세요.")
            continue
        cln = line_of(csrc, cm.start())
        psrc = screen_text(read(c["producer"]), c["producer"])
        found = [line_of(psrc, m.start()) for m in re.finditer(re.escape(c["needle"]), psrc)]
        where = f"{c['consumer']}:{cln} → {c['producer']} · 「{c['needle']}」 · {c['what']}"
        if found:
            say(OK, where, f"생산 지점 {len(found)}곳(줄 {found}) — 규약이 살아 있습니다.")
        else:
            say(BAD, where,
                f"{c['producer']} 어디에도 「{c['needle']}」 문자열이 없습니다 — "
                "판별이 한 번도 참이 되지 않습니다. 화면은 정상으로 보이고 효과만 사라집니다.")


# ── D. 고정된 수를 주장하는 문구 ─────────────────────────────────────────────
# "네 가지 방법"은 사실 주장이다. 방법이 조건부로 꺼지는 코드가 있으면 그 주장은
# 항상 참일 수 없다(#63). 문구와 코드를 대조한다.
# '최대'가 앞에 붙으면 고정 주장이 아니라 상한 표시라 통과시킨다 — 방법이 꺼질 수
# 있다는 사실과 어긋나지 않는다. 이 예외가 없으면 옳게 고친 문장까지 [문제]로 떨어진다.
FIXED_COUNT = re.compile(r"(?<!최대 )(네 가지 방법|네 방법|4방법|4가지 방법|네 답)")
COUNT_SCOPE = ["web/guide.html", "web/home.html", "web/stock.html",
               "web/assets/stock.js", "src/analysis/etf.py", "src/data/models.py",
               "src/ui/pages/home.py", "src/ui/pages/stock.py"]


def check_fixed_counts():
    print("\n■ D. 고정된 수를 주장하는 문구 — 방법은 조건부로 꺼진다(#63)")
    vsrc = read("src/analysis/valuation.py")
    gate = re.search(r"book_distorted\s*=", vsrc)
    skipped = re.search(r'"skipped"', read("src/web/serialize.py"))
    if not (gate and skipped):
        say(NA, "게이트·skipped 경로를 찾지 못해 대조하지 못했습니다.")
        return
    say(OK, "코드는 방법이 꺼질 수 있음을 이미 알고 있다",
        f"src/analysis/valuation.py:{line_of(vsrc, gate.start())} `book_distorted` 게이트가 ③을 끄고, "
        "화면으로 나가는 payload에 `skipped`(사유 포함)가 이미 실려 있습니다.")
    hits = []
    for path in COUNT_SCOPE:
        src = screen_text(read(path), path)
        for m in FIXED_COUNT.finditer(src):
            hits.append((path, line_of(src, m.start()), m.group(0)))
    if not hits:
        say(OK, "사용자 화면에 고정 수 주장이 없음")
        return
    detail = "\n".join(f"{p}:{ln}  「{g}」" for p, ln, g in hits)
    say(BAD, f"방법 수를 4로 못 박은 문구 {len(hits)}곳", detail)


# ── E. 면책 ──────────────────────────────────────────────────────────────────
DISCLAIMER_PAGES = {
    "web/home.html": "홈", "web/stock.html": "주식·ETF", "web/bond.html": "금리",
    "web/portfolio.html": "포트폴리오", "web/test.html": "성향 진단", "web/guide.html": "사용설명서",
}
DISCLAIMER_MARK = ("학습", "자문이 아", "추천이 아", "권유", "투자 조언이 아")


def check_disclaimer():
    print("\n■ E. 면책 — 사용자가 들어오는 화면마다 붙어 있는가(CLAUDE.md 규칙)")
    for path, label in DISCLAIMER_PAGES.items():
        src = read(path)
        hit = [w for w in DISCLAIMER_MARK if w in src]
        if hit:
            say(OK, f"{label} ({path})", f"근거 어구 {hit}")
        else:
            say(BAD, f"{label} ({path}) — 면책 문구 없음")
    ai = read("src/analysis/ai_analysis.py")
    if "DISCLAIMER" in ai and "투자 조언이 아닙니다" in ai:
        say(OK, "AI 생성 결과 (src/analysis/ai_analysis.py)", "DISCLAIMER 상수가 항상 덧붙습니다.")
    else:
        say(BAD, "AI 생성 결과 — 면책 상수를 찾지 못했습니다.")


# ── F. 결측 안내 ─────────────────────────────────────────────────────────────
# `—` 옆 말풍선(na())은 "왜 없는지"를 알리려고 만든 자리다. "없습니다"만 반복하면
# 화면의 `—`를 한 번 더 쓴 것과 같다. 최소한 **원인**(왜 못 구했나)이나
# **결과**(그래서 무엇을 못 하나) 중 하나는 있어야 안내가 된다.
NA_CALL = re.compile(r"\bna\('([^']+)'\)")
CAUSE = ("적자", "짧", "무료", "부족", "받지 못", "확인하지 못", "없어", "없거나", "아니")
EFFECT = ("계산할 수 없", "계산하지 못", "가정", "제외", "표시하지", "판단", "탭은")


def check_na_reasons():
    print("\n■ F. 결측 안내 — `—` 말풍선이 '왜 없는지'를 실제로 설명하는가")
    src = screen_text(read("web/assets/stock.js"), "web/assets/stock.js")
    bare = []
    total = 0
    for m in NA_CALL.finditer(src):
        reason = m.group(1)
        total += 1
        if not any(k in reason for k in CAUSE) and not any(k in reason for k in EFFECT):
            bare.append((line_of(src, m.start()), reason))
    if not bare:
        say(OK, f"결측 안내 {total}곳 모두 원인이나 결과를 담고 있음")
        return
    say(BAD, f"결측 안내 {total}곳 중 {len(bare)}곳이 '없습니다'의 반복",
        "\n".join(f"web/assets/stock.js:{ln}  「{r}」" for ln, r in bare)
        + "\n원인(왜 못 구했나)도 결과(그래서 무엇을 못 하나)도 없어 `—`를 한 번 더 쓴 셈입니다.")


# ── G. 정적으로는 판정할 수 없는 것 ──────────────────────────────────────────
def note_limits():
    print("\n■ G. 이 스크립트로는 확인할 수 없는 것")
    say(NA, "문장이 근거보다 세거나 약한가",
        "'크게 저평가'가 그 종목에서 과한 말인지는 계산 결과와 함께 읽어야 안다. "
        "스크립트는 어휘의 층위까지만 본다.")
    say(NA, "결측 안내의 설명이 사실과 맞는가",
        "F는 안내가 '무엇을 말하는 모양인가'까지만 본다. 그 원인이 진짜 원인인지는 "
        "데이터 경로를 따라가며 읽어야 안다.")
    say(NA, "눈높이 두 버전(guide.html)의 톤이 본문과 어긋나지 않는가",
        "읽는 사람의 배경 지식에 대한 판단이라 규칙으로 못 박을 수 없다.")


def list_registry():
    print("용어집 — 판정 문구를 만드는 지점과 그 층위\n")
    print(f"  내재가치 대비 판단 : {' · '.join(INTRINSIC)}")
    print(f"  관찰된 가격 수준   : {' · '.join(OBSERVED)}\n")
    for path, anchor, span, layer, note in LAYER_SITES:
        got = _slice(path, anchor, span)
        ln = got[1] if got else "?"
        print(f"  [{layer:<11}] {path}:{ln}  — {note}")


def main(argv):
    ap = argparse.ArgumentParser(description="R3 화면 언어 검증")
    ap.add_argument("--list", action="store_true", help="용어집(레지스트리)만 출력")
    args = ap.parse_args(argv)
    if args.list:
        list_registry()
        return 0
    print("=" * 78)
    print("R3 화면 언어 검증 — 계산 결과를 정확하게, 쉽게 말하고 있는가")
    print("=" * 78)
    check_layers()
    check_colloquial()
    check_string_contracts()
    check_data_contracts()
    check_fixed_counts()
    check_disclaimer()
    check_na_reasons()
    note_limits()
    print("\n" + "=" * 78)
    print(f"합계 · 확인 {_tally[OK]} · 문제 {_tally[BAD]} · 불가 {_tally[NA]}")
    print("=" * 78)
    return 1 if _tally[BAD] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
