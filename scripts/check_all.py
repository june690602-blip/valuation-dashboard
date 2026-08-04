"""CI가 PR마다 돌리는 것을 **그대로** 로컬에서 돌린다.

    python scripts/check_all.py            # 전부
    python scripts/check_all.py --list     # 무엇을 돌릴지만 보여준다

## 왜 이 스크립트가 있나

관문 목록을 손으로 베껴 적으면 언젠가 어긋난다. 실제로 어긋났다 — ADR-0014 Task 9가
`web/assets/stock.js`의 인라인 스타일 예산(308건)을 5건 넘겼는데, 그때 돌린 것이
`check_design.py`뿐이라 아무도 몰랐다. ADR-0015가 2건을 더 얹은 뒤에야 **CI에서** 터졌다.
`check_design.py`가 통과했다는 사실이 오히려 "웹 변경은 괜찮다"는 잘못된 확신을 줬다.

그래서 이 스크립트는 목록을 갖고 있지 않다. **`.github/workflows/quality.yml`을 읽어서
거기 적힌 명령을 실행한다.** 워크플로에 관문이 추가되면 여기도 자동으로 따라간다 —
베껴 적은 목록이 아니라 원본을 본다.

## 판정할 수 없는 것

- **네트워크가 필요한 진단은 CI에도 없고 여기에도 없다.** `check_analysis.py`·
  `check_warranted.py`·`check_normalized.py`·`check_size_bias.py`·
  `check_valuation_basis.py`는 수동 계열이다. 이 스크립트가 통과해도 그것들은 따로 돌려야 한다.
- **`pip install` 단계는 건너뛴다.** 로컬 가상환경을 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"
OK, BAD, NA = "[확인]", "[문제]", "[불가]"

# 로컬에서 돌릴 이유가 없는 단계 — 의존성 설치는 가상환경을 건드린다.
SKIP_PREFIXES = ("python -m pip install",)


def steps() -> list[tuple[str, str]]:
    """(단계 이름, 명령) — quality.yml의 run 블록을 순서대로. YAML 라이브러리를 쓰지 않는다
    (새 의존성을 들이지 않으려는 것이고, 이 워크플로는 구조가 단순하다)."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    out, name = [], ""
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"\s*-?\s*name:\s*(.+?)\s*$", ln)
        if m:
            name = m.group(1)
        m = re.match(r"(\s*)run:\s*(.*)$", ln)
        if m:
            indent, rest = len(m.group(1)), m.group(2).strip()
            if rest == "|":
                body, i = [], i + 1
                while i < len(lines):
                    nxt = lines[i]
                    if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                        break
                    if nxt.strip():
                        body.append(nxt.strip())
                    i += 1
                for cmd in body:
                    out.append((name, cmd))
                continue
            if rest:
                out.append((name, rest))
        i += 1
    return [(n, c) for n, c in out if not c.startswith(SKIP_PREFIXES)]


def run(cmd: str) -> tuple[int, str]:
    """명령 하나를 돌린다. `python …`은 지금 인터프리터로 바꿔 가상환경을 타게 한다."""
    if cmd.startswith("python "):
        args = [sys.executable] + cmd.split()[1:]
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    bash = shutil.which("bash")
    if not bash:
        return -1, "bash를 찾지 못해 건너뛴다 (셸 명령)"
    p = subprocess.run([bash, "-lc", cmd], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="실행하지 않고 목록만")
    args = ap.parse_args()

    plan = steps()
    if not plan:
        print(f"{BAD} {WORKFLOW.relative_to(ROOT)}에서 run 단계를 찾지 못했다.")
        return 1

    print(f"CI 관문 {len(plan)}개 — 출처 {WORKFLOW.relative_to(ROOT)}\n")
    if args.list:
        for n, c in plan:
            print(f"  {n:<32} {c}")
        return 0

    bad = skipped = 0
    for n, c in plan:
        rc, out = run(c)
        if rc == -1:
            skipped += 1
            print(f"  {NA} {n}\n         {c}\n         {out.strip()}")
            continue
        tail = [x for x in out.splitlines() if x.strip()][-1:] or [""]
        if rc == 0:
            print(f"  {OK} {n:<32} {tail[0][:60]}")
        else:
            bad += 1
            print(f"  {BAD} {n}\n         $ {c}")
            for line in out.splitlines()[-25:]:
                print(f"         {line}")

    print(f"\n{'─' * 60}")
    print(f"통과 {len(plan) - bad - skipped} · 실패 {bad} · 건너뜀 {skipped}")
    if bad:
        print("\nCI도 같은 이유로 실패한다. 고치고 다시 돌릴 것.")
    else:
        print("\nCI가 보는 것은 전부 통과했다. 네트워크가 필요한 진단(check_analysis·\n"
              "check_warranted·check_normalized·check_valuation_basis)은 따로 돌려야 한다.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
