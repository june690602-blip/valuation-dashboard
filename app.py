"""투자지표 — 멀티페이지 엔트리(라우터).

실행: streamlit run app.py
페이지 본문: src/ui/pages/ (홈·주식·채권·포트폴리오), 분석 로직: src/analysis/ (순수 함수).
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="투자지표 — 가치평가 대시보드", page_icon="📊",
                   layout="wide", initial_sidebar_state="expanded")

# 우상단 기본 "Running" 상태 위젯(스포츠 픽토그램 애니메이션)을 숨긴다.
# 로딩 안내는 각 페이지의 st.spinner 메시지로만 깔끔하게 노출한다.
st.markdown(
    """
    <style>
    [data-testid="stStatusWidget"] { display: none !important; }
    /* 메트릭 숫자가 좁은 칸에서 줄바꿈/잘리지 않도록: 한 줄 유지 + 살짝 축소 */
    [data-testid="stMetricValue"] {
        white-space: nowrap;
        font-size: 1.55rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

from src.ui.nav import PAGES  # noqa: E402  (set_page_config 이후에 임포트해야 함)

st.navigation(list(PAGES.values())).run()

# ── 증권사식 차트 자동 y-fit ─────────────────────────────────────────
# 시계열 차트(nav="timex")는 시간축(x)만 이동/확대되도록 가격축(y)을 고정했다. 여기서
# 보이는 x 구간에 맞춰 y 범위를 자동 재조정(TradingView처럼 데이터가 화면을 꽉 채움).
# window.parent.Plotly는 첫 차트 렌더 후 사용 가능 — 없으면 조용히 대기(fixedrange 폴백).
import streamlit.components.v1 as components  # noqa: E402

components.html(
    """
    <script>
    (function () {
      var doc, PL;
      try { doc = window.parent.document; } catch (e) { return; }
      function fit(gd) {
        try {
          PL = window.parent.Plotly;
          if (!PL || gd._fitting || !gd.calcdata) return;
          var fl = gd._fullLayout; if (!fl || !fl.xaxis || typeof fl.xaxis.d2c !== "function") return;
          var xa = fl.xaxis; if (!xa.range) return;
          var xlo = Math.min(xa.d2c(xa.range[0]), xa.d2c(xa.range[1]));
          var xhi = Math.max(xa.d2c(xa.range[0]), xa.d2c(xa.range[1]));
          // trace를 y축(y, y2 …)별로 묶어 각 축을 보이는 x 구간에 맞춘다.
          // gd.data[i].y는 배열이 아닐 수 있어 gd.calcdata(이미 변환된 {x,y} 좌표)를 쓴다.
          var groups = {};
          (gd.data || []).forEach(function (tr, i) { var ax = tr.yaxis || "y"; (groups[ax] = groups[ax] || []).push(i); });
          var upd = {};
          Object.keys(groups).forEach(function (ax) {
            var lo = Infinity, hi = -Infinity;
            groups[ax].forEach(function (i) {
              var cd = gd.calcdata[i]; if (!cd) return;
              for (var k = 0; k < cd.length; k++) {
                var px = cd[k].x, py = cd[k].y;
                if (px >= xlo && px <= xhi && py != null && isFinite(py)) { if (py < lo) lo = py; if (py > hi) hi = py; }
              }
            });
            if (isFinite(lo) && isFinite(hi) && hi > lo) {
              var pad = (hi - lo) * 0.08;
              var loOut = (lo >= 0) ? Math.max(0, lo - pad) : lo - pad;  // 양수전용(거래량 등)은 0 기준
              upd[(ax === "y" ? "yaxis" : "yaxis" + ax.slice(1)) + ".range"] = [loOut, hi + pad];
            }
          });
          if (Object.keys(upd).length) {
            gd._fitting = true;
            PL.relayout(gd, upd).then(function () { gd._fitting = false; }).catch(function () { gd._fitting = false; });
          }
        } catch (e) {}
      }
      function attach(gd) {
        if (gd._autofit || typeof gd.on !== "function") return;
        gd._autofit = true;
        gd.on("plotly_relayout", function (ev) {
          if (!ev) return;
          // 줌/팬(xaxis.range) 또는 더블클릭 리셋(xaxis.autorange) 뒤 y를 보이는 x구간에 맞춤.
          if (("xaxis.range[0]" in ev) || ("xaxis.range" in ev) || ev["xaxis.autorange"]) {
            setTimeout(function () { fit(gd); }, 0);  // Plotly가 x range를 확정한 뒤 계산
          }
        });
      }
      function scan() { try { doc.querySelectorAll(".js-plotly-plot").forEach(attach); } catch (e) {} }
      scan();
      setInterval(scan, 1200);
    })();
    </script>
    """,
    height=0,
)
