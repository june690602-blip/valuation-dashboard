"""프로젝트를 파일 하나(gemini_context.md)로 번들 — Gemini 등 외부 AI에게 '읽기 전용 상담'을 시키기 위함.

용도: 이 파일을 Gemini(gemini.google.com)의 **Gem 지식**으로 올려두면, 매번 프로젝트를
설명하지 않고 질문만 해도 구조·재무 로직·설계 결정을 보고 답한다. 코드 수정용이 아니라 Q&A용.

포함: CLAUDE.md·README·docs·전체 소스(app.py, src/, scripts/)·requirements + (있으면) 설계 결정 메모리.
제외(중요): API 키(.streamlit/secrets.toml, .env)·캐시·__pycache__·.git·.venv — 절대 번들에 넣지 않는다.

실행: python scripts/export_context.py  →  gemini_context.md 생성
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "gemini_context.md"

# 텍스트로 포함할 파일 확장자
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".cfg", ".ini"}
# 경로에 이 조각이 들어가면 통째로 제외 (보안·잡음)
EXCLUDE_PARTS = {
    "secrets.toml", ".env", ".git", ".venv", "__pycache__",
    "data/cache", ".streamlit/secrets", "gemini_context.md",
}
# 확실히 넣을 문서 (순서 고정 — 먼저 보여줄 것)
LEAD_FILES = ["CLAUDE.md", "README.md", "requirements.txt", "docs/사용설명서.md"]

# 설계 결정 메모리(우리가 내린 판단의 '이유'). 있으면 포함, 없으면 조용히 생략.
MEMORY_DIR = Path.home() / ".claude" / "projects" / "C--Users-----Desktop-----" / "memory"
MEMORY_FILES = ["MEMORY.md", "project-valuation-dashboard.md", "user-june-vibecoding.md"]


def _excluded(p: Path) -> bool:
    s = p.as_posix()
    return any(part in s for part in EXCLUDE_PARTS)


def _fence(path_label: str, text: str, lang: str = "") -> str:
    # 본문에 ``` 가 있어도 깨지지 않게 4중 백틱으로 감싼다.
    return f"\n### `{path_label}`\n\n````{lang}\n{text.rstrip()}\n````\n"


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def collect_repo_files() -> list[Path]:
    """LEAD_FILES 먼저, 그다음 소스 트리(app.py·src·scripts)를 정렬해 반환(중복 제거)."""
    seen: set[Path] = set()
    ordered: list[Path] = []

    for rel in LEAD_FILES:
        p = ROOT / rel
        if p.exists() and not _excluded(p):
            ordered.append(p)
            seen.add(p)

    for p in sorted(ROOT.rglob("*")):
        if p.is_dir() or _excluded(p) or p in seen:
            continue
        if p.suffix.lower() in TEXT_SUFFIXES:
            ordered.append(p)
            seen.add(p)
    return ordered


def build() -> str:
    lines: list[str] = []
    lines.append("# 투자지표 — 프로젝트 컨텍스트 팩 (Gemini 상담용)\n")
    lines.append(
        f"> 자동 생성: {datetime.now():%Y-%m-%d %H:%M}. "
        "`python scripts/export_context.py`로 갱신.\n"
    )
    lines.append(
        "\n## 너(Gemini)의 역할\n"
        "너는 이 **주식 가치평가 대시보드** 프로젝트의 상담역이다. 아래에 프로젝트 전체 소스와 "
        "설계 결정이 들어 있다. 사용자가 맥락을 다시 설명하지 않아도, 이 자료를 근거로 "
        "구조·재무 로직·데이터 소스·개선 방향 질문에 답하라. **코드를 직접 수정하지는 말고**(실제 "
        "구현은 다른 도구가 함), 판단 근거를 보여주되 단정하지 않는 톤을 유지하라. "
        "AI가 생성한 투자 관련 서술에는 '학습·분석 보조용' 성격을 상기시켜라.\n"
    )

    # 파일 트리
    files = collect_repo_files()
    lines.append("\n## 파일 목록\n")
    for p in files:
        lines.append(f"- `{p.relative_to(ROOT).as_posix()}`")
    lines.append("")

    # 설계 결정 메모리 (우리 대화에서 증류된 '이유')
    mem_blocks: list[str] = []
    for name in MEMORY_FILES:
        mp = MEMORY_DIR / name
        txt = _read(mp)
        if txt:
            mem_blocks.append(_fence(f"memory/{name}", txt, "markdown"))
    if mem_blocks:
        lines.append("\n## 설계 결정·맥락 (개발 과정에서 내린 판단의 이유)\n")
        lines.extend(mem_blocks)

    # 소스·문서 본문
    lines.append("\n## 소스 및 문서 전문\n")
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        txt = _read(p)
        if txt is None:
            continue
        lang = "python" if p.suffix == ".py" else ("markdown" if p.suffix == ".md" else "")
        lines.append(_fence(rel, txt, lang))

    return "\n".join(lines)


def _secret_values() -> list[str]:
    """secrets.toml·.env의 실제 키 '값'을 읽어온다(존재 시). 번들에 이 값이 섞였는지 검사용."""
    vals: list[str] = []
    for rel in (".streamlit/secrets.toml", ".env"):
        p = ROOT / rel
        txt = _read(p)
        if not txt:
            continue
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            # 자격증명 키만 대상(GEMINI_MODEL·포트 등 비밀 아닌 설정값 오탐 방지).
            if not any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PWD")):
                continue
            v = v.strip().strip('"').strip("'")
            if len(v) >= 8:
                vals.append(v)
    return vals


def main() -> None:
    content = build()
    # 안전장치: 실제 키 '값'이 번들에 섞였는지 검사(파일명 언급이 아니라 값 자체).
    # 표준 Gemini 키 접두사도 정적으로 확인하되, 이 스크립트가 번들에 포함될 때
    # 자기 자신을 오탐하지 않도록 needle을 런타임에 조합한다.
    gemini_prefix = "AIza" + "Sy"
    for danger in _secret_values() + [gemini_prefix]:
        if danger in content:
            raise SystemExit("[중단] 번들에 실제 키 값으로 보이는 문자열이 감지됨 — 업로드 금지, EXCLUDE 규칙 확인.")
    OUT.write_text(content, encoding="utf-8")
    kb = len(content.encode("utf-8")) / 1024
    print(f"생성 완료: {OUT.relative_to(ROOT)}  ({kb:.0f} KB, 약 {len(content)//3:,} 토큰 추정)")
    print("→ gemini.google.com → Gem 만들기 → 이 파일을 '지식'으로 업로드하면 끝.")


if __name__ == "__main__":
    main()
