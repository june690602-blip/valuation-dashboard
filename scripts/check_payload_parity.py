"""병렬화가 **출력을 바꾸지 않았는가** — analyze() 페이로드 전후 비교 (ADR-0045).

    python scripts/check_payload_parity.py save    KR 005930
    python scripts/check_payload_parity.py compare KR 005930

**네트워크가 필요하다.** CI 관문이 아니라 수동 진단이다.

## 왜 이 스크립트가 있나

병렬화에서 어려운 것은 빨라지는 쪽이 아니라 **아무것도 안 바뀌었음을 증명하는 쪽**이다.
빨라진 것은 초시계가 말해 주지만, 조용히 달라진 값은 아무도 말해 주지 않는다 — 순서가
바뀐 경고 한 줄, 다른 스레드가 먼저 채운 필드 하나는 화면에서 눈에 띄지 않는다.

그래서 합격선을 **"페이로드가 한 바이트도 안 바뀐다"**로 두고(HANDOFF-PERF.md §5),
바꾸기 **전에** 골든을 뜬다. 나중에 뜨면 무엇과 비교하는지가 사라진다.

## 한계 — 읽기 전에 알아야 한다

- **원천이 살아 있는 데이터다.** `data/cache/`의 TTL이 만료되면(시세 1시간·네이버 12시간·
  뉴스 6시간) 두 번째 실행이 **새 값**을 받아 온다. 그 차이는 병렬화 탓이 아니다.
  save와 compare를 **같은 시간대에** 돌려라. 시세 갱신으로 보이는 차이가 나오면
  캐시를 다시 채우고 한 번 더 재라.
- 그래서 이 검사는 **위증(false alarm)은 낼 수 있어도 묵인(false pass)은 어렵다** —
  경고 순서·필드 누락처럼 병렬화가 실제로 깨뜨리는 것들은 시간과 무관하게 잡힌다.
- 결정적(deterministic) 회귀는 `tests/test_load_parallel.py`가 맡는다. 이 스크립트는
  **실제 종목에서** 그 테스트가 못 보는 것(진짜 원천·진짜 순서)을 본다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = ROOT / "data" / "parity"
OK, BAD = "[확인]", "[문제]"

# 실행할 때마다 반드시 달라지는 값 — 비교에서 뺀다. **여기에 추가할 때는 이유를 적어라.**
# 이 목록이 길어지면 검사가 통과하는 이유가 '같아서'가 아니라 '안 봐서'가 된다.
VOLATILE = {
    "computed_at",   # 분석을 돌린 시각 — 정의상 매번 다르다
}


def payload(market: str, query: str, peers: int, no_news: bool) -> dict:
    from src.web.serialize import analyze
    return analyze(market, query, peer_count=peers, include_news=not no_news)


def normalize(obj):
    """json 왕복을 거쳐 타입을 고정한다 — 저장본과 새 값이 같은 자로 비교되게."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str, sort_keys=True))


def diff(a, b, path: str = "") -> list[str]:
    """서로 다른 자리를 `경로: 이전 → 이후`로 모은다(재귀). 순서 차이도 차이로 본다."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k in VOLATILE:
                continue
            sub = f"{path}.{k}" if path else k
            if k not in a:
                out.append(f"{sub}: (없음) → {short(b[k])}")
            elif k not in b:
                out.append(f"{sub}: {short(a[k])} → (없음)")
            else:
                out += diff(a[k], b[k], sub)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: 길이 {len(a)} → {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out
    if a != b:
        return [f"{path}: {short(a)} → {short(b)}"]
    return []


def short(v) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= 90 else s[:87] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["save", "compare"])
    ap.add_argument("market", nargs="?", default="KR", choices=["KR", "US"])
    ap.add_argument("query", nargs="?", default="005930")
    ap.add_argument("--peers", type=int, default=9)
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--limit", type=int, default=40, help="차이를 몇 줄까지 보일지")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{args.market}_{args.query}" + ("_nonews" if args.no_news else "")
    path = OUT_DIR / f"{tag}.json"

    got = normalize(payload(args.market, args.query, args.peers, args.no_news))

    if args.mode == "save":
        path.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True),
                        encoding="utf-8")
        print(f"{OK} 골든 저장 — {path.relative_to(ROOT)} "
              f"({path.stat().st_size / 1024:.0f}KB · {len(got)} 최상위 키)")
        return 0

    if not path.exists():
        print(f"{BAD} 골든이 없다 — 먼저 `save`로 뜨고 코드를 고쳐라: {path.relative_to(ROOT)}")
        return 1
    want = json.loads(path.read_text(encoding="utf-8"))
    rows = diff(want, got)
    if not rows:
        print(f"{OK} {args.market} {args.query} — 페이로드 완전 일치 "
              f"(제외: {', '.join(sorted(VOLATILE))})")
        return 0
    print(f"{BAD} {args.market} {args.query} — 다른 자리 {len(rows)}곳")
    for r in rows[: args.limit]:
        print(f"  {r}")
    if len(rows) > args.limit:
        print(f"  … 외 {len(rows) - args.limit}곳")
    print("\n※ 시세·컨센서스처럼 원천이 갱신됐을 수 있다(캐시 TTL). 도크스트링의 '한계'를 읽어라.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
