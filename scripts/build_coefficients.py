"""①의 회귀 계수를 만들어 파일로 낸다 — GitHub Actions가 밤에 부르는 것 (이슈 #131).

    python scripts/build_coefficients.py --out dist/coefficients

## 왜 이 스크립트가 따로 있나

`get_coefficients()`를 부르면 안 된다. 그쪽은 **저장소에 구워진 파일을 먼저 읽으므로**
(`_from_repo`), 이 스크립트가 그것을 부르면 어제 만든 파일을 그대로 다시 써서
**계수가 영원히 갱신되지 않는다.** 여기서는 수집과 적합을 직접 부른다.

## 무엇이 나오나

    KR.json    {다리: {계수}}  — `_coefficients_usable`이 검사하는 그 모양 그대로
    US.json
    meta.json  언제·무엇으로 만들었나 (사람이 읽는 용도 · 서버는 로그에만 쓴다)

`KR.json`을 계수 dict **그대로** 두는 것이 중요하다. 여기에 `built_at` 같은 키를 얹으면
`_coefficients_usable`이 그 문자열을 다리로 보고 검사에 실패한다 — 그러면 서버가
"쓸 수 없는 계수"로 판단해 조용히 전 종목 수집으로 물러난다. 메타는 **다른 파일**에 둔다.

## 실패를 어떻게 다루나

시장 하나가 실패해도 나머지는 낸다. 무료 원천은 자주 부분적으로 죽는데
(yfinance 실측 성공률 62%), 한쪽 실패로 양쪽을 다 버리면 그날 배포에 계수가 없다.
**두 시장 다 실패했을 때만** 0이 아닌 코드로 끝낸다 — 그래야 워크플로가 빨간불이 되고,
이전 파일은 브랜치에 그대로 남는다(force-push를 안 하므로).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OK, BAD = "[확인]", "[문제]"


def build(market: str) -> tuple[dict | None, int]:
    """(계수, 표본 행 수). 실패하면 (None, 0)."""
    from src.data.universe_multiples import (build_coefficients, collect_kr,
                                             collect_us)
    from src.data.universe_multiples import _coefficients_usable

    snap = collect_kr() if market == "KR" else collect_us()
    coef = build_coefficients(snap)
    if not _coefficients_usable(coef):
        print(f"{BAD} {market}: 계수가 스키마 검사를 통과하지 못했다 — 내보내지 않는다.")
        return None, len(snap)
    return coef, len(snap)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist/coefficients")
    ap.add_argument("--markets", nargs="*", default=["KR", "US"])
    a = ap.parse_args()

    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    meta = {"built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "markets": {}}
    made = 0

    for market in a.markets:
        try:
            coef, rows = build(market)
        except Exception as e:  # noqa: BLE001 — 한 시장 실패가 다른 시장을 막지 않는다
            print(f"{BAD} {market}: {type(e).__name__}: {e}")
            meta["markets"][market] = {"ok": False, "error": f"{type(e).__name__}"}
            continue
        if coef is None:
            meta["markets"][market] = {"ok": False, "error": "schema"}
            continue
        (out / f"{market}.json").write_text(
            json.dumps(coef, ensure_ascii=False, indent=1), encoding="utf-8")
        meta["markets"][market] = {"ok": True, "rows": rows, "legs": sorted(coef)}
        made += 1
        print(f"{OK} {market}: 표본 {rows:,}행 · 다리 {sorted(coef)}")

    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    if made == 0:
        print(f"\n{BAD} 두 시장 모두 실패했다 — 이전 파일을 덮어쓰지 않도록 실패로 끝낸다.")
        return 1
    print(f"\n{OK} {made}개 시장 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
