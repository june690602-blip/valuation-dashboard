"""백테스트 수집물을 옮길 수 있게 묶고 푼다 — 3,864개 파일 → 3개.

    python scripts/backtest_pack.py pack              # raw/ → pack/  (검증 포함)
    python scripts/backtest_pack.py unpack            # pack/ → raw/
    python scripts/backtest_pack.py manifest          # panel.parquet의 출처를 적는다
    python scripts/backtest_pack.py pack --market US

왜 필요한가
-----------
`data/backtest/`는 `.gitignore`에 있다(파생물 · 67MB · 3,864개 파일). 그래서 **다른 PC에서
분석을 이어받을 수 없고**, 판정 경로를 건드릴 때마다 수집 48분 + 패널 14분을 다시 낸다.

묶으면 파일이 **3개**가 되어 USB·클라우드로 옮길 수 있다. 압축률도 좋아진다 —
parquet은 파일마다 스키마·인덱스 오버헤드가 붙는데, 1,288개를 한 덩어리로 만들면
열 단위 압축이 제대로 걸린다.

**공개 저장소에 올리는 용도가 아니다.** 주가는 Yahoo·FinanceDataReader에서 받은 것이고
그 재배포는 원천 약관에 걸릴 수 있다. 이 스크립트는 **자기 기기 사이에 옮기는** 도구다.
저장소에 커밋하는 것은 `panel.parquet`(0.5MB · 파생 집계)뿐이다.

무엇이 보존되어야 하나
--------------------
`backtest_panel.py`의 `load_all()`이 읽는 것이 계약이다:

    meta_{code}.json      dict
    fin_{code}.parquet    index=회계연도(int) · `filed` 열 포함
    px_{code}.parquet     'close' 열 · 인덱스는 날짜로 변환 가능

`fin.attrs`(fs_div)는 `load_all()`이 쓰지 않고 meta에 같은 값이 있으므로 잃어도 된다.
meta는 parquet으로 만들면 None·bool·정수가 타입 강제를 받으므로 **JSON 한 덩어리로** 둔다.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DIRS = {"KR": "backtest", "US": "backtest_us"}
# zstd는 pyarrow에 기본 포함이고 snappy보다 크게 작아진다. 없으면 기본값으로 물러선다.
COMPRESSION = "zstd"
VERIFY_SAMPLE = 8


def _paths(market: str) -> tuple[Path, Path]:
    out = ROOT / "data" / DIRS[market]
    return out / "raw", out / "pack"


def _write(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path, compression=COMPRESSION)
    except Exception:                     # zstd가 없는 환경 — 기본 압축으로
        df.to_parquet(path)


def pack(market: str) -> int:
    raw, pk = _paths(market)
    metas = sorted(raw.glob("meta_*.json"))
    if not metas:
        print(f"{raw}에 수집물이 없다 — backtest_collect.py를 먼저 돌려라.")
        return 1
    pk.mkdir(parents=True, exist_ok=True)

    fins, pxs, meta_all = [], [], {}
    for mp in metas:
        code = mp.stem[5:]
        fp, xp = raw / f"fin_{code}.parquet", raw / f"px_{code}.parquet"
        if not (fp.exists() and xp.exists()):
            continue
        f = pd.read_parquet(fp).reset_index()
        f.insert(0, "code", code)
        fins.append(f)
        x = pd.read_parquet(xp).reset_index()
        x.insert(0, "code", code)
        pxs.append(x)
        meta_all[code] = json.loads(mp.read_text(encoding="utf-8"))

    fin = pd.concat(fins, ignore_index=True)
    px = pd.concat(pxs, ignore_index=True)
    _write(fin, pk / "fin.parquet")
    _write(px, pk / "px.parquet")
    (pk / "meta.json").write_text(
        json.dumps(meta_all, ensure_ascii=False), encoding="utf-8")

    before = sum(p.stat().st_size for p in raw.iterdir() if p.is_file())
    after = sum(p.stat().st_size for p in pk.iterdir() if p.is_file())
    print(f"묶음 완료 — {market}")
    print(f"  종목        {len(meta_all):,}곳")
    print(f"  재무 행     {len(fin):,}   주가 행 {len(px):,}")
    print(f"  파일 수     {len(list(raw.iterdir())):,}개 → {len(list(pk.iterdir()))}개")
    print(f"  크기        {before / 1e6:,.1f}MB → {after / 1e6:,.1f}MB"
          f"  ({after / before:.0%})")
    print(f"  위치        {pk}")
    return 0 if verify(market) else 1


def unpack(market: str, force: bool = False) -> int:
    raw, pk = _paths(market)
    if not (pk / "meta.json").exists():
        print(f"{pk}에 묶음이 없다 — pack을 먼저 돌리거나 파일을 옮겨 와라.")
        return 1
    if raw.exists() and any(raw.glob("meta_*.json")) and not force:
        print(f"{raw}에 이미 수집물이 있다. 덮어쓰려면 --force.")
        return 1
    raw.mkdir(parents=True, exist_ok=True)

    meta_all = json.loads((pk / "meta.json").read_text(encoding="utf-8"))
    fin = pd.read_parquet(pk / "fin.parquet")
    px = pd.read_parquet(pk / "px.parquet")
    fin_idx = [c for c in ("fiscal_year", "index") if c in fin.columns][0]
    px_idx = [c for c in ("Date", "index", "date") if c in px.columns][0]

    for code, g in fin.groupby("code", sort=False):
        f = g.drop(columns=["code"]).set_index(fin_idx)
        f.index.name = "fiscal_year"
        f.to_parquet(raw / f"fin_{code}.parquet")
    for code, g in px.groupby("code", sort=False):
        x = g.drop(columns=["code"]).set_index(px_idx)
        x.index.name = "Date"
        x[["close"]].to_parquet(raw / f"px_{code}.parquet")
    for code, m in meta_all.items():
        (raw / f"meta_{code}.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")

    print(f"풀기 완료 — {market} · 종목 {len(meta_all):,}곳 → {raw}")
    return 0 if verify(market) else 1


def verify(market: str) -> bool:
    """묶음을 임시로 풀어 원본과 대조한다. **묶고 나서 반드시 돈다.**

    묶는 코드와 푸는 코드가 갈리면 조용히 다른 데이터가 되고, 그것을 다른 PC에서
    발견하게 된다. 그때는 원본이 없다.
    """
    raw, pk = _paths(market)
    if not (pk / "meta.json").exists() or not any(raw.glob("meta_*.json")):
        return True
    meta_all = json.loads((pk / "meta.json").read_text(encoding="utf-8"))
    raw_codes = {p.stem[5:] for p in raw.glob("meta_*.json")}
    if set(meta_all) != raw_codes:
        print(f"  [문제] 종목 집합이 다르다 — 묶음 {len(meta_all)} vs 원본 {len(raw_codes)}")
        return False

    fin = pd.read_parquet(pk / "fin.parquet")
    px = pd.read_parquet(pk / "px.parquet")
    fin_idx = [c for c in ("fiscal_year", "index") if c in fin.columns][0]
    px_idx = [c for c in ("Date", "index", "date") if c in px.columns][0]
    sample = random.Random(0).sample(sorted(raw_codes), min(VERIFY_SAMPLE, len(raw_codes)))
    for code in sample:
        f0 = pd.read_parquet(raw / f"fin_{code}.parquet")
        x0 = pd.read_parquet(raw / f"px_{code}.parquet")
        f1 = (fin[fin["code"] == code].drop(columns=["code"])
              .set_index(fin_idx).rename_axis(f0.index.name))
        x1 = (px[px["code"] == code].drop(columns=["code"])
              .set_index(px_idx).rename_axis(x0.index.name))[["close"]]
        if len(f0) != len(f1) or len(x0) != len(x1):
            print(f"  [문제] {code} 행수 불일치 "
                  f"(재무 {len(f0)}/{len(f1)} · 주가 {len(x0)}/{len(x1)})")
            return False
        if not x0["close"].to_numpy().round(6).tolist() == \
                x1["close"].to_numpy().round(6).tolist():
            print(f"  [문제] {code} 주가 값이 다르다")
            return False
    print(f"  [확인] 왕복 검증 통과 — 종목 집합 일치 · 표본 {len(sample)}곳 값 일치")
    return True


def manifest(market: str) -> int:
    """`panel.parquet`이 무엇으로 언제 만들어졌는지 적는다.

    이 파일은 저장소에 커밋하는 **유일한 파생물**이라(0.5MB) 스테일 여부를 알 수 있어야
    한다. 판정 코드가 바뀌면 패널도 다시 만들어야 하는데, 커밋된 parquet은 말없이
    옛 코드의 결과로 남는다. 그때 이 매니페스트의 커밋 해시가 어긋남을 알려 준다.
    """
    out = ROOT / "data" / DIRS[market]
    p = out / "panel.parquet"
    if not p.exists():
        print(f"{p}가 없다 — backtest_panel.py를 먼저 돌려라.")
        return 1
    df = pd.read_parquet(p)
    raw, _ = _paths(market)
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                              capture_output=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain",
                                     "scripts/backtest_panel.py",
                                     "src/analysis"], cwd=ROOT, text=True,
                                    capture_output=True, check=True).stdout.strip())
    except Exception:
        head, dirty = "unknown", None
    man = {
        "market": market,
        "rows": int(len(df)),
        "dates": sorted(str(pd.Timestamp(d).date()) for d in df["date"].unique()),
        "codes": int(df["code"].nunique()),
        "columns": list(df.columns),
        "raw_stocks_at_build": len(list(raw.glob("meta_*.json"))) if raw.exists() else None,
        "built_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "code_commit": head,
        "code_dirty_at_build": dirty,
        "note": ("판정 경로(src/analysis)나 backtest_panel.py가 이 커밋 이후 바뀌었으면 "
                 "이 패널은 스테일이다. raw/를 풀고 backtest_panel.py를 다시 돌려라."),
    }
    mp = out / "panel_manifest.json"
    mp.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"매니페스트 → {mp}")
    print(f"  {man['rows']:,}행 · 시점 {len(man['dates'])}개 · 종목 {man['codes']:,}곳"
          f" · 커밋 {head[:8]}" + (" (더러움)" if dirty else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("pack", "unpack", "verify", "manifest"))
    ap.add_argument("--market", default="KR", choices=tuple(DIRS))
    ap.add_argument("--force", action="store_true", help="unpack: 기존 raw/를 덮어쓴다")
    a = ap.parse_args()
    if a.action == "pack":
        return pack(a.market)
    if a.action == "unpack":
        return unpack(a.market, a.force)
    if a.action == "manifest":
        return manifest(a.market)
    return 0 if verify(a.market) else 1


if __name__ == "__main__":
    raise SystemExit(main())
