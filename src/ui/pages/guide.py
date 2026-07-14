"""처음 쓰는 사용자를 위한 투자지표 사용설명서.

설명서를 별도 탭에 열어 둔 채 실제 화면을 따라갈 수 있도록, 기능 목록보다
의사결정 순서와 오해하기 쉬운 지표를 먼저 설명한다.
"""
from __future__ import annotations

import streamlit as st


GUIDE_CSS = """
<style>
.guide-hero {
  border: 1px solid var(--line); border-left: 4px solid var(--navy);
  border-radius: 12px; padding: 1.35rem 1.5rem 1.25rem; margin: .35rem 0 1.35rem;
  background: linear-gradient(135deg, #fff 0%, #f7f9fc 100%);
}
.guide-hero .overline, .guide-overline {
  margin: 0 0 .35rem; color: var(--navy); font-size: .7rem; font-weight: 750;
  letter-spacing: .14em; text-transform: uppercase;
}
.guide-hero h1 { margin: 0 0 .55rem; font-size: clamp(1.35rem, 2.2vw, 1.85rem); }
.guide-hero p { margin: 0; color: var(--ink2); line-height: 1.75; max-width: 68rem; }
.guide-actions { display: flex; flex-wrap: wrap; gap: .55rem; margin-top: 1rem; }
.guide-action {
  min-height: 42px; display: inline-flex; align-items: center; justify-content: center;
  padding: .55rem .9rem; border: 1px solid #c9cbd1; border-radius: 10px;
  color: var(--ink) !important; background: #fff; font-size: .88rem; font-weight: 650;
  text-decoration: none !important;
}
.guide-action:hover { border-color: var(--navy); color: var(--navy) !important; }
.guide-action.primary { background: var(--navy); border-color: var(--navy); color: #fff !important; }
.guide-step {
  height: 100%; min-height: 148px; padding: 1rem 1.05rem; border: 1px solid var(--line);
  border-radius: 12px; background: var(--surface); box-shadow: 0 1px 2px rgba(23,32,52,.035);
}
.guide-step .num {
  display: inline-flex; width: 1.7rem; height: 1.7rem; align-items: center; justify-content: center;
  border-radius: 999px; background: #edf3fb; color: var(--navy); font-weight: 750; font-size: .76rem;
}
.guide-step h3 { margin: .7rem 0 .3rem; font-size: .98rem; }
.guide-step p { margin: 0; color: var(--ink2); line-height: 1.7; font-size: .88rem; }
.guide-rule {
  border-top: 1px solid var(--line); padding-top: .8rem; margin-top: .8rem;
  color: var(--ink2); font-size: .88rem; line-height: 1.7;
}
.guide-rule strong { color: var(--ink); }
.guide-check {
  border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.1rem;
  background: #fbfbfa; color: var(--ink2); line-height: 1.8;
}
.guide-check strong { color: var(--ink); }
.guide-mini { color: var(--muted); font-size: .8rem; line-height: 1.65; }
@media (max-width: 700px) {
  .guide-hero { padding: 1.1rem; }
  .guide-action { width: 100%; min-height: 44px; }
  .guide-step { min-height: auto; }
}
</style>
"""


def _open_links(*items: tuple[str, str, bool]) -> None:
    """같은 앱의 화면을 별도 탭에 여는 버튼형 링크 묶음."""
    links = []
    for label, href, primary in items:
        kind = " primary" if primary else ""
        links.append(
            f'<a class="guide-action{kind}" href="{href}" target="_blank" '
            f'rel="noopener">{label} <span aria-hidden="true">↗</span></a>'
        )
    st.markdown(f'<div class="guide-actions">{"".join(links)}</div>', unsafe_allow_html=True)


def _step_card(number: int, title: str, body: str) -> str:
    return (
        '<div class="guide-step">'
        f'<span class="num" aria-hidden="true">{number}</span>'
        f'<h3>{title}</h3><p>{body}</p></div>'
    )


