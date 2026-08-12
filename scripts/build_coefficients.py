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

## 실패를 어떻게 다루나 — **못 만든 것을 파괴하지 않는다** (ADR-0049)

이 문단은 예전에 이렇게 적혀 있었다:

> 시장 하나가 실패해도 나머지는 낸다. … 한쪽 실패로 양쪽을 다 버리면 안 된다.
> **두 시장 다 실패했을 때만** 0이 아닌 코드로 끝낸다 — 이전 파일은 브랜치에 그대로 남는다.

**의도는 맞았고 실제는 정반대였다.** publish 단계가 출력 폴더를 **orphan force-push**하므로,
폴더에 없는 파일은 **브랜치에서도 지워진다.** 2026-08-12 밤에 KR이 KRX 차단(`Access Denied`)
으로 실패했고, 워크플로는 초록불로 끝나면서 **멀쩡하던 어제치 `KR.json`까지 지웠다.**
그 뒤 서버는 404를 받아 전 종목 수집으로 물러났다(로컬 실측 263.85초 · 네이버 2,701회).
판정 가중의 77%(①38.5+⑤38.5)가 그 계수에 걸려 있다.

이제 이렇게 한다:

1. 워크플로가 **브랜치의 현재 내용을 `--out`에 먼저 깔아 준다**(seed).
2. 이 스크립트는 만든 것만 덮어쓴다 — 못 만든 시장은 **어제 파일이 그대로 남는다.**
3. 이어받은 시장은 `meta.json`에 `carried_over: true`와 **원래 만든 시각**을 적는다.
   안 적으면 그 파일이 얼마나 낡았는지 아무도 모른다.

끝내는 코드 셋:

    0  모든 시장을 **이번에** 새로 만들었다
    2  일부를 이어받았다 — publish는 해야 하고(좋은 쪽이 갱신됐다) **워크플로는 빨간불**이어야 한다
    1  쓸 수 있는 세트가 아예 없다 — publish 하면 안 된다
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 테스트가 이 파일을 import 한다 — 그때 stdout은 pytest의 캡처 객체라 reconfigure가 없다.
getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8")

OK, BAD = "[확인]", "[문제]"


def build(market: str) -> tuple[dict | None, int]:
    """(계수, 표본 행 수). 실패하면 (None, 0)."""
    from src.data.universe_multiples import (build_coefficients, collect_kr,
                                             collect_us)
    from src.data.universe_multiples import _coefficients_usable

    snap = collect_kr() if market == "KR" else collect_us()
    coef = build_coefficients(snap)
    if not _coefficients_usable(coef):
        # **얼마나 모자란지 함께 찍는다**(ADR-0051). "통과하지 못했다"만으로는 다리가
        # 아예 안 나온 것인지 하나만 빠진 것인지 알 수 없어, 며칠을 봐도 진단이 안 된다.
        got = " · ".join(f"{k}={(v or {}).get('n')}" for k, v in sorted(coef.items()))
        print(f"{BAD} {market}: 계수가 스키마 검사를 통과하지 못했다 — 내보내지 않는다. "
              f"(얻은 다리: {got or '없음'} · 스냅숏 {len(snap):,}행)")
        return None, len(snap)
    return coef, len(snap)


