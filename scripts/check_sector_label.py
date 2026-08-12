"""회귀에 들어가는 업종 라벨이 계수표와 같은 자인가 (ADR-0044).

    python scripts/check_sector_label.py            # 캐시된 AI 라벨 전수 대조 (네트워크 불필요)
    python scripts/check_sector_label.py --live 005930 000660   # 실제 종목 전후 비교

## 무엇을 재나

①⑤의 적정 배수는 `log(배수) = α + β·log(시총) + ΣROE더미 + Σ업종더미` 회귀에서 나오고
(ADR-0014), 그 계수표는 **KRX 표준산업분류(KR) · GICS 섹터(US)** 문자열로 적합된다.
`warranted_multiple`은 이렇게 조회한다::

    sec = sector if sector in coef["sector_coef"] else OTHER_SECTOR

**못 찾으면 예외도 경고도 없이 '기타'로 떨어진다.** 그래서 조회하는 문자열이 계수표와
같은 자인지 **밖에서 세어 보는 것**이 이 스크립트다.

## 왜 문제였나

`kr_provider`·`us_provider`는 Gemini 업종분류가 성공하면 `sector`를 AI가 지은 이름으로
덮어썼다(피어 선정용). 그 라벨이 회귀 조회까지 새어 들어가 전부 '기타'가 됐다.
ADR-0044가 `sector_official`을 갈라 고쳤고, 이 스크립트가 그 상태를 지킨다.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.data.universe_multiples import coefficients_or_none  # noqa: E402


def audit_cached_ai_labels(market: str) -> int:
    """캐시에 쌓인 AI 업종 문자열이 계수표에 몇 개나 있나 — 네트워크 없이 돈다."""
    coef = coefficients_or_none(market)
    if not coef or "per" not in coef:
        print(f"[{market}] 계수표가 없다 — 먼저 계수를 구워라(scripts/bake_coefficients.py).")
        return 1
    fitted = set(coef["per"]["sector_coef"])

    labels: set[str] = set()
    for p in glob.glob(str(ROOT / "data" / "cache" / "ai_peers_*.json")):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("sector"):
            labels.add(str(d["sector"]))

    print(f"[{market}] 계수표 업종 {len(fitted)}개 · 캐시된 AI 라벨 {len(labels)}개")
    if not labels:
        print("  AI 라벨 캐시가 비어 있다 — 이 대조는 건너뛴다(Gemini 키 없이 돌았을 수 있다).")
        return 0
    hit = sorted(labels & fitted)
    miss = sorted(labels - fitted)
    print(f"  계수표와 일치: {len(hit)}개 {hit if hit else ''}")
    print(f"  일치하지 않음: {len(miss)}개 — 이 라벨로 조회하면 전부 '기타'로 떨어진다")
    for m in miss[:10]:
        print(f"      {m!r}")
    if len(miss) > 10:
        print(f"      … 외 {len(miss) - 10}개")
    print("\n  ⚠ 이 목록이 비어 있어야 하는 것이 **아니다.** AI 라벨은 원래 계수표와 다른 어휘다.")
    print("  요점은 **그 라벨을 회귀에 넣지 않는 것**이고, 그것은 아래 --live가 확인한다.")
    return 0


def audit_live(codes: list[str], market: str) -> int:
    """실제 종목을 돌려 회귀에 들어간 라벨이 계수표에 있는지 확인한다(네트워크 필요)."""
    from src.analysis.valuation import regression_sector
    from src.web.serialize import _defaults, _pipeline

    coef = coefficients_or_none(market)
    fitted = set(coef["per"]["sector_coef"]) if coef and "per" in coef else set()
    rf, mrp = _defaults(market)

    print(f"\n{'종목':<14}{'표시 sector(AI)':<20}{'회귀 sector(공식)':<26}{'표에있나':<8}")
    print("-" * 72)
    bad = 0
    for code in codes:
        try:
            d, *_ = _pipeline(market, code, 9, rf, mrp, (), ())
        except Exception as e:  # noqa: BLE001
            print(f"{code:<14}로드 실패: {type(e).__name__}: {e}")
            bad += 1
            continue
        used = regression_sector(d)
        ok = used in fitted
        if not ok:
            bad += 1
        print(f"{d.name:<14}{str(d.sector)[:18]:<20}{str(used)[:24]:<26}{'✓' if ok else '✗ 기타':<8}")
    print()
    if bad:
        print(f"[문제] {bad}개 종목이 계수표에 없는 라벨로 회귀를 조회한다 — ADR-0044를 읽어라.")
        return 1
    print("[확인] 회귀에 들어간 라벨이 전부 계수표에 있다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    ap.add_argument("--live", nargs="*", default=None,
                    help="실제 종목 코드들 — 네트워크가 필요하다")
    args = ap.parse_args()
    rc = audit_cached_ai_labels(args.market)
    if args.live:
        rc |= audit_live(args.live, args.market)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