def _render_quick_start() -> None:
    st.markdown("## 5분 빠른 시작")
    st.caption("처음에는 모든 숫자를 읽지 않아도 됩니다. 아래 네 단계만 한 번 따라 해보세요.")
    cols = st.columns(4)
    cards = [
        (1, "분석 대상을 고릅니다", "주식·채권·포트폴리오 중 지금 답을 얻고 싶은 화면을 새 탭으로 엽니다."),
        (2, "입력값을 확인합니다", "주식은 시장과 종목, 채권은 국가·만기, 포트폴리오는 자산과 비중을 먼저 맞춥니다."),
        (3, "결론부터 읽습니다", "판정이나 핵심 지표를 먼저 본 뒤, 그 결론을 만든 근거 화면으로 내려갑니다."),
        (4, "반대 근거를 찾습니다", "신뢰도·표본 수·현금흐름·공시와 뉴스가 결론을 흔들지 않는지 확인합니다."),
    ]
    for col, (number, title, body) in zip(cols, cards):
        col.markdown(_step_card(number, title, body), unsafe_allow_html=True)

    _open_links(
        ("주식 가치평가 열기", "stock", True),
        ("채권 열기", "bond", False),
        ("포트폴리오 열기", "portfolio", False),
        ("투자성향 테스트가 있는 홈 열기", "home", False),
    )
    st.caption(
        "링크는 새 탭에서 새 분석으로 열립니다. 기존 입력값이나 테스트 결과를 이어 쓰려면 "
        "처음 열어 둔 분석 탭의 왼쪽 메뉴로 이동하세요."
    )


def _render_stock_guide() -> None:
    st.markdown("#### 권장 순서: 결론 → 근거 → 반대 근거")
    st.markdown(
        """
1. **종목을 불러옵니다.** 한국 주식은 6자리 코드 또는 종목명, 미국 주식은 티커를 입력합니다. 종목명·현재가·판정이 보이면 준비 완료입니다.
2. **요약·판정**에서 적정가 범위, 괴리율, 신뢰도를 먼저 봅니다. 괴리율 `+`는 모형 적정가가 현재가보다 높고, `−`는 낮다는 뜻입니다.
3. **밸류에이션 → 재무 분석 → 업종 비교**에서 ‘싸 보이는 이유’가 이익·현금흐름·경쟁력으로 뒷받침되는지 확인합니다.
4. **주가차트**에서 기간별 추세와 시장 대비 성과를 봅니다. 과거 상대성과는 향후 수익 예측이 아닙니다.
5. **자본비용 → 백테스트 → 기업·뉴스**에서 반대 근거를 찾습니다. 백테스트는 역사적 밴드 신호 하나만 점검하며 종합 판정 전체를 검증하지 않습니다.
"""
    )
    st.info(
        "신뢰도는 여러 평가 방법이 얼마나 비슷한 값을 냈는지를 보여줍니다. "
        "데이터 정확도나 수익 가능성을 보증하는 점수가 아닙니다.",
        icon="ℹ️",
    )

    with st.expander("9개 분석 화면 지도", expanded=False):
        st.markdown(
            """
| 알고 싶은 것 | 화면 | 먼저 볼 것 |
|---|---|---|
| 이 회사는 무엇을 하나? | 기업·뉴스 | 사업 개요와 최근 이슈 |
| 결론은 무엇인가? | 요약·판정 | 적정가 범위·괴리율·신뢰도 |
| 가격 흐름은 어떤가? | 주가차트 | 기간별 추세·지수 대비 성과 |
| 업종·과거 대비 싼가? | 밸류에이션 | 업종·자기 역사·RIM의 방향 |
| 회사 체력은 괜찮은가? | 재무 분석 | 매출·마진·현금흐름 |
| 경쟁사 중 어디쯤인가? | 업종 비교 | 가치와 수익성의 상대 위치 |
| 자본비용보다 더 버는가? | 자본비용 | ROIC와 WACC의 차이 |
| 역사적 밴드 신호가 과거에 통했나? | 백테스트 | 표본 수와 이후 수익 분포 |
| AI가 계산 결과를 어떻게 해석하나? | 종합 평가 | 스탠스·가격선·편입비중과 그 근거 |
"""
        )
        st.caption("색만으로 판단하지 말고 ‘업종 대비 낮음/높음’ 문구와 실제 수치를 함께 확인하세요.")

    _open_links(
        ("주식 화면 열고 005930 입력", "stock", True),
        ("주식 화면 열고 AAPL 입력", "stock", False),
    )


