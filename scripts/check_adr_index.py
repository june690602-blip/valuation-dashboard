"""ADR 인덱스 검사 — `docs/adr/README.md`의 표가 실제 ADR 파일과 맞는가.

    python scripts/check_adr_index.py

네트워크도 API 키도 필요 없다. CI가 PR마다 돌린다.

## 왜 있는가

ADR 인덱스는 **줄 추가만** 일어나는 표다. PR마다 자기 ADR 한 줄을 표 끝에 붙이는데,
git의 기본 머지 전략은 "같은 자리에 서로 다른 줄"을 충돌로 본다. 그래서 ADR을 만드는
PR이 둘 이상 열려 있으면 **나중에 머지되는 쪽이 항상 충돌**했다(#99·#100·#101에서 세 번).

`.gitattributes`의 `merge=union`이 그 충돌을 없앤다 — 양쪽 줄을 모두 남긴다. 대신 두 가지가
무너질 수 있고, 이 스크립트가 그걸 잡는다.

- **번호 순서가 어긋날 수 있다.** 표의 의미는 그대로라 알림만 한다([불가]).
- **양쪽이 같은 줄을 고치면 그 줄이 두 번 남는다.** 이건 조용히 틀린 문서를 만드므로
  실패시킨다([문제]).

여기에 사람이 자주 내는 실수 둘을 같이 본다 — 파일은 만들었는데 표에 안 넣은 경우,
표에는 있는데 링크가 깨진 경우. 셋 다 "인덱스가 파일과 어긋난다"는 한 가지 문제다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

ADR_DIR = ROOT / "docs" / "adr"
INDEX = ADR_DIR / "README.md"
OK, BAD, NA = "[확인]", "[문제]", "[불가]"

ROW = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|")
FILE_NUM = re.compile(r"^(\d{4})-")

_tally = {OK: 0, BAD: 0, NA: 0}


def say(verdict: str, title: str, detail: str = "") -> None:
    _tally[verdict] += 1
    print(f"  {verdict} {title}")
    for line in (detail or "").splitlines():
        if line.strip():
            print(f"         {line}")


def main() -> int:
    print("\nADR 인덱스 — 표가 파일과 맞는가\n" + "─" * 72)
    text = INDEX.read_text(encoding="utf-8")
    lines = text.splitlines()

    files = {m.group(1): p.name for p in sorted(ADR_DIR.glob("*.md"))
             if (m := FILE_NUM.match(p.name))}
    rows: dict[str, str] = {}
    dup_nums: list[str] = []
    for line in lines:
        m = ROW.match(line.strip())
        if not m:
            continue
        num, href = m.group(1), m.group(2)
        if num in rows:
            dup_nums.append(num)
        else:
            rows[num] = href

    # ── union이 무너지는 자리 ────────────────────────────────────────
    seen: set[str] = set()
    dup_lines: list[str] = []
    for line in lines:
        s = line.strip()
        if not s.startswith("|"):
            continue
        if s in seen:
            dup_lines.append(s)
        seen.add(s)
    if dup_lines or dup_nums:
        detail = ["머지가 양쪽 줄을 모두 남긴 자리다(.gitattributes의 merge=union).",
                  "한쪽을 지우면 된다 — 어느 쪽을 남길지는 사람이 정해야 한다."]
        if dup_nums:
            detail.insert(0, f"같은 번호가 두 줄: {', '.join(sorted(set(dup_nums)))}")
        detail[:0] = dup_lines[:5]
        say(BAD, f"표에 같은 줄이 두 번 있다 ({len(dup_lines) + len(dup_nums)}건)",
            "\n".join(detail))
    else:
        say(OK, "중복된 줄 없음")

    # ── 표 ↔ 파일 ───────────────────────────────────────────────────
    missing = sorted(set(files) - set(rows))
    extra = sorted(set(rows) - set(files))
    if missing:
        say(BAD, f"파일은 있는데 표에 없는 ADR {len(missing)}건",
            "\n".join(f"{n} — {files[n]}" for n in missing) +
            "\n표 끝에 한 줄 붙이면 된다. 순서는 신경 쓰지 않아도 된다.")
    if extra:
        say(BAD, f"표에는 있는데 파일이 없는 ADR {len(extra)}건", ", ".join(extra))
    if not missing and not extra:
        say(OK, f"표와 파일이 일치한다 ({len(files)}건)")

    broken = [f"{n} → {href}" for n, href in sorted(rows.items())
              if not (ADR_DIR / href).exists()]
    say(BAD if broken else OK,
        f"깨진 링크 {len(broken)}건" if broken else "모든 링크가 실제 파일을 가리킨다",
        "\n".join(broken))

    # ── 순서는 알림만 ───────────────────────────────────────────────
    order = [n for line in lines if (m := ROW.match(line.strip())) for n in [m.group(1)]]
    if order != sorted(order):
        say(NA, "표가 번호순이 아니다",
            f"현재 순서: {' · '.join(order)}\n"
            "merge=union이 양쪽 줄을 남기면서 생길 수 있다. 표의 의미는 그대로라\n"
            "실패시키지 않는다 — 눈에 거슬리면 손으로 옮기면 된다.")
    else:
        say(OK, "표가 번호순이다")

    print("\n" + "═" * 72)
    print(f"확인 {_tally[OK]} · 문제 {_tally[BAD]} · 불가 {_tally[NA]}")
    print("═" * 72)
    if _tally[BAD]:
        print("\n실패 — 인덱스가 실제 ADR 파일과 어긋난다.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