def _previous(out: Path) -> dict:
    """`--out`에 이미 깔려 있는(=브랜치에서 이어받은) 이전 meta. 없으면 빈 dict."""
    try:
        return json.loads((out / "meta.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 없거나 깨졌으면 '이전이 없다'와 같다
        return {}


def _thinned(market: str, out: Path, coef: dict) -> list:
    """새 계수가 **브랜치에 있는 이전 계수보다 크게 얇은가**. 이전이 없으면 비교하지 않는다."""
    from src.data.universe_multiples import thinned_legs

    path = out / f"{market}.json"
    if not path.exists():
        return []
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 못 읽으면 비교할 이전이 없는 것과 같다
        return []
    return thinned_legs(coef, old)


def _carry_over(market: str, out: Path, prev: dict, why: str) -> dict | None:
    """이번에 못 만든 시장 — 이어받을 파일이 있으면 그 사실을 meta에 적어 돌려준다."""
    if not (out / f"{market}.json").exists():
        print(f"{BAD} {market}: 이어받을 이전 파일도 없다 — 이 시장은 비어 있게 된다.")
        return None
    was = (prev.get("markets", {}).get(market) or {})
    since = was.get("built_at") or prev.get("built_at") or "알 수 없음"
    print(f"{BAD} {market}: 이번에 못 만들어 **이전 파일을 이어받는다** (원래 만든 시각: {since})")
    return {"ok": False, "error": why, "carried_over": True, "built_at": since,
            "rows": was.get("rows"), "legs": was.get("legs")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist/coefficients")
    ap.add_argument("--markets", nargs="*", default=["KR", "US"])
    a = ap.parse_args()

    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    prev = _previous(out)          # 워크플로가 브랜치 내용을 미리 깔아 둔다
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    meta = {"built_at": now, "markets": {}}
    made, carried, missing = 0, 0, 0

    for market in a.markets:
        why = None
        try:
            coef, rows = build(market)
        except Exception as e:  # noqa: BLE001 — 한 시장 실패가 다른 시장을 막지 않는다
            print(f"{BAD} {market}: {type(e).__name__}: {e}")
            coef, rows, why = None, 0, f"{type(e).__name__}"
        if coef is not None:
            # 모양은 맞는데 **표본이 얇으면** 받지 않는다(ADR-0050). 레이트리밋에 걸린
            # 빌드가 두꺼운 계수를 조용히 덮어쓰는 것을 막는다 — 기둥 다리(per·pbr)만 본다.
            thin = _thinned(market, out, coef)
            if thin:
                from src.data.universe_multiples import MIN_LEG_RATIO
                for leg, a, b in thin:
                    print(f"{BAD} {market}: {leg} 표본이 {a:,}으로 이전 {b:,}의 "
                          f"{a / b:.0%}다({MIN_LEG_RATIO:.0%} 미만) — 수집이 깨진 것으로 본다.")
                coef, why = None, "thin"
        if coef is None:
            entry = _carry_over(market, out, prev, why or "schema")
            if entry is not None:
                meta["markets"][market] = entry
                carried += 1
            else:
                meta["markets"][market] = {"ok": False, "error": why or "schema"}
                missing += 1
            continue
        (out / f"{market}.json").write_text(
            json.dumps(coef, ensure_ascii=False, indent=1), encoding="utf-8")
        meta["markets"][market] = {"ok": True, "rows": rows, "legs": sorted(coef),
                                   "leg_n": {k: (coef[k] or {}).get("n") for k in sorted(coef)},
                                   "built_at": now}
        made += 1
        # **다리별 표본 수를 찍는다**(ADR-0051). 이것이 CI 캐시가 실제로 먹었는지 보는
        # 유일한 창이다 — 얇은 빌드는 거부돼 파일로 남지 않으므로 로그가 아니면 알 수 없다.
        legs = " · ".join(f"{k}={(coef[k] or {}).get('n')}" for k in sorted(coef))
        print(f"{OK} {market}: 표본 {rows:,}행 · {legs}")

    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    if made + carried == 0:
        print(f"\n{BAD} 낼 수 있는 시장이 없다 — publish 하면 안 된다(이전 파일이 지워진다).")
        return 1
    if carried or missing:
        # ⚠ `missing`을 여기 넣는 것이 중요하다. 이어받을 파일조차 없는 시장(=지금의 KR)이
        # 초록으로 끝나면 **비어 있는 채로 아무도 모른다.** 그것이 이 사고의 시작이었다.
        print(f"\n{BAD} 새로 만든 {made}개 · 이어받은 {carried}개 · **비어 있는 {missing}개** → {out}")
        print("     publish는 해야 한다(좋은 쪽이 갱신됐다). 워크플로는 빨간불로 끝난다.")
        return 2
    print(f"\n{OK} {made}개 시장 전부 새로 만들었다 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