def _render_bond_guide() -> None:
    st.markdown("#### 금리 수준보다 ‘변화에 얼마나 민감한가’를 봅니다")
    st.markdown(
        """
1. **한국 또는 미국**을 고르고 수익률곡선의 모양을 확인합니다. 우상향·평탄화·역전은 경기와 정책 기대가 만기별 금리에 반영된 결과입니다.
2. 관심 만기의 **현재 금리와 과거 범위**를 비교합니다. 한 시점의 금리만으로 방향을 단정하지 마세요.
3. 금리 시나리오에서 **듀레이션과 볼록성**을 봅니다. 듀레이션은 금리 1%p 변화에 대한 가격 민감도의 1차 근사이고, 볼록성은 큰 변화에서 그 오차를 보정합니다.
"""
    )
    st.warning(
        "수익률과 가격은 대체로 반대로 움직입니다. 듀레이션 결과는 세금·신용위험·중도매매 비용을 포함한 실제 수익률이 아닙니다.",
        icon="⚠️",
    )
    _open_links(("채권 화면 새 탭으로 열기", "bond", True))


def _render_portfolio_guide() -> None:
    st.markdown("#### 수익률 하나가 아니라 비중·위험·분산을 함께 봅니다")
    st.markdown(
        """
1. 실제 보유 비중 또는 검토 중인 비중으로 자산을 담고, **합계가 100%인지** 확인합니다.
2. 기대수익과 변동성뿐 아니라 **자산 간 상관관계**를 함께 봅니다. 비슷하게 움직이는 자산이 많으면 종목 수가 많아도 분산 효과가 작습니다.
3. 샤프·트레이너·알파는 서로 다른 위험 기준을 씁니다. 하나의 점수로 우열을 확정하지 말고 벤치마크와 같은 기간·통화 기준인지 확인합니다.
"""
    )
    st.info(
        "투자성향 테스트를 먼저 하면 위험회피계수와 참고 위험자산 비중을 포트폴리오 화면에서 함께 비교할 수 있습니다.",
        icon="ℹ️",
    )
    _open_links(
        ("포트폴리오 화면 열기", "portfolio", True),
        ("홈에서 투자성향 테스트 선택", "home", False),
    )


def _render_risk_guide() -> None:
    st.markdown("#### ‘정답 배분’이 아니라 내 손실 감내 수준을 점검합니다")
    st.markdown(
        """
1. 8개 문항에 현재 상황을 기준으로 답합니다. 기대수익보다 **손실을 견딜 수 있는 기간과 규모**를 현실적으로 선택하세요.
2. 결과의 위험회피계수 `A`와 참고 위험자산 비중을 확인합니다. `A`가 클수록 변동성을 더 부담스럽게 본다는 뜻입니다.
3. 시장·금리·변동성 가정을 바꾸며 결과가 얼마나 달라지는지 보고, 포트폴리오의 실제 비중과 비교합니다.
"""
    )
    st.warning(
        "화면의 ‘권장·최적 위험자산 비중’은 입력한 시장 가정 아래의 교육용 모형값입니다. "
        "공식 투자성향 진단이나 개인화된 투자권유가 아니며 금융회사의 절차를 대체하지 않습니다.",
        icon="⚠️",
    )
    _open_links(("홈 열고 ‘테스트 시작’ 선택", "home", True))


def _render_reading_paths() -> None:
    st.markdown("## 도구별 읽는 법")
    st.caption("필요한 도구 하나만 골라 읽으세요. 각 안내는 실제 화면에서 확인할 순서대로 정리했습니다.")
    stock_tab, bond_tab, portfolio_tab, risk_tab = st.tabs(
        ["📈 주식 가치평가", "🏦 채권", "🧺 포트폴리오", "🧭 투자성향"]
    )
    with stock_tab:
        _render_stock_guide()
    with bond_tab:
        _render_bond_guide()
    with portfolio_tab:
        _render_portfolio_guide()
    with risk_tab:
        _render_risk_guide()


def _render_terms() -> None:
    st.markdown("## 핵심 용어, 1분만에 보기")
    st.caption("공식보다 해석을 먼저 익히고, 계산식은 각 화면의 도움말에서 확인하세요.")
    left, right = st.columns(2)
    with left:
        with st.expander("적정가 · 괴리율 · 신뢰도"):
            st.markdown(
                "- **적정가**: 여러 모형이 계산한 참고값입니다. 증권사 목표주가나 보장된 가격이 아닙니다.\n"
                "- **괴리율**: 모형 적정가를 현재가와 비교한 비율입니다. 크기만 보지 말고 평가 방법들의 방향이 같은지 함께 봅니다.\n"
                "- **신뢰도**: 평가 방법끼리 얼마나 비슷한 값을 냈는지 나타냅니다. 데이터 품질 점수는 아닙니다."
            )
        with st.expander("PER · PBR · ROE · 업종 백분위"):
            st.markdown(
                "- **PER**: 주가가 이익의 몇 배인지 보여줍니다. 적자 기업에는 해석이 어렵습니다.\n"
                "- **PBR**: 주가가 장부가치의 몇 배인지 보여줍니다. 금융업·자산주 비교에 자주 씁니다.\n"
                "- **ROE**: 주주자본으로 얼마를 벌었는지 나타냅니다. 일회성 이익과 과도한 부채를 함께 확인합니다.\n"
                "- **업종 백분위**: 50이 업종 중앙값입니다. 같은 업종 안에서의 상대 위치입니다."
            )
    with right:
        with st.expander("ROIC · WACC · 베타"):
            st.markdown(
                "- **ROIC**: 영업에 투입한 자본으로 낸 수익률입니다.\n"
                "- **WACC**: 주주와 채권자가 요구하는 평균 자본비용입니다.\n"
                "- **ROIC − WACC**: 양수면 자본비용보다 더 벌었다는 뜻입니다. 한 해보다 지속성을 보세요.\n"
                "- **베타**: 시장 움직임에 대한 민감도입니다. 기업 전체 위험을 모두 설명하지는 않습니다."
            )
        with st.expander("변동성 · 샤프비율 · 백테스트"):
            st.markdown(
                "- **변동성**: 수익률이 흔들린 정도입니다. 손실 크기와 완전히 같은 개념은 아닙니다.\n"
                "- **샤프비율**: 변동성 한 단위당 무위험수익 초과 성과입니다. 같은 기간·통화끼리 비교합니다.\n"
                "- **백테스트**: 이 앱에서는 역사적 밴드 신호 하나를 과거에 적용해 봅니다. 종합 판정 검증이나 미래 보장으로 해석하지 않습니다."
            )


def _render_ai_and_faq() -> None:
    st.markdown("## AI는 선택 기능입니다")
    st.markdown(
        "가치평가·재무·차트·채권·포트폴리오와 규칙 기반 설명은 **Gemini 키 없이도 사용할 수 있습니다.** "
        "키를 연결하면 Gemini가 업종·비교기업 선정, 기업 소개 번역, 뉴스 분류·요약, 종합 평가에 참여할 수 있습니다."
    )
    st.warning(
        "종합 평가는 매수·회피 같은 스탠스, 목표가·손절선, 성향별 편입비중까지 생성할 수 있습니다. "
        "이는 검증된 투자 조언이 아니라 언어모델의 출력입니다. 수치와 출처를 공시·뉴스 원문으로 확인하고 실제 주문 기준으로 사용하지 마세요.",
        icon="⚠️",
    )
    with st.expander("Gemini 기능을 연결하려면"):
        st.markdown(
            "1. Google AI Studio에서 API 키를 발급합니다.\n"
            "2. `.streamlit/secrets.toml`에 `GEMINI_API_KEY = \"...\"` 형식으로 넣거나 환경변수로 설정합니다.\n"
            "3. 앱을 다시 실행합니다.\n\n"
            "키를 코드나 GitHub 저장소에 올리지 마세요. 오류가 나더라도 핵심 분석 기능은 계속 사용할 수 있습니다."
        )

    st.markdown("## 문제가 생겼을 때")
    faqs = [
        ("첫 조회가 오래 걸려요", "처음에는 종목·재무·비교기업 데이터를 여러 공개 원천에서 모으기 때문에 시간이 걸릴 수 있습니다. 같은 분석은 캐시되어 이후 조회가 빨라집니다."),
        ("종목을 찾지 못해요", "시장(KR/US)을 먼저 확인하세요. 한국은 6자리 코드 또는 종목명, 미국은 티커를 권장합니다. 이름이 모호하면 코드를 입력하는 편이 정확합니다."),
        ("화면에 ‘—’가 많아요", "공개 데이터 원천에 값이 없거나 비교가 부적절할 때 임의로 추정하지 않고 ‘—’로 표시합니다. 최근 공시 원문과 다른 출처를 함께 확인하세요."),
        ("포털이나 증권사 숫자와 달라요", "기준일, 연결·별도 재무제표, 최근 12개월(TTM), 수정주가, 환율과 주식 수 처리 방식이 다르면 값도 달라집니다. 화면의 기준일과 출처를 먼저 맞춰 보세요."),
        ("AI 분석이 안 돼요", "Gemini 키가 없거나 호출 한도·네트워크 문제일 수 있습니다. AI는 선택 기능이므로 요약·밸류에이션·재무·차트 등 핵심 화면부터 사용하면 됩니다."),
    ]
    for title, body in faqs:
        with st.expander(title):
            st.write(body)


def _render_final_check() -> None:
    st.markdown("## 결론을 내리기 전, 다섯 가지")
    st.markdown(
        """
<div class="guide-check">
  <strong>□</strong> 적정가 방법들의 방향과 신뢰도를 함께 확인했다.<br>
  <strong>□</strong> 이익과 현금흐름이 함께 좋아지는지 봤다.<br>
  <strong>□</strong> 업종 대비 싸거나 비싼 이유를 확인했다.<br>
  <strong>□</strong> 백테스트 표본과 데이터 기준일의 한계를 봤다.<br>
  <strong>□</strong> 최근 공시·뉴스 원문으로 핵심 가정을 교차 확인했다.
</div>
""",
        unsafe_allow_html=True,
    )
    _open_links(
        ("주식 분석 시작", "stock", True),
        ("홈에서 다른 도구 선택", "home", False),
    )
    st.caption("본 도구는 공개 데이터를 이용한 학습·분석 보조 도구이며, 특정 상품의 매수·매도 추천이 아닙니다.")


def render() -> None:
    """사용설명서 페이지를 렌더링한다."""
    st.markdown(GUIDE_CSS, unsafe_allow_html=True)

    with st.sidebar:
        st.caption("📖 설명서는 새 탭에 두고 실제 화면과 함께 보세요.")
        st.markdown(
            "**추천 순서**\n\n"
            "1. 5분 빠른 시작\n"
            "2. 필요한 도구 하나\n"
            "3. 핵심 용어\n"
            "4. 문제 해결"
        )

    st.markdown(
        """
<div class="guide-hero">
  <p class="overline">QUICK GUIDE · 처음 5분</p>
  <h1>숫자를 많이 보는 법보다, 올바른 순서로 읽는 법</h1>
  <p>투자지표는 결론을 대신 내려주는 서비스가 아니라, 가격·기업 체력·위험을 같은 기준으로 점검하는 분석 도구입니다. 설명서를 옆에 두고 실제 종목 하나로 따라 해보세요.</p>
</div>
""",
        unsafe_allow_html=True,
    )

    _render_quick_start()
    st.divider()
    _render_reading_paths()
    st.divider()
    _render_terms()
    st.divider()
    _render_ai_and_faq()
    st.divider()
    _render_final_check()
