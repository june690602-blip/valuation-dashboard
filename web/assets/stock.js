/* ══════════════════════════════════════════════════════════════════════
   투자지표 — 주식 가치평가 상세 (Meridian) · 실데이터 연결판
   /api/analyze 를 fetch 해서 받은 JSON(payload)으로 헤더·타일·9개 탭·12개 차트를
   전부 렌더한다. 차트 좌표 로직은 Claude Design 핸드오프(.dc.html)를 이식.
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── SVG/HTML 문자열 빌더 ── */
  var ATTR = { strokeWidth: 'stroke-width', strokeDasharray: 'stroke-dasharray', strokeLinecap: 'stroke-linecap', strokeLinejoin: 'stroke-linejoin', strokeOpacity: 'stroke-opacity', fillOpacity: 'fill-opacity', textAnchor: 'text-anchor', fontFamily: 'font-family', fontSize: 'font-size', fontWeight: 'font-weight', className: 'class' };
  function kebab(s) { return s.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); }); }
  function styleStr(o) { var s = ''; for (var k in o) s += kebab(k) + ':' + o[k] + ';'; return s; }
  function el(tag, attrs) {
    var kids = Array.prototype.slice.call(arguments, 2);
    attrs = attrs || {};
    var style = {};
    if (attrs.style) for (var sk in attrs.style) style[sk] = attrs.style[sk];
    var s = '<' + tag;
    for (var k in attrs) {
      if (k === 'style' || attrs[k] == null) continue;
      var val = attrs[k];
      // var()는 프레젠테이션 속성에서 Firefox/Safari가 해석하지 못한다 → 인라인 style로.
      if (typeof val === 'string' && val.indexOf('var(') >= 0) { style[k] = val; continue; }
      s += ' ' + (ATTR[k] || k) + '="' + String(val).replace(/"/g, '&quot;') + '"';
    }
    var st = styleStr(style);
    if (st) s += ' style="' + st + '"';
    s += '>';
    for (var i = 0; i < kids.length; i++) { var c = kids[i]; if (c == null || c === false) continue; s += Array.isArray(c) ? c.join('') : c; }
    return s + '</' + tag + '>';
  }
  // 홑따옴표(')까지 이스케이프한다 — 네 프런트 파일이 같은 규칙을 써야 한다(R5).
  // 지금은 속성을 " 로만 감싸서 없어도 악용되지 않지만, 같은 이름의 함수가 파일마다
  // 다른 안전성을 뜻하는 상태를 남기지 않는다. 인라인 핸들러(onclick=)는 쓰지 않으므로
  // &#39;이 JS 문맥으로 새어 들어갈 자리가 없다.
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]; }); }
  function $(id) { return document.getElementById(id); }

  /* ── 미니 마크다운 (Gemini 응답: ### 제목 · **굵게** · - 목록 · > 인용) ── */
  function mdToHtml(md) {
    var lines = String(md == null ? '' : md).replace(/\r/g, '').split('\n');
    var html = '', inList = false;
    function inline(s) { return esc(s).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>'); }
    function closeList() { if (inList) { html += '</ul>'; inList = false; } }
    for (var i = 0; i < lines.length; i++) {
      var t = lines[i].trim();
      if (!t) { closeList(); continue; }
      var h = t.match(/^(#{1,6})\s+(.*)$/);
      if (h) { closeList(); html += '<h3>' + inline(h[2]) + '</h3>'; continue; }
      if (/^>\s?/.test(t)) { closeList(); html += '<blockquote>' + inline(t.replace(/^>\s?/, '')) + '</blockquote>'; continue; }
      if (/^[-*]\s+/.test(t)) { if (!inList) { html += '<ul>'; inList = true; } html += '<li>' + inline(t.replace(/^[-*]\s+/, '')) + '</li>'; continue; }
      closeList(); html += '<p>' + inline(t) + '</p>';
    }
    closeList();
    return '<div class="aimd">' + html + '</div>';
  }

  /* ── AI 엔드포인트 호출 (news_ai · opinion) → 마크다운 렌더 ── */
  function aiFetch(kind, out, btn) {
    var old = btn.textContent; btn.disabled = true; btn.textContent = 'AI 생성 중…';
    out.innerHTML = '<div style="font-size:12px;color:var(--ink-3);margin-top:8px;display:flex;align-items:center;gap:8px"><span class="spin" style="width:14px;height:14px;margin:0"></span>Gemini가 분석하는 중…</div>';
    var url = (kind === 'news' ? 'api/news_ai' : 'api/opinion') + '?market=' + encodeURIComponent(state.market) + '&query=' + encodeURIComponent(state.query) + '&peer_count=' + (state.peer_count || 9);
    fetch(url).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        btn.disabled = false; btn.textContent = old;
        if (!res.ok || res.j.error) { out.innerHTML = '<div style="font-size:12.5px;color:var(--danger);margin-top:8px">AI 생성 실패: ' + esc(res.j.error || '알 수 없는 오류') + '</div>'; return; }
        out.innerHTML = mdToHtml(res.j.markdown);
      })
      .catch(function (e) { btn.disabled = false; btn.textContent = old; out.innerHTML = '<div style="font-size:12.5px;color:var(--danger);margin-top:8px">서버 연결 실패: ' + esc(e.message) + '</div>'; });
  }

  /* ── 포맷터 ── */
  var CUR = 'KRW';
  function won(v) { return v == null ? '—' : Math.round(v).toLocaleString('en-US'); }
  function fmtPrice(v) { if (v == null) return '—'; return CUR === 'KRW' ? won(v) + '원' : '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function fmtMoney(v) {
    if (v == null) return '—'; var a = Math.abs(v);
    if (CUR === 'KRW') { if (a >= 1e12) return (v / 1e12).toFixed(1) + '조원'; if (a >= 1e8) return Math.round(v / 1e8).toLocaleString('en-US') + '억원'; return won(v) + '원'; }
    if (a >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B'; if (a >= 1e6) return '$' + (v / 1e6).toFixed(0) + 'M'; return '$' + won(v);
  }
  function fmtPct(v, d) { return v == null ? '—' : (v * 100).toFixed(d == null ? 1 : d) + '%'; }
  function fmtX(v) { if (v == null) return '—'; return (v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : v.toFixed(0)) + '×'; }
  function fmtSigned(v) { return v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; }
  function fmtMult(key, v) { if (v == null) return '—'; if (key === 'div_yield') return (v * 100).toFixed(1) + '%'; if (key === 'peg') return v.toFixed(2); return fmtX(v); }
  /* 빈 값 '—'에 이유 말풍선을 붙인다(순수 CSS 툴팁, .na) — 왜 없는지 짧게 알린다. */
  function na(reason) { return '<span class="na" tabindex="0" data-tip="' + esc(reason) + '">—</span>'; }
  function compactWon(v) { if (v == null) return '—'; return CUR === 'KRW' ? Math.round(v / 1000).toLocaleString('en-US') + '천' : '$' + Math.round(v).toLocaleString('en-US'); }

  var VERDICTS = ['크게 저평가', '저평가', '적정 수준', '고평가', '크게 고평가'];
  function vIdx(v) { var i = VERDICTS.indexOf(v); return i < 0 ? 2 : i; }
  function vPos(v) { return [12, 31, 50, 69, 88][vIdx(v)]; }
  function vTone(v) { var i = vIdx(v); return i <= 1 ? 'positive' : i === 2 ? 'neutral' : 'negative'; }

  /* ── 상태 ── */
  var state = { market: 'KR', query: '035420', kind: 'stock', pricePeriod: '1Y', priceMode: 'abs', chartType: 'line', ma: { m20: true, m60: true, m120: false }, hover: null, bandMetric: 'PER', scnBear: -0.15, scnBull: 0.15, scnMult: 0, peerEx: [], peerAdd: [], _editKey: '' };
  var D = null;
  var EXAMPLES = { KR: [['삼성전자', '005930'], ['현대차', '005380'], ['NAVER', '035420'], ['KB금융', '105560']], US: [['Apple', 'AAPL'], ['Microsoft', 'MSFT'], ['Coca-Cola', 'KO'], ['Rivian', 'RIVN']] };

  /* ══════════ 차트 (데이터 구동) ══════════ */

  function bulletChart() {
    if (window.matchMedia && window.matchMedia('(max-width: 560px)').matches) return bulletChartNarrow();
    var est = D.verdict.estimates || [];
    var cur = D.meta.price, avg = D.verdict.fair_mid;
    var vals = [cur]; est.forEach(function (e) { if (e.low != null) vals.push(e.low); if (e.high != null) vals.push(e.high); if (e.mid != null) vals.push(e.mid); });
    if (avg != null) vals.push(avg);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), sp = (hi - lo) || hi * 0.2 || 1;
    // 아래 여백이 0 밑으로 내려가면 '-124천' 같은 음수 주가 눈금이 생긴다 — 0에서 자른다.
    var dmin = Math.max(0, lo - sp * 0.18), dmax = hi + sp * 0.12;
    var W = 1040, padR = 30, plotL = 250, plotW = W - plotL - padR;
    var X = function (v) { return plotL + (Math.max(dmin, Math.min(dmax, v)) - dmin) / (dmax - dmin) * plotW; };
    var headH = 104, rowH = 58, rowsTop = headH, axisY = rowsTop + est.length * rowH + 10, H = axisY + 40;
    var els = [];
    var upside = (avg != null && cur) ? avg / cur - 1 : null;
    var up = upside != null && upside >= 0;
    var accent = up ? 'var(--dv-green)' : 'var(--dv-clay)';
    els.push(el('text', { x: 0, y: 20, fontSize: 12, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)' }, '현재가'));
    els.push(el('text', { x: 0, y: 52, fontSize: 30, fill: 'var(--ink)', fontFamily: 'var(--font-mono)', fontWeight: 600 }, won(cur)));
    els.push(el('path', { d: 'M196 43 h44 m-9 -7 l9 7 l-9 7', fill: 'none', stroke: 'var(--ink-3)', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' }));
    els.push(el('text', { x: 256, y: 20, fontSize: 12, fill: accent, fontFamily: 'var(--font-sans)' }, '종합 적정가 · 가중'));
    els.push(el('text', { x: 256, y: 52, fontSize: 30, fill: accent, fontFamily: 'var(--font-mono)', fontWeight: 600 }, won(avg)));
    if (upside != null) {
      els.push(el('rect', { x: 452, y: 24, width: 92, height: 34, rx: 17, fill: accent }));
      els.push(el('text', { x: 498, y: 46, fontSize: 16, fill: '#fff', fontFamily: 'var(--font-mono)', fontWeight: 600, textAnchor: 'middle' }, fmtSigned(upside)));
      els.push(el('text', { x: 498, y: 74, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'middle' }, up ? '상승여력' : '하락위험'));
    }
    var guideTop = rowsTop - 6, guideBot = axisY;
    if (avg != null) {
      els.push(el('rect', { x: Math.min(X(cur), X(avg)), y: guideTop, width: Math.abs(X(avg) - X(cur)), height: guideBot - guideTop, fill: accent, fillOpacity: 0.07 }));
      els.push(el('line', { x1: X(avg), x2: X(avg), y1: guideTop, y2: guideBot, stroke: accent, strokeWidth: 1.5 }));
    }
    els.push(el('line', { x1: X(cur), x2: X(cur), y1: guideTop, y2: guideBot, stroke: 'var(--ink)', strokeWidth: 1.5, strokeDasharray: '5 4' }));
    els.push(el('text', { x: X(cur), y: guideTop - 6, fontSize: 11.5, fill: 'var(--ink)', fontFamily: 'var(--font-sans)', fontWeight: 600, textAnchor: 'middle' }, '현재가'));
    est.forEach(function (m, i) {
      var y = rowsTop + i * rowH + rowH / 2;
      els.push(el('text', { x: 0, y: y - 4, fontSize: 15, fill: 'var(--ink)', fontFamily: 'var(--font-sans)', fontWeight: 600 }, esc(m.method)));
      var _nt = m.note || '';
      els.push(el('text', { x: 0, y: y + 14, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)' }, esc(_nt.length > 34 ? _nt.slice(0, 33) + '…' : _nt)));
      if (m.low != null && m.high != null) els.push(el('line', { x1: X(m.low), x2: X(m.high), y1: y, y2: y, stroke: 'var(--dv-navy)', strokeWidth: 9, strokeLinecap: 'round', opacity: 0.22 }));
      if (m.mid != null) els.push(el('circle', { cx: X(m.mid), cy: y, r: 6, fill: 'var(--dv-navy)', stroke: 'var(--paper)', strokeWidth: 1.5 }));
      els.push(el('text', { x: plotL + plotW + padR, y: y + 5, fontSize: 14, fill: 'var(--ink)', fontFamily: 'var(--font-mono)', fontWeight: 500, textAnchor: 'end' }, compactWon(m.mid)));
    });
    els.push(el('line', { x1: plotL, x2: plotL + plotW, y1: axisY, y2: axisY, stroke: 'var(--line)', strokeWidth: 1 }));
    for (var t = 0; t <= 4; t++) { var tv = dmin + (dmax - dmin) * t / 4, xx = plotL + plotW * t / 4; els.push(el('line', { x1: xx, x2: xx, y1: axisY, y2: axisY + 5, stroke: 'var(--line-strong)', strokeWidth: 1 })); els.push(el('text', { x: xx, y: axisY + 21, fontSize: 11.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, compactWon(tv))); }
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }

  // 좁은 화면(≤560px) 전용 배치 — 데스크톱 bulletChart()는 그대로 둔다.
  // 데스크톱은 viewBox 1040px를 width:100%로 줄여 그리는데, 폰에서는 배율이 0.32까지
  // 떨어져 15px 글자가 5px로 렌더된다(읽을 수 없다). 여기서는 컨테이너 실폭을 그대로
  // viewBox로 삼아 배율을 1 부근에 두고(선언 크기 = 화면 크기), 왼쪽 라벨 열을 없애
  // 방법 이름을 막대 위로 올린다. 근거 문구는 바로 아래 방법별 표에 그대로 있어 뺀다.
  function bulletChartNarrow() {
    var est = D.verdict.estimates || [];
    var cur = D.meta.price, avg = D.verdict.fair_mid;
    var vals = [cur]; est.forEach(function (e) { if (e.low != null) vals.push(e.low); if (e.high != null) vals.push(e.high); if (e.mid != null) vals.push(e.mid); });
    if (avg != null) vals.push(avg);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), sp = (hi - lo) || hi * 0.2 || 1;
    // 아래 여백이 0 밑으로 내려가면 '-124천' 같은 음수 주가 눈금이 생긴다 — 0에서 자른다.
    var dmin = Math.max(0, lo - sp * 0.18), dmax = hi + sp * 0.12;
    var host = $('bulletChart');
    var W = Math.max(280, Math.min(520, (host && host.clientWidth) || 340));
    var plotL = 5, plotW = W - 10;   // 막대 끝(둥근 캡)이 지면 밖으로 잘리지 않게 좌우를 조금 들인다
    var X = function (v) { return plotL + (Math.max(dmin, Math.min(dmax, v)) - dmin) / (dmax - dmin) * plotW; };
    var headH = 164, rowH = 84, rowsTop = headH, axisY = rowsTop + est.length * rowH + 14, H = axisY + 34;
    // 폰에서는 왼쪽 라벨 열이 없어 기준선(현재가·적정가)이 글자 위를 지난다 → 종이색 테두리로 글자를 살린다.
    var halo = { paintOrder: 'stroke', stroke: 'var(--paper)', strokeWidth: '3.5px', strokeLinejoin: 'round' };
    var haloThin = { paintOrder: 'stroke', stroke: 'var(--paper)', strokeWidth: '3px', strokeLinejoin: 'round' };
    var els = [];
    var upside = (avg != null && cur) ? avg / cur - 1 : null;
    var up = upside != null && upside >= 0;
    var accent = up ? 'var(--dv-green)' : 'var(--dv-clay)';
    els.push(el('text', { x: 0, y: 14, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)' }, '현재가'));
    els.push(el('text', { x: 0, y: 44, fontSize: 22, fill: 'var(--ink)', fontFamily: 'var(--font-mono)', fontWeight: 600 }, won(cur)));
    els.push(el('text', { x: 0, y: 84, fontSize: 11, fill: accent, fontFamily: 'var(--font-sans)' }, '종합 적정가 · 가중'));
    els.push(el('text', { x: 0, y: 114, fontSize: 22, fill: accent, fontFamily: 'var(--font-mono)', fontWeight: 600 }, won(avg)));
    if (upside != null) {
      els.push(el('rect', { x: W - 92, y: 14, width: 92, height: 32, rx: 16, fill: accent }));
      els.push(el('text', { x: W - 46, y: 35, fontSize: 15, fill: '#fff', fontFamily: 'var(--font-mono)', fontWeight: 600, textAnchor: 'middle' }, fmtSigned(upside)));
      els.push(el('text', { x: W - 46, y: 62, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'middle' }, up ? '상승여력' : '하락위험'));
    }
    var guideTop = rowsTop - 12, guideBot = axisY;
    if (avg != null) {
      els.push(el('rect', { x: Math.min(X(cur), X(avg)), y: guideTop, width: Math.abs(X(avg) - X(cur)), height: guideBot - guideTop, fill: accent, fillOpacity: 0.07 }));
      els.push(el('line', { x1: X(avg), x2: X(avg), y1: guideTop, y2: guideBot, stroke: accent, strokeWidth: 1.5 }));
    }
    els.push(el('line', { x1: X(cur), x2: X(cur), y1: guideTop, y2: guideBot, stroke: 'var(--ink)', strokeWidth: 1.5, strokeDasharray: '5 4' }));
    els.push(el('text', { x: X(cur), y: guideTop - 9, fontSize: 10.5, fill: 'var(--ink)', fontFamily: 'var(--font-sans)', fontWeight: 600, textAnchor: X(cur) < 26 ? 'start' : X(cur) > W - 26 ? 'end' : 'middle', style: haloThin }, '현재가'));
    est.forEach(function (m, i) {
      var y0 = rowsTop + i * rowH, y = y0 + 42;
      if (i) els.push(el('line', { x1: 0, x2: W, y1: y0 - 5, y2: y0 - 5, stroke: 'var(--line)', strokeWidth: 1 }));
      els.push(el('text', { x: 0, y: y0 + 16, fontSize: 13, fill: 'var(--ink)', fontFamily: 'var(--font-sans)', fontWeight: 600, style: halo }, esc(m.method)));
      els.push(el('text', { x: W, y: y0 + 16, fontSize: 12, fill: 'var(--ink)', fontFamily: 'var(--font-mono)', fontWeight: 500, textAnchor: 'end', style: halo }, compactWon(m.mid)));
      if (m.low != null && m.high != null) {
        var xa = X(m.low), xb = X(m.high);
        els.push(el('line', { x1: xa, x2: xb, y1: y, y2: y, stroke: 'var(--dv-navy)', strokeWidth: 8, strokeLinecap: 'butt', opacity: 0.22 }));
        // 범위의 시작·끝을 눈금으로 못 박는다 — 막대만으로는 어디까지가 이 방법의 범위인지 읽히지 않는다.
        els.push(el('line', { x1: xa, x2: xa, y1: y - 7, y2: y + 7, stroke: 'var(--dv-navy)', strokeWidth: 1.5 }));
        els.push(el('line', { x1: xb, x2: xb, y1: y - 7, y2: y + 7, stroke: 'var(--dv-navy)', strokeWidth: 1.5 }));
        // 방법 간 격차가 크면 좁은 범위(RIM은 축의 1.5%)는 몇 픽셀로 뭉개진다 → 숫자로도 적는다.
        if (xb - xa >= 82) {
          els.push(el('text', { x: xa, y: y + 24, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'start', style: haloThin }, compactWon(m.low)));
          els.push(el('text', { x: xb, y: y + 24, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'end', style: haloThin }, compactWon(m.high)));
        } else {
          var mx = (xa + xb) / 2, anc = mx < 52 ? 'start' : mx > W - 52 ? 'end' : 'middle';
          els.push(el('text', { x: anc === 'start' ? 0 : anc === 'end' ? W : mx, y: y + 24, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: anc, style: haloThin }, compactWon(m.low) + ' ~ ' + compactWon(m.high)));
        }
      }
      if (m.mid != null) els.push(el('circle', { cx: X(m.mid), cy: y, r: 5.5, fill: 'var(--dv-navy)', stroke: 'var(--paper)', strokeWidth: 1.5 }));
    });
    els.push(el('line', { x1: plotL, x2: plotL + plotW, y1: axisY, y2: axisY, stroke: 'var(--line)', strokeWidth: 1 }));
    for (var t = 0; t <= 2; t++) {   // 눈금은 3개 — 폰 폭에서 5개는 라벨이 서로 붙는다
      var tv = dmin + (dmax - dmin) * t / 2, xx = plotL + plotW * t / 2;
      els.push(el('line', { x1: xx, x2: xx, y1: axisY, y2: axisY + 5, stroke: 'var(--line-strong)', strokeWidth: 1 }));
      els.push(el('text', { x: xx, y: axisY + 22, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: t === 0 ? 'start' : t === 2 ? 'end' : 'middle' }, compactWon(tv)));
    }
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }

  function radarChart() {
    var order = ['밸류에이션', '수익성', '성장성', '재무 안정성', '현금흐름'];
    var cats = order.map(function (k) { return [k === '재무 안정성' ? '재무안정성' : k, D.scores.cats[k]]; });
    var cx = 190, cy = 158, R = 112, W = 380, H = 300;
    var pt = function (val, i) { var a = (-90 + i * 72) * Math.PI / 180, rr = R * (val || 0) / 100; return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)]; };
    var els = [];
    [25, 50, 75, 100].forEach(function (r) { var p = ''; for (var i = 0; i < 5; i++) { var q = pt(r, i); p += (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1) + ' '; } p += 'Z'; els.push(el('path', { d: p, fill: 'none', stroke: r === 50 ? 'var(--dv-clay)' : 'var(--line)', strokeWidth: 1, strokeDasharray: r === 50 ? '4 3' : 'none', opacity: r === 50 ? 0.75 : 1 })); });
    for (var i = 0; i < 5; i++) { var q = pt(100, i); els.push(el('line', { x1: cx, y1: cy, x2: q[0], y2: q[1], stroke: 'var(--line)', strokeWidth: 1 })); }
    var pp = ''; cats.forEach(function (c, i) { var q = pt(c[1], i); pp += (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1) + ' '; }); pp += 'Z';
    els.push(el('path', { d: pp, fill: 'var(--dv-navy)', fillOpacity: 0.14, stroke: 'var(--dv-navy)', strokeWidth: 1.8 }));
    cats.forEach(function (c, i) { var q = pt(c[1], i); els.push(el('circle', { cx: q[0], cy: q[1], r: 3.4, fill: 'var(--dv-navy)' })); });
    cats.forEach(function (c, i) { var q = pt(124, i), anchor = q[0] < cx - 8 ? 'end' : q[0] > cx + 8 ? 'start' : 'middle'; els.push(el('text', { x: q[0], y: q[1] - 2, fontSize: 12, fill: 'var(--ink-2)', fontFamily: 'var(--font-sans)', textAnchor: anchor, fontWeight: 500 }, c[0])); els.push(el('text', { x: q[0], y: q[1] + 13, fontSize: 12, fill: 'var(--ink)', fontFamily: 'var(--font-mono)', textAnchor: anchor, fontWeight: 500 }, c[1] == null ? '—' : Math.round(c[1]))); });
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block', maxWidth: '400px', margin: '0 auto' } }, els);
  }

  function scoreDesc(k, v) {
    if (v == null) {
      // 금융업은 이 두 축의 지표(부채비율·유동비율·FCF수익률 등)가 부적합해 의도적으로 제외한다.
      // '산출 불가'(데이터 실패)로 오해되지 않게 사유를 분명히 구분한다.
      if (D.meta && D.meta.is_financial && (k === '재무 안정성' || k === '현금흐름'))
        return '금융업 특성상 일반 지표가 부적합 — 제외';
      return nullScoreReason(k);
    }
    var strong = v >= 65, weak = v < 35;
    var tail = strong ? '업종 상위 — 강점' : weak ? '업종 하위 — 약점' : v >= 50 ? '업종 평균 이상' : '업종 평균 부근';
    return tail;
  }

  // 점수 미산출 사유 — details의 지표별 피어 보유 수(n)로 원인을 구분해 보여준다.
  function nullScoreReason(k) {
    var rows = (D.scores.details || {})[k] || [];
    if (!rows.length) return '산출 불가';
    var maxN = 0, selfMissing = true;
    rows.forEach(function (r) { if (r.n != null && r.n > maxN) maxN = r.n; if (r.target != null) selfMissing = false; });
    if (maxN < 3) return '피어 표본 부족 — 지표 보유 피어 ' + maxN + '개 (최소 3개 필요). 무료 데이터 결측으로, 잠시 후 재조회하면 채워질 수 있어요.';
    if (selfMissing) return '자사 지표 결측 — 피어는 충분하지만 이 종목의 값이 없어 산출 불가';
    return '산출 불가 (지표별 상세 참조)';
  }
  function scoreBars() {
    var order = ['밸류에이션', '수익성', '성장성', '재무 안정성', '현금흐름'];
    return order.map(function (k) {
      var v = D.scores.cats[k]; var good = v != null && v >= 50; var w = v == null ? 0 : v;
      return el('div', { style: { display: 'grid', gridTemplateColumns: '92px 1fr', gap: '16px', alignItems: 'center' } },
        el('span', { style: { fontSize: '14px', fontWeight: 600 } }, k === '재무 안정성' ? '재무안정성' : k),
        el('div', {},
          el('div', { style: { position: 'relative', height: '30px', background: 'var(--paper-3)', border: '1px solid var(--line-strong)', borderRadius: 'var(--radius-md)', overflow: 'hidden' } },
            // 의미색(강점=green·약점=clay)은 유지하되 종이 쪽으로 68% 톤다운 — 판정 막대보다 조용하게.
            el('div', { style: { position: 'absolute', top: 0, bottom: 0, left: 0, width: w + '%', background: good ? 'color-mix(in srgb, var(--dv-green) 68%, var(--paper))' : 'color-mix(in srgb, var(--dv-clay) 68%, var(--paper))' } }),
            el('div', { style: { position: 'absolute', left: '50%', top: 0, bottom: 0, width: '2px', background: 'var(--ink)', opacity: 0.5 } }),
            el('span', { style: { position: 'absolute', left: '50%', top: '2px', transform: 'translateX(-50%)', fontSize: '10px', color: 'var(--ink-2)', fontFamily: 'var(--font-sans)' } }, '50'),
            v == null ? '' : el('span', { style: { position: 'absolute', left: 'calc(' + w + '% - 8px)', top: '50%', transform: 'translate(-100%,-50%)', fontFamily: 'var(--font-mono)', fontSize: '14px', fontWeight: 700, color: 'var(--ink)' } }, Math.round(v))
          ),
          el('div', { style: { fontSize: '12px', color: 'var(--ink-2)', marginTop: '5px', lineHeight: 1.4 } }, scoreDesc(k, v))
        )
      );
    }).join('');
  }

  /* ── 주가차트 (Canvas · 리서치 차트형 시간축 탐색) ────────────────
     선 차트의 직관성은 유지하되, 시간축만 확대·이동하고 보이는 구간에 맞춰
     가격축을 자동 조정한다. 교차선의 상세값은 상단 시세 스트립과 축 태그에 표시. */
  var CVAR_CACHE = {};
  function cvar(name) { if (CVAR_CACHE[name] == null) { CVAR_CACHE[name] = (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim() || '#000'; } return CVAR_CACHE[name]; }
  var CH_FONT_SANS = '"IBM Plex Sans KR", system-ui, sans-serif';
  var CH_FONT_MONO = '"Noto Sans KR", system-ui, monospace';

  function makePriceChart(container, D, state) {
    var d = D.price;
    var rel = state.priceMode === 'rel';
    var fullClose = Array.isArray(d.close) ? d.close : [];
    var N = fullClose.length;
    function emptyChart(message) {
      container.innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:28px 0;border-top:1px solid var(--line)">' + esc(message) + '</div>';
      return { destroy: function () {}, reset: function () {} };
    }
    var hasFiniteClose = fullClose.some(function (v) { return v != null && isFinite(v); });
    if (!N || !hasFiniteClose) return emptyChart('표시할 유효 주가 데이터가 없습니다.');
    function parsedDate(s) {
      var m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
      return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
    }
    function calendarCutoff(last, months, years) {
      var first = new Date(last.getFullYear() - (years || 0), last.getMonth() - (months || 0), 1);
      var lastDay = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
      return new Date(first.getFullYear(), first.getMonth(), Math.min(last.getDate(), lastDay));
    }
    function periodStart() {
      if (!N) return 0;
      var datesAll = d.dates || [], last = parsedDate(datesAll[N - 1]), cutoff = null;
      if (last) {
        if (state.pricePeriod === 'YTD') cutoff = new Date(last.getFullYear(), 0, 1);
        else if (state.pricePeriod === '1M') cutoff = calendarCutoff(last, 1, 0);
        else if (state.pricePeriod === '3M') cutoff = calendarCutoff(last, 3, 0);
        else if (state.pricePeriod === '6M') cutoff = calendarCutoff(last, 6, 0);
        else if (state.pricePeriod === '1Y') cutoff = calendarCutoff(last, 0, 1);
        else if (state.pricePeriod === '3Y') cutoff = calendarCutoff(last, 0, 3);
        else if (state.pricePeriod === '5Y') cutoff = calendarCutoff(last, 0, 5);
        if (cutoff) {
          for (var di = 0; di < N; di++) { var dt = parsedDate(datesAll[di]); if (dt && dt >= cutoff) return di; }
        }
      }
      var sessions = { '1M': 21, '3M': 63, '6M': 126, 'YTD': 252, '1Y': 252, '3Y': 756, '5Y': 1260 }[state.pricePeriod] || 252;
      return Math.max(0, N - sessions);
    }
    var offset = periodStart();
    function selected(a, fallback) {
      a = Array.isArray(a) ? a : null;
      var out = [];
      for (var ai = offset; ai < N; ai++) out.push(a && a[ai] != null ? a[ai] : (fallback ? fallback[ai] : null));
      return out;
    }
    var close = selected(fullClose), dates = selected(d.dates || []), vol = selected(d.vol || []);
    /* 결측 OHLC는 종가로 추정하지 않는다. 데이터가 없으면 상태줄에 정직하게 —로 표시한다. */
    var open = selected(d.open || []), high = selected(d.high || []), low = selected(d.low || []);
    var ma20 = selected(d.ma20 || []), ma60 = selected(d.ma60 || []), ma120 = selected(d.ma120 || []), bench = selected(d.bench || []);
    var n = close.length;
    if (!n) return emptyChart('표시할 주가 데이터가 없습니다.');
    var latestIndex = n - 1; while (latestIndex > 0 && close[latestIndex] == null) latestIndex--;

    /* 비교 모드는 선택 기간의 첫 공통 거래일을 100으로 맞춘다. */
    var stockY = null, benchY = null, compareBase = 0;
    if (rel) {
      while (compareBase < n && (close[compareBase] == null || bench[compareBase] == null)) compareBase++;
      if (compareBase >= n) {
        $('priceStatusName').textContent = (D.meta.name || D.meta.ticker || '종목') + ' · 상대성과';
        $('priceStatusMeta').textContent = '벤치마크와 공통으로 유효한 거래일이 없습니다.';
        $('priceStatusPrice').textContent = '—'; $('priceStatusChange').textContent = '비교 불가';
        $('priceStatusChange').style.color = 'var(--ink-3)'; $('priceStatusMetrics').innerHTML = '';
        return emptyChart('벤치마크 데이터가 부족해 상대성과를 계산할 수 없습니다.');
      }
      var c0 = close[compareBase], b0 = bench[compareBase];
      stockY = close.map(function (v, i) { return i < compareBase || v == null ? null : v / c0 * 100; });
      benchY = bench.map(function (v, i) { return i < compareBase || v == null ? null : v / b0 * 100; });
    }

    /* 레이아웃(CSS px) */
    var cssW = Math.max(260, Math.round(container.clientWidth || 700));
    var padL = 8, padR = cssW < 560 ? 58 : 72, plotT = 14;
    var cssH, plotH, volTop, volH;
    if (rel) { cssH = Math.max(280, Math.round(cssW * 0.42)); plotH = cssH - plotT - 34; }
    else { cssH = Math.max(330, Math.round(cssW * 0.50)); volH = Math.round(cssH * 0.17); var volGap = 18; plotH = cssH - plotT - 34 - volH - volGap; volTop = plotT + plotH + volGap; }
    var xw = cssW - padL - padR;

    /* x축만 탐색하고, y축은 현재 보이는 데이터에 자동 맞춤. */
    var viewStart = 0, viewEnd = Math.max(0, n - 1), hover = null, ymin = 0, ymax = 1, vmax = 1;
    var minSpan = Math.min(Math.max(1, n - 1), 14);
    function clampView() {
      var maxSpan = Math.max(1, n - 1), span = viewEnd - viewStart;
      if (span < minSpan) { var mid = (viewStart + viewEnd) / 2; viewStart = mid - minSpan / 2; viewEnd = mid + minSpan / 2; }
      if (span > maxSpan) { viewStart = 0; viewEnd = n - 1; }
      if (viewStart < 0) { viewEnd -= viewStart; viewStart = 0; }
      if (viewEnd > n - 1) { viewStart -= viewEnd - (n - 1); viewEnd = n - 1; }
      viewStart = Math.max(0, viewStart); viewEnd = Math.min(n - 1, viewEnd);
    }
    function visibleBounds() { return [Math.max(0, Math.floor(viewStart)), Math.min(n - 1, Math.ceil(viewEnd))]; }
    function scaleVisible() {
      var b = visibleBounds(), vals = [], series = rel ? [stockY, benchY] : [close];
      if (!rel && state.ma.m20) series.push(ma20);
      if (!rel && state.ma.m60) series.push(ma60);
      if (!rel && state.ma.m120) series.push(ma120);
      for (var si = 0; si < series.length; si++) for (var vi = b[0]; vi <= b[1]; vi++) if (series[si][vi] != null && isFinite(series[si][vi])) vals.push(+series[si][vi]);
      if (!vals.length) vals = [0, 1];
      ymin = Math.min.apply(null, vals); ymax = Math.max.apply(null, vals);
      var padv = (ymax - ymin) * 0.08 || Math.max(Math.abs(ymax) * 0.02, 1); ymin -= padv; ymax += padv;
      vmax = 1;
      if (!rel) for (var vv = b[0]; vv <= b[1]; vv++) if (vol[vv] != null && isFinite(vol[vv])) vmax = Math.max(vmax, +vol[vv]);
    }
    function xDoc(i) { var span = Math.max(1, viewEnd - viewStart); return padL + (i - viewStart) / span * xw; }
    function yDoc(v) { return plotT + (1 - (v - ymin) / (ymax - ymin)) * plotH; }
    function valAtY(sy) { return ymin + (1 - (sy - plotT) / plotH) * (ymax - ymin); }
    function idxAtX(sx) { var i = Math.round(viewStart + (sx - padL) / xw * (viewEnd - viewStart)); return Math.max(0, Math.min(n - 1, i)); }
    function zoomAt(sx, factor) {
      var span = Math.max(1, viewEnd - viewStart), next = Math.max(minSpan, Math.min(n - 1, span * factor));
      var ratio = Math.max(0, Math.min(1, (sx - padL) / xw)), anchor = viewStart + ratio * span;
      viewStart = anchor - ratio * next; viewEnd = viewStart + next; clampView();
    }
    function panPixels(dx) { var shift = -dx / xw * (viewEnd - viewStart); viewStart += shift; viewEnd += shift; clampView(); }

    /* 캔버스(HiDPI) */
    var dpr = window.devicePixelRatio || 1;
    var cv = document.createElement('canvas');
    cv.style.width = cssW + 'px'; cv.style.height = cssH + 'px'; cv.style.display = 'block'; cv.style.touchAction = 'pan-y'; cv.style.cursor = 'crosshair';
    cv.tabIndex = 0; cv.setAttribute('role', 'img'); cv.setAttribute('aria-describedby', 'priceCaption');
    cv.setAttribute('aria-label', D.meta.name + ' 일봉 주가 차트. 좌우 화살표로 이동하고 더하기와 빼기로 확대·축소하며 Home 또는 Escape로 전체 보기를 할 수 있습니다.');
    cv.width = Math.round(cssW * dpr); cv.height = Math.round(cssH * dpr);
    container.innerHTML = ''; container.appendChild(cv);
    var ctx = cv.getContext('2d');
    var COL = { ink: cvar('--ink'), ink2: cvar('--ink-2'), ink3: cvar('--ink-3'), line: cvar('--line'), lineStrong: cvar('--line-strong'), paper: cvar('--paper'), fill: cvar('--paper-3'), gold: cvar('--dv-gold'), slate: cvar('--dv-slate'), plum: cvar('--dv-plum'), clay: cvar('--dv-clay'), positive: cvar('--dv-positive'), negative: cvar('--dv-negative') };

    function fmtChartPrice(v) { if (v == null || !isFinite(v)) return '—'; return CUR === 'KRW' ? Math.round(v).toLocaleString('en-US') : Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
    function displayDate(v) { return String(v || '—').replace(/-/g, '.'); }
    function signedPctPoint(v) { return v == null || !isFinite(v) ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }
    function metric(label, value) { return '<div class="chart-status-metric"><dt>' + esc(label) + '</dt><dd>' + esc(value) + '</dd></div>'; }
    function updateStatus(i) {
      i = Math.max(0, Math.min(n - 1, i == null ? latestIndex : i));
      var name = D.meta.name || D.meta.ticker || '종목', source = d.source || '', delay = d.delay_note || '';
      $('priceStatusName').textContent = name + (rel ? ' · 상대성과' : '');
      var metaText = ['일봉', D.meta.currency || CUR, '기준일 ' + displayDate(dates[i]), source, delay].filter(Boolean).join(' · ');
      $('priceStatusMeta').textContent = metaText; $('priceStatusMeta').title = metaText;
      if (rel) {
        var sv = stockY[i] == null ? null : stockY[i] - 100, bv = benchY[i] == null ? null : benchY[i] - 100;
        var excess = sv == null || bv == null ? null : sv - bv, benchName = D.meta.benchmark_name || D.meta.benchmark || '벤치마크';
        $('priceStatusPrice').textContent = signedPctPoint(sv);
        $('priceStatusChange').textContent = excess == null ? '초과수익률 —' : '초과 ' + (excess >= 0 ? '+' : '') + excess.toFixed(2) + '%p';
        $('priceStatusChange').style.color = excess == null ? 'var(--ink-3)' : excess >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)';
        $('priceStatusMetrics').innerHTML = metric(name, signedPctPoint(sv)) + metric(benchName, signedPctPoint(bv)) + metric('초과수익률', excess == null ? '—' : (excess >= 0 ? '+' : '') + excess.toFixed(2) + '%p');
      } else {
        var gi = offset + i, prev = i === latestIndex && d.prev_close != null ? d.prev_close : (gi > 0 ? fullClose[gi - 1] : null);
        var delta = i === latestIndex && d.change != null ? d.change : (prev == null ? null : close[i] - prev);
        var pct = i === latestIndex && d.change_pct != null ? d.change_pct : (prev ? delta / prev : null);
        $('priceStatusPrice').textContent = fmtPrice(close[i]);
        $('priceStatusChange').textContent = delta == null ? '전일 대비 —' : (delta >= 0 ? '+' : '') + (CUR === 'KRW' ? fmtChartPrice(delta) + '원' : '$' + fmtChartPrice(delta)) + '  ' + fmtSigned(pct);
        $('priceStatusChange').style.color = delta == null ? 'var(--ink-3)' : delta >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)';
        $('priceStatusMetrics').innerHTML = metric('시가', fmtChartPrice(open[i])) + metric('고가', fmtChartPrice(high[i])) + metric('저가', fmtChartPrice(low[i])) + metric('종가', fmtChartPrice(close[i])) + metric('거래량', vol[i] == null ? '—' : Math.round(vol[i]).toLocaleString('en-US'));
      }
    }

    function strokeArr(arr, color, w) {
      var b = visibleBounds(); ctx.beginPath(); var started = false;
      for (var i = Math.max(0, b[0] - 1); i <= Math.min(n - 1, b[1] + 1); i++) { var v = arr[i]; if (v == null) { started = false; continue; } var sx = xDoc(i), sy = yDoc(v); if (!started) { ctx.moveTo(sx, sy); started = true; } else ctx.lineTo(sx, sy); }
      ctx.strokeStyle = color; ctx.lineWidth = w; ctx.lineJoin = 'round'; ctx.stroke();
    }
    /* 봉(캔들) 차트 — 시가 대비 종가로 양봉(초록)·음봉(빨강). 색은 앱 전반과 통일(초록=상승). */
    function drawCandles() {
      var b = visibleBounds(), visN = Math.max(1, viewEnd - viewStart);
      var step = visN > 520 ? 3 : visN > 260 ? 2 : 1, bw = Math.max(1, xw / visN * 0.62 * step);
      for (var i = Math.max(0, b[0]); i <= Math.min(n - 1, b[1]); i += step) {
        var o = open[i], hh = high[i], ll = low[i], c = close[i];
        if (c == null) continue;
        var up = (o == null) ? true : c >= o, col = up ? COL.positive : COL.negative, x = xDoc(i);
        if (hh != null && ll != null) { ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, yDoc(hh)); ctx.lineTo(x, yDoc(ll)); ctx.stroke(); }
        ctx.fillStyle = col;
        if (o != null) { var yO = yDoc(o), yC = yDoc(c), top = Math.min(yO, yC); ctx.fillRect(x - bw / 2, top, bw, Math.max(1, Math.abs(yC - yO))); }
        else ctx.fillRect(x - bw / 2, yDoc(c) - 0.75, bw, 1.5);
      }
    }
    function axisTag(text, x, y, align, bg, fg) {
      ctx.font = '10.5px ' + CH_FONT_MONO; var tw = Math.ceil(ctx.measureText(text).width) + 12, th = 19;
      var bx = align === 'right' ? x : x - tw / 2, by = y - th / 2;
      if (align !== 'right') bx = Math.max(padL, Math.min(padL + xw - tw, bx));
      ctx.fillStyle = bg; ctx.fillRect(Math.round(bx), Math.round(by), tw, th);
      ctx.fillStyle = fg; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text, bx + tw / 2, by + th / 2 + 0.5); ctx.textBaseline = 'alphabetic';
    }
    function draw() {
      scaleVisible();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, cssW, cssH);
      /* 가로 그리드 + 자동 가격축 */
      ctx.strokeStyle = COL.line; ctx.lineWidth = 1; ctx.font = '10.5px ' + CH_FONT_MONO; ctx.textAlign = 'left';
      for (var g = 0; g <= 4; g++) { var gy = plotT + g / 4 * plotH; ctx.beginPath(); ctx.moveTo(padL, gy + 0.5); ctx.lineTo(padL + xw, gy + 0.5); ctx.stroke(); ctx.fillStyle = COL.ink3; ctx.fillText(rel ? valAtY(gy).toFixed(1) : fmtChartPrice(valAtY(gy)), padL + xw + 6, gy + 3.5); }
      /* 시리즈 — 플롯 영역 클립 */
      ctx.save(); ctx.beginPath(); ctx.rect(padL, plotT, xw, (rel ? plotH : volTop + volH - plotT)); ctx.clip();
      if (!rel) {
        var vb = visibleBounds(), visibleN = Math.max(1, viewEnd - viewStart), step = visibleN > 520 ? 3 : visibleN > 260 ? 2 : 1, bw = Math.max(1, xw / visibleN * 0.62 * step);
        for (var i = vb[0]; i <= vb[1]; i += step) {
          if (vol[i] == null) continue;
          var h = vol[i] / vmax * volH, volumeColor = COL.slate;
          if (i > 0 && close[i] != null && close[i - 1] != null) volumeColor = close[i] < close[i - 1] ? COL.negative : COL.positive;
          ctx.globalAlpha = 0.34; ctx.fillStyle = volumeColor;
          ctx.fillRect(xDoc(i) - bw / 2, volTop + volH - h, bw, Math.max(0.5, h));
        }
        ctx.globalAlpha = 1;
        if (state.chartType === 'candle') {
          drawCandles();
          if (state.ma.m120) strokeArr(ma120, COL.plum, 1.3);
          if (state.ma.m60) strokeArr(ma60, COL.slate, 1.3);
          if (state.ma.m20) strokeArr(ma20, COL.gold, 1.3);
        } else {
          if (state.ma.m120) strokeArr(ma120, COL.plum, 1.3);
          if (state.ma.m60) strokeArr(ma60, COL.slate, 1.3);
          if (state.ma.m20) strokeArr(ma20, COL.gold, 1.3);
          strokeArr(close, COL.ink, 1.9);
        }
      } else { strokeArr(benchY, COL.clay, 1.4); strokeArr(stockY, COL.ink, 1.9); }
      /* 현재가 점선과 최신 데이터 포인트 */
      var latest = d.cur != null ? d.cur : close[latestIndex], lastVisible = latestIndex >= viewStart && latestIndex <= viewEnd;
      if (!rel && latest != null && latest >= ymin && latest <= ymax) {
        var lastY = yDoc(latest); ctx.strokeStyle = COL.ink2; ctx.lineWidth = 1; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(padL, lastY); ctx.lineTo(padL + xw, lastY); ctx.stroke(); ctx.setLineDash([]);
        if (lastVisible) { ctx.fillStyle = COL.ink; ctx.beginPath(); ctx.arc(xDoc(latestIndex), lastY, 3.4, 0, Math.PI * 2); ctx.fill(); }
      }
      if (hover != null && hover >= 0 && hover < n) {
        var yv = rel ? stockY[hover] : close[hover], hx = xDoc(hover), hy = yv == null ? null : yDoc(yv);
        ctx.strokeStyle = COL.ink3; ctx.lineWidth = 1; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(hx, plotT); ctx.lineTo(hx, rel ? plotT + plotH : volTop + volH); if (hy != null) { ctx.moveTo(padL, hy); ctx.lineTo(padL + xw, hy); } ctx.stroke(); ctx.setLineDash([]);
        if (hy != null) { ctx.fillStyle = COL.ink; ctx.beginPath(); ctx.arc(hx, hy, 3.2, 0, Math.PI * 2); ctx.fill(); }
      }
      ctx.restore();
      /* x축 날짜 라벨 */
      ctx.fillStyle = COL.ink3; ctx.font = '10px ' + CH_FONT_MONO; ctx.textAlign = 'center';
      var ly = (rel ? plotT + plotH : volTop + volH) + 15;
      for (var t = 0; t <= 4; t++) { var lx = padL + t / 4 * xw, labelDate = displayDate(dates[idxAtX(lx)]); ctx.fillText(viewEnd - viewStart > 300 ? labelDate.slice(2, 7) : labelDate.slice(5), lx, ly); }
      if (!rel) { ctx.fillStyle = COL.ink3; ctx.font = '10px ' + CH_FONT_SANS; ctx.textAlign = 'left'; ctx.fillText('거래량', padL, volTop - 4); }
      if (rel) { var benchName = D.meta.benchmark_name || D.meta.benchmark || '벤치마크'; ctx.textAlign = 'left'; ctx.font = '11px ' + CH_FONT_SANS; ctx.fillStyle = COL.ink; ctx.fillText(D.meta.name, padL + 2, plotT + 12); ctx.fillStyle = COL.clay; ctx.fillText(benchName, padL + 2 + ctx.measureText(D.meta.name).width + 10, plotT + 12); }
      /* 축 태그는 마크 위, 축 여백에 고정 */
      if (!rel && latest != null && latest >= ymin && latest <= ymax) axisTag(fmtChartPrice(latest), padL + xw + 3, yDoc(latest), 'right', COL.ink, COL.paper);
      if (hover != null && hover >= 0 && hover < n) {
        var hoverValue = rel ? stockY[hover] : close[hover], hoverX = xDoc(hover);
        if (hoverValue != null) axisTag(rel ? hoverValue.toFixed(2) : fmtChartPrice(hoverValue), padL + xw + 3, yDoc(hoverValue), 'right', COL.ink3, COL.paper);
        axisTag(displayDate(dates[hover]), hoverX, (rel ? plotT + plotH : volTop + volH) + 23, 'center', COL.ink3, COL.paper);
      }
    }

    /* 입력 응답은 즉시 그린다. 플링만 시간 기반으로 처리한다. */
    var raf = (window.requestAnimationFrame || function (f) { return setTimeout(f, 16); });
    var caf = (window.cancelAnimationFrame || clearTimeout);
    function dirty() { draw(); }

    /* 좌우 플링(관성) */
    var flingId = 0;
    function stopFling() { if (flingId) { caf(flingId); flingId = 0; } }
    function startFling(vx) {
      if (Math.abs(vx) < 0.6) return; stopFling();
      function stepF() { vx *= 0.92; var before = viewStart; panPixels(vx); if (viewStart === before) vx = 0; draw(); if (Math.abs(vx) > 0.25) flingId = raf(stepF); else flingId = 0; }
      flingId = raf(stepF);
    }
    function resetView() { stopFling(); viewStart = 0; viewEnd = n - 1; hover = null; updateStatus(latestIndex); draw(); }

    /* 이벤트 */
    function pos(e) { var r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    function onWheel(e) { e.preventDefault(); stopFling(); var p = pos(e); var factor = Math.exp(e.deltaY * (e.ctrlKey ? 0.009 : 0.0018)); factor = Math.max(0.58, Math.min(1.72, factor)); zoomAt(p[0], factor); dirty(); }
    var dragging = false, moved = false, lx = 0, vX = 0;
    function onDown(e) { cv.focus({ preventScroll: true }); cv.setPointerCapture && cv.setPointerCapture(e.pointerId); dragging = true; moved = false; lx = e.clientX; vX = 0; hover = null; updateStatus(latestIndex); stopFling(); }
    function onMove(e) {
      var p = pos(e);
      if (dragging) { var dx = e.clientX - lx; if (Math.abs(dx) > 1) moved = true; panPixels(dx); vX = dx; lx = e.clientX; dirty(); }
      else { if (p[0] < padL || p[0] > padL + xw || p[1] < plotT || p[1] > (rel ? plotT + plotH : volTop + volH)) { if (hover != null) { hover = null; updateStatus(latestIndex); dirty(); } } else { var i = idxAtX(p[0]); if (i !== hover) { hover = i; updateStatus(i); dirty(); } } }
    }
    function onUp() { if (dragging) { dragging = false; if (moved) startFling(vX); } }
    function onLeave() { if (!dragging && hover != null) { hover = null; updateStatus(latestIndex); dirty(); } }
    function onDbl(e) { e.preventDefault(); resetView(); }
    function onKey(e) {
      var handled = true, reset = false;
      if (e.key === 'ArrowLeft') panPixels(xw * 0.10);
      else if (e.key === 'ArrowRight') panPixels(-xw * 0.10);
      else if (e.key === '+' || e.key === '=') zoomAt(padL + xw / 2, 0.72);
      else if (e.key === '-' || e.key === '_') zoomAt(padL + xw / 2, 1.38);
      else if (e.key === 'Home' || e.key === 'Escape') { resetView(); reset = true; }
      else handled = false;
      if (handled) { e.preventDefault(); if (!reset) draw(); }
    }
    cv.addEventListener('wheel', onWheel, { passive: false });
    cv.addEventListener('pointerdown', onDown);
    cv.addEventListener('pointermove', onMove);
    cv.addEventListener('pointerup', onUp);
    cv.addEventListener('pointercancel', onUp);
    cv.addEventListener('pointerleave', onLeave);
    cv.addEventListener('dblclick', onDbl);
    cv.addEventListener('keydown', onKey);

    updateStatus(latestIndex); draw();
    return { reset: resetView, destroy: function () { stopFling(); cv.removeEventListener('wheel', onWheel); cv.removeEventListener('pointerdown', onDown); cv.removeEventListener('pointermove', onMove); cv.removeEventListener('pointerup', onUp); cv.removeEventListener('pointercancel', onUp); cv.removeEventListener('pointerleave', onLeave); cv.removeEventListener('dblclick', onDbl); cv.removeEventListener('keydown', onKey); } };
  }

  var priceChartInst = null;
  function renderPrice() {
    var wrap = $('priceChart');
    if (priceChartInst) { priceChartInst.destroy(); priceChartInst = null; }
    if (!D || !D.price || D.price.error) { wrap.innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:20px 0">주가 데이터를 불러오지 못했습니다.</div>'; return; }
    if (!wrap.clientWidth) return;  // 숨김 상태(탭 비활성) — 탭 활성화 때 다시 그린다
    priceChartInst = makePriceChart(wrap, D, state);
  }

  function bandChart() {
    var b = D.band[state.bandMetric.toLowerCase()];
    if (!b) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px', padding: '20px 0' } }, '밴드를 계산할 수 없습니다 (상장기간 부족 또는 적자).');
    var M = b.price.length, price = b.price, lo = b.q10 || b.q25, mid = b.q50, hi = b.q90 || b.q75;
    var W = 760, padL = 6, padR = 58, plotT = 10, plotH = 228, xw = W - padL - padR;
    var X = function (i) { return padL + (M <= 1 ? 0 : i / (M - 1) * xw); };
    var all = lo.concat(hi, price).filter(function (v) { return v != null; }); var ymin = Math.min.apply(null, all), ymax = Math.max.apply(null, all); var padv = (ymax - ymin) * 0.06; ymin -= padv; ymax += padv;
    var Y = function (v) { return plotT + (1 - (v - ymin) / (ymax - ymin)) * plotH; };
    var line = function (a) { var p = ''; for (var i = 0; i < a.length; i++) { if (a[i] == null) continue; p += (p ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(a[i]).toFixed(1) + ' '; } return p; };
    var area = ''; for (var i = 0; i < M; i++) area += (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(hi[i]).toFixed(1) + ' '; for (var k = M - 1; k >= 0; k--) area += 'L' + X(k).toFixed(1) + ' ' + Y(lo[k]).toFixed(1) + ' '; area += 'Z';
    var els = [];
    for (var g = 0; g <= 3; g++) { var yy = plotT + g / 3 * plotH, val = ymax - (ymax - ymin) * g / 3; els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL + xw + 6, y: yy + 3.5, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)' }, Math.round(val).toLocaleString('en-US'))); }
    els.push(el('path', { d: area, fill: 'var(--dv-slate)', fillOpacity: 0.12, stroke: 'none' }));
    els.push(el('path', { d: line(hi), fill: 'none', stroke: 'var(--dv-slate)', strokeWidth: 1, opacity: 0.55 }));
    els.push(el('path', { d: line(lo), fill: 'none', stroke: 'var(--dv-slate)', strokeWidth: 1, opacity: 0.55 }));
    if (mid) els.push(el('path', { d: line(mid), fill: 'none', stroke: 'var(--dv-slate)', strokeWidth: 1, strokeDasharray: '4 3' }));
    els.push(el('path', { d: line(price), fill: 'none', stroke: 'var(--dv-navy)', strokeWidth: 1.9 }));
    els.push(el('circle', { cx: X(M - 1), cy: Y(price[M - 1]), r: 3.6, fill: 'var(--dv-navy)' }));
    els.push(el('text', { x: padL + 4, y: Y(hi[Math.round(M * 0.2)]) - 5, fontSize: 10, fill: 'var(--dv-slate)', fontFamily: 'var(--font-sans)' }, '90분위'));
    els.push(el('text', { x: padL + 4, y: Y(lo[Math.round(M * 0.2)]) + 13, fontSize: 10, fill: 'var(--dv-slate)', fontFamily: 'var(--font-sans)' }, '10분위'));
    for (var t = 0; t <= 5; t++) { var ii = Math.round(t / 5 * (M - 1)); els.push(el('text', { x: X(ii), y: plotT + plotH + 16, fontSize: 10, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, b.dates[ii])); }
    return el('svg', { viewBox: '0 0 ' + W + ' ' + (plotT + plotH + 24), style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }
  function renderBand() {
    $('bandChart').innerHTML = bandChart();
    var b = D.band[state.bandMetric.toLowerCase()];
    var cap = $('bandCaption');
    if (b && b.percentile != null) { var p = b.percentile; cap.innerHTML = '밴드는 5년 배수 분포의 10–90분위를 펀더멘털(EPS/BPS)에 곱한 가격대. 주가(네이비 선)가 위쪽 선에 가까울수록 역사적으로 비쌈. 현재 배수는 5년 분포 <b style="color:var(--ink-2)">하위 ' + p.toFixed(0) + '%</b> — ' + (p < 35 ? '역사적으로도 싼 구간.' : p > 65 ? '역사적으로 비싼 구간.' : '중간 구간.'); }
    else cap.textContent = '';
  }

  /* 범용 그룹막대 / 멀티라인 */
  function barGroups(labels, series, opt) {
    opt = opt || {}; var W = opt.W || 760, H = opt.H || 230, padL = 6, padR = 46, top = 16, plotH = H - 46, xw = W - padL - padR, n = labels.length, g = series.length;
    var vmax = 0, vmin = 0; series.forEach(function (s) { s.data.forEach(function (v) { if (v > vmax) vmax = v; if (v < vmin) vmin = v; }); });
    var rng = vmax - vmin || 1, Y = function (v) { return top + (1 - (v - vmin) / rng) * plotH; }, slot = xw / n, bw = Math.min(26, (slot * 0.62) / g);
    var els = [];
    for (var gg = 0; gg <= 3; gg++) { var val = vmax - (vmax - vmin) * gg / 3, yy = Y(val); els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL + xw + 6, y: yy + 3.5, fontSize: 10, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)' }, opt.fmt ? opt.fmt(val) : val.toFixed(1))); }
    if (vmin < 0) { var zy = Y(0); els.push(el('line', { x1: padL, x2: padL + xw, y1: zy, y2: zy, stroke: 'var(--ink-3)', strokeWidth: 1 })); }
    labels.forEach(function (lb, i) { var cx = padL + slot * i + slot / 2; series.forEach(function (s, si) { var v = s.data[i]; if (v == null) return; var bx = cx - (g * bw) / 2 + si * bw, y0 = Y(Math.max(0, v)), y1 = Y(Math.min(0, v)); els.push(el('rect', { x: bx, y: y0, width: bw - 2, height: Math.max(1, y1 - y0), fill: s.color, rx: 1 })); }); els.push(el('text', { x: cx, y: top + plotH + 16, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, lb)); });
    var lg = el('div', { style: { display: 'flex', gap: '16px', marginTop: '8px', flexWrap: 'wrap' } }, series.map(function (s) { return el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--ink-2)' } }, el('span', { style: { width: '10px', height: '10px', borderRadius: '2px', background: s.color, display: 'inline-block' } }), s.name); }).join(''));
    return el('div', {}, el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els), lg);
  }
  function lineMulti(labels, series, opt) {
    opt = opt || {}; var W = opt.W || 760, H = opt.H || 220, padL = 6, padR = 46, top = 14, plotH = H - 42, xw = W - padL - padR, n = labels.length;
    var vmax = -1e9, vmin = 1e9; series.forEach(function (s) { s.data.forEach(function (v) { if (v == null) return; if (v > vmax) vmax = v; if (v < vmin) vmin = v; }); });
    if (vmax < vmin) { vmax = 1; vmin = 0; }
    var pad = (vmax - vmin) * 0.12 || 1; vmax += pad; vmin -= pad;
    var X = function (i) { return padL + (n <= 1 ? 0 : i / (n - 1) * xw); }, Y = function (v) { return top + (1 - (v - vmin) / (vmax - vmin)) * plotH; };
    var els = [];
    for (var gg = 0; gg <= 3; gg++) { var val = vmax - (vmax - vmin) * gg / 3, yy = Y(val); els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL + xw + 6, y: yy + 3.5, fontSize: 10, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)' }, opt.fmt ? opt.fmt(val) : val.toFixed(0))); }
    series.forEach(function (s) { var p = ''; s.data.forEach(function (v, i) { if (v == null) return; p += (p ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1) + ' '; }); els.push(el('path', { d: p, fill: 'none', stroke: s.color, strokeWidth: 1.8 })); var last = s.data.length - 1; if (s.data[last] != null) els.push(el('circle', { cx: X(last), cy: Y(s.data[last]), r: 3, fill: s.color })); });
    labels.forEach(function (lb, i) { if (i % Math.ceil(n / 6) === 0 || i === n - 1) els.push(el('text', { x: X(i), y: top + plotH + 16, fontSize: 10, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, lb)); });
    // hover: 인덱스별 투명 밴드 → 세로 가이드 + 라벨·시리즈 값 표시 (.lm-hband CSS가 토글)
    var step = n <= 1 ? xw : xw / (n - 1);
    labels.forEach(function (lb, i) {
      var hx = X(i);
      var anchor = hx > padL + xw * 0.62 ? 'end' : 'start';
      var tx = anchor === 'end' ? hx - 7 : hx + 7;
      var hv = [el('line', { x1: hx, x2: hx, y1: top, y2: top + plotH, stroke: 'var(--ink-3)', strokeWidth: 1, strokeDasharray: '3 3' }),
                el('text', { x: tx, y: top + 11, fontSize: 10.5, fontWeight: 700, fill: 'var(--ink)', fontFamily: 'var(--font-mono)', textAnchor: anchor }, esc(lb))];
      series.forEach(function (s, si) {
        var v = s.data[i];
        if (v != null) hv.push(el('circle', { cx: hx, cy: Y(v), r: 3.5, fill: s.color, stroke: 'var(--paper)', strokeWidth: 1.2 }));
        hv.push(el('text', { x: tx, y: top + 11 + 13 * (si + 1), fontSize: 10.5, fontWeight: 600, fill: s.color, fontFamily: 'var(--font-mono)', textAnchor: anchor },
          esc(s.name.replace(' %', '')) + ' ' + (v == null ? '—' : (opt.fmt ? opt.fmt(v) : String(v)))));
      });
      els.push(el('g', { className: 'lm-hband' },
        el('rect', { x: hx - step / 2, y: top - 6, width: step, height: plotH + 26, fill: 'transparent' }),
        el('g', { className: 'lm-hv' }, hv)));
    });
    var lg = el('div', { style: { display: 'flex', gap: '16px', marginTop: '8px', flexWrap: 'wrap' } }, series.map(function (s) { return el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--ink-2)' } }, el('span', { style: { width: '12px', height: '2px', background: s.color, display: 'inline-block' } }), s.name); }).join(''));
    return el('div', {}, el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els), lg);
  }

  /* 축 눈금 간격 — 1·2·5·10 계열의 읽기 좋은 값으로 */
  function niceStep(range, target) {
    var raw = Math.max(range, 1e-9) / target;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), n = raw / mag;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
  }

  /* 가격과 수익성 지도 — 주가차트와 같은 문법(헤어라인 그리드·모노 축라벨·절제된 팔레트).
     업종 중앙값 십자선이 사분면을 정의하고, 라벨은 겹치면 숨겼다가 hover 때 드러낸다. */
  function peerScatter() {
    var pts = (D.peers && D.peers.scatter) || [];
    if (pts.length < 2) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px' } }, '피어 표본이 부족합니다.');
    var W = 760, H = 438, padL = 58, padR = 26, padT = 26, padB = 56;
    var xw = W - padL - padR, plotH = H - padT - padB;
    var perArr = pts.map(function (p) { return p.per; }), roeArr = pts.map(function (p) { return p.roe; });
    var xStep = niceStep(Math.max.apply(null, perArr) * 1.08, 5);
    var perMax = Math.max(xStep, Math.ceil(Math.max.apply(null, perArr) * 1.08 / xStep) * xStep);
    var rHi = Math.max.apply(null, roeArr), rLo = Math.min(0, Math.min.apply(null, roeArr));
    var span = (rHi - rLo) || 1, yStep = niceStep(span * 1.2, 5);
    var yTop = Math.ceil((rHi + span * 0.1) / yStep) * yStep;
    var yBot = Math.floor((rLo - (rLo < 0 ? span * 0.1 : 0)) / yStep) * yStep;
    if (yTop <= yBot) yTop = yBot + yStep;
    var X = function (v) { return padL + Math.max(0, Math.min(perMax, v)) / perMax * xw; };
    var Y = function (v) { return padT + (1 - (Math.max(yBot, Math.min(yTop, v)) - yBot) / (yTop - yBot)) * plotH; };
    var med = function (a) { var s = a.slice().sort(function (x, y) { return x - y; }), m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
    // 축 범위는 자사를 포함해 잡고(자사 점이 화면 밖으로 나가면 안 된다), **업종 중앙 십자선은
    // 자사를 뺀 피어만으로** 낸다. 자사를 넣으면 중앙선이 자사 쪽으로 끌려 실제보다 업종에
    // 가까워 보이고, 표본이 홀수면 중앙값이 자사 자신이 되어 가로선이 자기 점 위에 그려진다
    // (삼성전자 실측: 피어 9개에서 ROE 십자선 18.86% = 자사 ROE). 서버의 peer_median도
    // exclude_self=True로 자사를 뺀다 — 같은 개념을 두 곳에서 계산하다 표본 정의가 갈렸다.
    var peerOnly = pts.filter(function (p) { return !p.self; });
    var medBase = peerOnly.length ? peerOnly : pts;
    var medPer = med(medBase.map(function (p) { return p.per; }));
    var medRoe = med(medBase.map(function (p) { return p.roe; }));
    var els = [];
    // 저PER·고ROE 사분면 — 의미색은 아주 옅게만
    els.push(el('rect', { x: padL, y: padT, width: X(medPer) - padL, height: Y(medRoe) - padT, fill: 'var(--dv-green)', fillOpacity: 0.05 }));
    // 그리드 — 주가차트와 같은 헤어라인
    for (var gv = yBot; gv <= yTop + 1e-9; gv += yStep) {
      els.push(el('line', { x1: padL, x2: padL + xw, y1: Y(gv), y2: Y(gv), stroke: 'var(--line)', strokeWidth: 1 }));
      els.push(el('text', { x: padL - 9, y: Y(gv) + 3.5, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'end' }, Math.round(gv) + '%'));
    }
    for (var gx = xStep; gx <= perMax + 1e-9; gx += xStep) {
      els.push(el('line', { x1: X(gx), x2: X(gx), y1: padT, y2: padT + plotH, stroke: 'var(--line)', strokeWidth: 1, opacity: 0.6 }));
    }
    for (var tx = 0; tx <= perMax + 1e-9; tx += xStep) {
      els.push(el('text', { x: X(tx), y: padT + plotH + 19, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, Math.round(tx) + '×'));
    }
    // 적자 경계(0%) — 아래로 내려간 피어가 있을 때만
    if (yBot < 0) els.push(el('line', { x1: padL, x2: padL + xw, y1: Y(0), y2: Y(0), stroke: 'var(--line-strong)', strokeWidth: 1 }));
    els.push(el('line', { x1: padL, x2: padL + xw, y1: padT + plotH, y2: padT + plotH, stroke: 'var(--line-strong)', strokeWidth: 1 }));
    els.push(el('line', { x1: padL, x2: padL, y1: padT, y2: padT + plotH, stroke: 'var(--line-strong)', strokeWidth: 1 }));
    // 업종 중앙값 십자선 — 사분면의 실제 기준
    els.push(el('line', { x1: X(medPer), x2: X(medPer), y1: padT, y2: padT + plotH, stroke: 'var(--dv-slate)', strokeWidth: 1, strokeDasharray: '4 3', opacity: 0.75 }));
    els.push(el('line', { x1: padL, x2: padL + xw, y1: Y(medRoe), y2: Y(medRoe), stroke: 'var(--dv-slate)', strokeWidth: 1, strokeDasharray: '4 3', opacity: 0.75 }));
    els.push(el('text', { x: X(medPer) + 5, y: padT + plotH - 6, fontSize: 9.5, fill: 'var(--dv-slate)', fontFamily: 'var(--font-mono)' }, '업종 중앙 ' + medPer.toFixed(1) + '×'));
    els.push(el('text', { x: padL + xw - 3, y: Y(medRoe) - 5, fontSize: 9.5, fill: 'var(--dv-slate)', fontFamily: 'var(--font-mono)', textAnchor: 'end' }, '업종 중앙 ' + medRoe.toFixed(1) + '%'));
    els.push(el('text', { x: padL + 8, y: padT + 15, fontSize: 11, fill: 'var(--dv-green)', fontFamily: 'var(--font-sans)', fontWeight: 600 }, '저PER · 고ROE'));
    // 점 = 클릭 가능한 그룹(data-q 검색키 · data-key 매칭키). 넓은 투명 히트원으로 클릭/hover 쉬움.
    // 라벨 자리는 본인부터 잡고(위→아래→오른→왼), 자리가 없으면 숨겨 hover 때만 보여준다.
    var boxes = [], rendered = [];
    function collides(r) {
      if (r.x < padL || r.x + r.w > padL + xw || r.y < padT || r.y + r.h > padT + plotH) return true;
      for (var i = 0; i < boxes.length; i++) {
        var q = boxes[i];
        if (r.x < q.x + q.w && r.x + r.w > q.x && r.y < q.y + q.h && r.y + r.h > q.y) return true;
      }
      return false;
    }
    pts.slice().sort(function (a, b) { return (b.self ? 1 : 0) - (a.self ? 1 : 0); }).forEach(function (p) {
      var cx = X(p.per), cy = Y(p.roe), fs = p.self ? 12.5 : 11.5, r = p.self ? 7 : 5;
      var name = String(p.n || ''), w = Math.max(20, name.length * fs * 0.62), h = fs + 3;
      var cands = [
        { bx: cx - w / 2, by: cy - r - 7 - h, tx: cx, ty: cy - r - 9, an: 'middle' },
        { bx: cx - w / 2, by: cy + r + 5, tx: cx, ty: cy + r + 6 + fs, an: 'middle' },
        { bx: cx + r + 6, by: cy - h / 2, tx: cx + r + 7, ty: cy + 4, an: 'start' },
        { bx: cx - r - 6 - w, by: cy - h / 2, tx: cx - r - 7, ty: cy + 4, an: 'end' }
      ];
      var spot = null;
      for (var i = 0; i < cands.length; i++) {
        var c = cands[i];
        if (!collides({ x: c.bx, y: c.by, w: w, h: h })) { spot = c; boxes.push({ x: c.bx, y: c.by, w: w, h: h }); break; }
      }
      var c0 = spot || cands[0];
      var kids = [
        el('title', {}, esc(name) + ' · PER ' + p.per.toFixed(1) + '× · ROE ' + p.roe.toFixed(1) + '%'),
        el('circle', { cx: cx, cy: cy, r: 16, fill: 'transparent', className: 'hit' }),
        el('circle', { cx: cx, cy: cy, r: r, fill: p.self ? 'var(--dv-navy)' : 'var(--paper)', stroke: p.self ? 'var(--dv-navy)' : 'var(--line-strong)', strokeWidth: 1.6, className: 'dot' }),
        el('text', { x: c0.tx, y: c0.ty, fontSize: fs, fontWeight: p.self ? 700 : 500, fill: p.self ? 'var(--ink)' : 'var(--ink-2)', fontFamily: 'var(--font-sans)', textAnchor: c0.an, className: 'lbl' + (spot ? '' : ' lbl-off') }, esc(name))
      ];
      rendered.push({ self: !!p.self, html: el('g', { className: 'pt', 'data-q': p.q || '', 'data-key': p.key || '', style: { cursor: 'pointer' } }, kids) });
    });
    // 본인 점을 맨 위에 그린다(SVG는 나중에 그린 것이 위)
    rendered.sort(function (a, b) { return (a.self ? 1 : 0) - (b.self ? 1 : 0); }).forEach(function (x) { els.push(x.html); });
    els.push(el('text', { x: padL + xw, y: padT + plotH + 40, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, 'PER (배) →'));
    els.push(el('text', { x: padL - 47, y: padT - 9, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)' }, '↑ ROE (%)'));
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }

  /* ── 종목 자동완성(타입어헤드) — 증권사 검색창처럼 타이핑에 맞는 종목을 밑에 띄운다 ── */
  function attachAutocomplete(input, onPick) {
    if (!input) return;
    var box = document.createElement('div');
    box.className = 'ac-drop';
    box.style.display = 'none';
    input.parentNode.appendChild(box);   // 부모 form이 position:relative
    var items = [], sel = -1, seq = 0, timer = null, lastQ = null;

    function close() { box.style.display = 'none'; items = []; sel = -1; }
    function render() {
      if (!items.length) { close(); return; }
      box.innerHTML = items.map(function (it, i) {
        return '<div class="ac-item' + (i === sel ? ' on' : '') + '" data-i="' + i + '">' +
          '<span class="ac-name">' + esc(it.name) + '</span>' +
          '<span class="ac-code">' + esc(it.code) + '</span>' +
          '<span class="ac-sub' + (it.kind === 'etf' ? ' ac-etf' : '') + '">' + esc(it.sub || '') + '</span></div>';
      }).join('');
      box.style.display = 'block';
    }
    function pick(i) { var it = items[i]; if (!it) return; close(); onPick(it); }
    function fetchSuggest(q) {
      var my = ++seq;
      fetch('api/suggest?market=' + encodeURIComponent(state.market) + '&q=' + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (my !== seq || input !== document.activeElement) return; // 최신 입력·포커스 유지 시만
          items = (d && d.items) || []; sel = -1; render();
        }).catch(function () { /* 자동완성 실패는 조용히 무시 — 직접 입력은 계속 가능 */ });
    }
    input.addEventListener('input', function () {
      var q = input.value.trim();
      if (q === lastQ) return; lastQ = q;
      clearTimeout(timer);
      if (q.length < 1) { close(); return; }
      timer = setTimeout(function () { fetchSuggest(q); }, 140);
    });
    input.addEventListener('keydown', function (e) {
      if (box.style.display === 'none' || !items.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); sel = (sel + 1) % items.length; render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); sel = (sel - 1 + items.length) % items.length; render(); }
      else if (e.key === 'Enter') { if (sel >= 0) { e.preventDefault(); pick(sel); } }   // 미선택 시 form 제출(직접 입력)
      else if (e.key === 'Escape') { close(); }
    });
    // mousedown은 preventDefault로 input blur만 막고, 실제 선택은 click에서 — 표준 패턴
    box.addEventListener('mousedown', function (e) { e.preventDefault(); });
    box.addEventListener('click', function (e) { var t = e.target.closest('.ac-item'); if (!t) return; pick(+t.getAttribute('data-i')); });
    input.addEventListener('blur', function () { setTimeout(close, 120); });
    input.addEventListener('focus', function () { var q = input.value.trim(); if (q && box.style.display === 'none') fetchSuggest(q); });
  }

  // 자동완성 선택 처리 — 주식은 9탭 기업분석, ETF는 3축 ETF 분석으로 간다.
  // (같은 페이지에서 kind만 갈아끼운다 — load()가 kind를 보고 엔드포인트를 고른다.)
  function pickTicker(it) {
    if (!it) return;
    state.kind = it.kind === 'etf' ? 'etf' : 'stock';
    state.query = it.code; $('tickerInput').value = it.code; load();
  }
  // Enter로 직접 친 질의도 ETF(코드·심볼·이름 정확 일치)면 ETF 분석으로 보낸다 —
  // 안 그러면 '연간 손익계산서를 못 찾았습니다' 같은 엉뚱한 오류를 보게 된다.
  // suggest는 서버 캐시라 이 한 번의 왕복 비용이 거의 없다.
  function submitQuery(q) {
    if (!q) return;
    var qq = q.toUpperCase();
    function go(kind) { state.kind = kind; state.query = q; $('tickerInput').value = q; load(); }
    fetch('api/suggest?market=' + encodeURIComponent(state.market) + '&q=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var items = (d && d.items) || [];
        for (var i = 0; i < items.length; i++) {
          var it = items[i];
          if (it.kind === 'etf' && (String(it.code).toUpperCase() === qq || String(it.name).toUpperCase() === qq)) { go('etf'); return; }
        }
        go('stock');
      })
      .catch(function () { go('stock'); });   // 자동완성이 죽어도 분석 자체는 막지 않는다
  }
  /* 점·표 클릭 → 재검색, 점 hover ↔ 좌측 피어표 행 상호 하이라이트 */
  function _searchTo(q) { if (!q) return; state.query = q; var ti = $('tickerInput'); if (ti) ti.value = q; state.hover = null; load(); }
  function _setLinked(key, on) {
    if (!key) return;
    ['peerScatter', 'peerTable'].forEach(function (id) {
      var c = $(id); if (!c) return; var els = c.querySelectorAll('[data-key]');
      for (var i = 0; i < els.length; i++) if (els[i].getAttribute('data-key') === key) els[i].classList.toggle('linked', on);
    });
  }
  /* 피어 편집 배선 — X(제외)·＋(추가)·초기화. 서버가 캐시된 원자료로 재계산하므로 수 초면 끝난다. */
  function wirePeerEdit() {
    var tbl = $('peerTable');
    tbl.querySelectorAll('.peer-x').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        var k = b.getAttribute('data-ex');
        if (k && state.peerEx.indexOf(k) < 0) { state.peerEx.push(k); load(); }
      });
    });
    var addBtn = tbl.querySelector('.peer-addbtn'), inp = tbl.querySelector('.peer-addinput');
    if (addBtn && inp) {
      addBtn.addEventListener('click', function () { inp.style.display = 'inline-block'; inp.focus(); });
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          var v = inp.value.trim();
          if (v && state.peerAdd.indexOf(v) < 0) { state.peerAdd.push(v); load(); }
        } else if (e.key === 'Escape') { inp.value = ''; inp.style.display = 'none'; }
      });
    }
    var rst = tbl.querySelector('.peer-reset');
    if (rst) rst.addEventListener('click', function () { state.peerEx = []; state.peerAdd = []; load(); });
  }

  function wirePeerLinks() {
    ['peerScatter', 'peerTable', 'rankTable'].forEach(function (id) {
      var c = $(id); if (!c || c._wired) return; c._wired = true;
      c.addEventListener('click', function (e) { var t = e.target.closest('[data-q]'); if (t && t.getAttribute('data-q')) _searchTo(t.getAttribute('data-q')); });
    });
    ['peerScatter', 'peerTable'].forEach(function (id) {
      var c = $(id); if (!c || c._hovered) return; c._hovered = true;
      c.addEventListener('mouseover', function (e) { var t = e.target.closest('[data-key]'); if (t) _setLinked(t.getAttribute('data-key'), true); });
      c.addEventListener('mouseout', function (e) { var t = e.target.closest('[data-key]'); if (t) _setLinked(t.getAttribute('data-key'), false); });
    });
  }

  function betaScatter() {
    var w = D.wacc; var pts = (w && w.reg_points) || [];
    if (pts.length < 10) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px' } }, '베타 회귀 표본이 부족합니다.');
    var beta = w.beta_line || w.beta_l || 1;
    var lim = Math.max.apply(null, pts.map(function (p) { return Math.max(Math.abs(p[0] || 0), Math.abs(p[1] || 0)); })) * 1.05 || 0.06;
    var W = 520, H = 300, pad = 40, top = 12, plotH = H - 42, xw = W - pad - 16;
    var X = function (v) { return pad + (v + lim) / (2 * lim) * xw; }, Y = function (v) { return top + (1 - (v + lim) / (2 * lim)) * plotH; };
    var els = [];
    els.push(el('line', { x1: pad, x2: pad + xw, y1: Y(0), y2: Y(0), stroke: 'var(--line-strong)', strokeWidth: 1 }));
    els.push(el('line', { x1: X(0), x2: X(0), y1: top, y2: top + plotH, stroke: 'var(--line-strong)', strokeWidth: 1 }));
    pts.forEach(function (p) { if (p[0] == null || p[1] == null) return; els.push(el('circle', { cx: X(p[0]), cy: Y(p[1]), r: 2.4, fill: 'var(--dv-slate)', fillOpacity: 0.55 })); });
    els.push(el('line', { x1: X(-lim), y1: Y(beta * -lim), x2: X(lim), y2: Y(beta * lim), stroke: 'var(--dv-navy)', strokeWidth: 2 }));
    els.push(el('text', { x: pad + xw - 6, y: top + 16, fontSize: 12, fill: 'var(--dv-navy)', fontFamily: 'var(--font-sans)', fontWeight: 600, textAnchor: 'end' }, 'β = ' + (w.beta_l != null ? w.beta_l.toFixed(2) : '—') + (w.r2 != null ? '  R²=' + w.r2.toFixed(2) : '')));
    els.push(el('text', { x: pad + xw, y: H - 4, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, '시장(' + D.meta.benchmark + ') 주간수익률 →'));
    els.push(el('text', { x: pad - 30, y: top + 6, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)' }, '종목 수익률'));
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }

  function waccWaterfall() {
    var w = D.wacc;
    if (!w || w.wacc == null) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px' } }, '금융업 등은 WACC가 의미를 갖지 않아 생략합니다.');
    var rf = w.rf * 100, ke = w.k_e * 100, kd = (w.k_d_after != null ? w.k_d_after : 0) * 100, wacc = w.wacc * 100;
    var vmax = Math.ceil(Math.max(ke, wacc, kd, rf) * 1.15);
    var W = 560, H = 250, padL = 6, top = 20, plotH = H - 70, xw = W - padL - 40, slot = xw / 5, bw = 52;
    var Y = function (v) { return top + (1 - v / vmax) * plotH; };
    var els = [];
    for (var g = 0; g <= 4; g++) { var val = vmax - vmax * g / 4, yy = Y(val); els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL + xw + 6, y: yy + 3.5, fontSize: 10, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)' }, val.toFixed(0) + '%')); }
    var cols = [{ label: '무위험 R_f', top: rf, bot: 0, c: 'var(--dv-slate)' }, { label: '+ β·MRP', top: ke, bot: rf, c: 'var(--dv-teal)' }, { label: 'k_e (CAPM)', top: ke, bot: 0, c: 'var(--dv-navy)' }, { label: 'k_d 세후', top: kd, bot: 0, c: 'var(--dv-clay)' }, { label: 'WACC', top: wacc, bot: 0, c: 'var(--ink)' }];
    cols.forEach(function (c, i) { var cx = padL + slot * i + slot / 2 - bw / 2, y0 = Y(c.top), y1 = Y(c.bot); els.push(el('rect', { x: cx, y: y0, width: bw, height: Math.max(2, y1 - y0), fill: c.c, rx: 1 })); els.push(el('text', { x: cx + bw / 2, y: y0 - 6, fontSize: 11, fill: 'var(--ink)', fontFamily: 'var(--font-mono)', fontWeight: 600, textAnchor: 'middle' }, c.top.toFixed(1) + '%')); els.push(el('text', { x: cx + bw / 2, y: top + plotH + 16, fontSize: 10, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'middle' }, c.label)); });
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }

  function roicSeries() {
    var w = D.wacc; var rs = w && w.roic_series;
    if (!rs || !rs.y.length) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px' } }, 'ROIC 시계열을 계산할 수 없습니다.');
    var years = rs.x, roic = rs.y.map(function (v) { return v == null ? null : v * 100; });
    var wacc = w.wacc != null ? years.map(function () { return w.wacc * 100; }) : null;
    var series = [{ name: 'ROIC', color: 'var(--dv-navy)', data: roic }];
    if (wacc) series.push({ name: 'WACC', color: 'var(--dv-clay)', data: wacc });
    return lineMulti(years, series, { fmt: function (v) { return v.toFixed(0) + '%'; }, H: 200 });
  }

  function backtestScatter() {
    var bt = D.backtest; var pts = (bt && bt.scatter) || [];
    if (pts.length < 10) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px' } }, '백테스트 표본이 부족합니다.');
    var xs = pts.map(function (p) { return p[0]; }), ys = pts.map(function (p) { return p[1]; });
    var xMin = Math.min(-10, Math.min.apply(null, xs)), xMax = Math.max(50, Math.max.apply(null, xs));
    var yMin = Math.min(-40, Math.min.apply(null, ys)), yMax = Math.max(60, Math.max.apply(null, ys));
    var W = 560, H = 300, padL = 42, top = 12, plotH = H - 46, xw = W - padL - 16;
    var X = function (v) { return padL + (v - xMin) / (xMax - xMin) * xw; }, Y = function (v) { return top + (1 - (v - yMin) / (yMax - yMin)) * plotH; };
    var th = (bt.threshold || 0.3) * 100;
    var els = [];
    els.push(el('line', { x1: X(0), x2: X(0), y1: top, y2: top + plotH, stroke: 'var(--line-strong)', strokeWidth: 1 }));
    els.push(el('line', { x1: padL, x2: padL + xw, y1: Y(0), y2: Y(0), stroke: 'var(--line-strong)', strokeWidth: 1 }));
    els.push(el('rect', { x: X(th), y: top, width: padL + xw - X(th), height: plotH, fill: 'var(--dv-green)', fillOpacity: 0.06 }));
    els.push(el('text', { x: X(th) + 6, y: top + 14, fontSize: 10, fill: 'var(--dv-green)', fontFamily: 'var(--font-sans)' }, '저평가 +' + th.toFixed(0) + '%↑ 구간'));
    pts.forEach(function (p) { els.push(el('circle', { cx: X(p[0]), cy: Y(Math.max(yMin, Math.min(yMax, p[1]))), r: 2.6, fill: 'var(--dv-navy)', fillOpacity: 0.5 })); });
    // OLS 회귀선
    var n = xs.length, mx = xs.reduce(function (a, b) { return a + b; }, 0) / n, my = ys.reduce(function (a, b) { return a + b; }, 0) / n, num = 0, den = 0;
    for (var i = 0; i < n; i++) { num += (xs[i] - mx) * (ys[i] - my); den += (xs[i] - mx) * (xs[i] - mx); }
    if (den > 0) { var slope = num / den, b0 = my - slope * mx; els.push(el('line', { x1: X(xMin), y1: Y(slope * xMin + b0), x2: X(xMax), y2: Y(slope * xMax + b0), stroke: 'var(--dv-clay)', strokeWidth: 1.8 })); }
    [-40, 0, 20, 40, 60].forEach(function (t) { if (t < yMin || t > yMax) return; els.push(el('text', { x: padL - 6, y: Y(t) + 3, fontSize: 9.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'end' }, t + '%')); });
    [0, 20, 40].forEach(function (t) { els.push(el('text', { x: X(t), y: top + plotH + 15, fontSize: 9.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, t + '%')); });
    els.push(el('text', { x: padL + xw, y: H - 2, fontSize: 10, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, '저평가율 →'));
    els.push(el('text', { x: padL - 30, y: top + 6, fontSize: 10, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)' }, '12M 수익'));
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }

  function equityCurve() {
    var eq = D.backtest && D.backtest.equity;
    if (!eq) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px' } }, '자산곡선을 계산할 수 없습니다.');
    var colors = ['var(--dv-navy)', 'var(--dv-slate)', 'var(--dv-clay)'];
    var series = eq.series.map(function (s, i) { var c = eq.cagr[s.name]; var lbl = s.name + (c != null ? ' (CAGR ' + (c * 100).toFixed(1) + '%)' : ''); return { name: lbl, color: colors[i % 3], data: s.y }; });
    return lineMulti(eq.dates, series, { fmt: function (v) { return v.toFixed(0); }, H: 240 });
  }

  /* ══════════ 섹션 렌더 (HTML) ══════════ */

  function badge(text, tone) { return el('span', { className: 'badge' + (tone ? ' badge-' + tone : '') }, esc(text)); }

  function renderHeader() {
    var m = D.meta, v = D.verdict;
    var initial = /[A-Za-z]/.test(m.name[0]) ? m.name[0].toUpperCase() : m.name[0];
    var tone = vTone(v.verdict), pos = vPos(v.verdict), gapCol = v.gap != null && v.gap >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)';
    var mono = 'var(--font-mono)', disp = 'var(--font-display)';
    var sub = [m.sector, m.industry].filter(Boolean).join(' · ');
    // 신뢰도 툴팁 — 산출 근거(방법 간 편차 = 변동계수)를 실제 수치로 설명
    var nMeth = (v.estimates || []).length;
    // 신뢰도는 '값이 모인 정도'이지 '독립적으로 합의한 정도'가 아니다. ②와 ④는 같은 배수
    // (자기 5년 PER 중앙값)에 서로 다른 EPS를 곱한 값이라 구조적으로 가까이 모인다 — 그래서
    // 편차가 작다는 사실만으로 신뢰도를 합의로 읽으면 실제보다 높게 받아들이게 된다.
    var confTip = v.dispersion != null
      ? '방법 간 중심값 편차 ±' + Math.round(v.dispersion * 100) + '% (' + nMeth + '개 방법) — '
        + (v.confidence === '높음' ? '방법 간 값이 좁게 모여 있습니다(±15% 미만).'
          : v.confidence === '중간' ? '방법 간 값이 다소 흩어져 있습니다(±15~35%).'
          : '방법 간 값이 크게 흩어져 있습니다(±35% 이상). 판정을 보수적으로 해석하세요.')
        + ' 값이 모인 정도이지 방법들이 독립적으로 합의했다는 뜻은 아닙니다 — ② 역사적 밴드와 ④ 선행 이익은 같은 배수를 씁니다.'
      : nMeth <= 1 ? '사용 가능한 평가 방법이 1개뿐이라 낮음으로 처리합니다.'
      : '편차 정보를 계산하지 못했습니다.';
    var finYear = (D.financials && D.financials.years && D.financials.years.length)
      ? D.financials.years[D.financials.years.length - 1] : null;
    // 판정 헤드라인 색 · 연속 마커 위치 · 평이한 해설
    // 판정은 색을 쓰지 않는다(R4) — 초록·클레이는 '숫자의 부호'(등락·수익률·괴리율) 전용이고,
    // 판정은 우리가 내린 판단이라 무채 잉크로만 쓴다. 방향은 문자와 눈금 위 위치가 말한다.
    var vColor = 'var(--ink)';
    var gp = v.gap == null ? null : Math.max(-0.4, Math.min(0.4, v.gap));
    var mpos = gp == null ? pos : (50 - gp / 0.4 * 40);   // 괴리율(연속)→바 위치. +괴리(상승여력)=왼쪽(저평가)
    var gapAbs = v.gap == null ? null : Math.abs(v.gap * 100).toFixed(1);
    var verdictLine = v.verdict === '적정 수준'
      ? '현재가가 가중 종합 적정가 범위 안에 있습니다.'
      : (v.gap != null
        ? '현재가가 적정가보다 ' + gapAbs + '% ' + (v.gap > 0 ? '낮습니다 — 상승여력' : '높습니다 — 하락위험') + '.'
        : '적정가를 계산하지 못했습니다.');
    // ── B (기본) ──
    $('hv-B').innerHTML =
      '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:22px 24px;display:flex;gap:32px;align-items:center;flex-wrap:wrap">' +
        '<div style="min-width:210px"><div style="display:flex;align-items:center;gap:11px">' +
          '<span style="width:38px;height:38px;flex:none;border-radius:var(--radius-sm);background:var(--ink);color:var(--paper);display:inline-flex;align-items:center;justify-content:center;font-family:' + disp + ';font-weight:900;font-size:18px">' + esc(initial) + '</span>' +
          '<div><div style="display:flex;align-items:center;gap:7px"><span style="font-family:' + mono + ';font-size:12px;color:var(--ink-3)">' + esc(m.ticker) + '</span>' + badge(m.market + ' · ' + m.benchmark, 'info') + '</div>' +
          '<div style="font-family:' + disp + ';font-weight:700;font-size:29px;letter-spacing:-0.01em;line-height:1;margin-top:3px">' + esc(m.name) + '</div></div></div>' +
          '<div style="font-family:' + mono + ';font-size:29px;font-weight:500;margin-top:14px">' + fmtPrice(m.price) + '</div></div>' +
        '<div style="flex:1;min-width:320px"><div style="display:flex;justify-content:space-between;align-items:baseline"><span class="kick">밸류에이션 판정</span><span style="font-size:12px;color:var(--ink-3)">신뢰도 <b class="na" tabindex="0" data-tip="' + esc(confTip) + '" style="color:var(--ink-2)">' + esc(v.confidence || '—') + '</b></span></div>' +
          // 큰 판정 헤드라인 — 결론(저평가/적정/고평가)이 한눈에
          '<div style="display:flex;align-items:baseline;gap:12px;margin-top:9px;flex-wrap:wrap">' +
            '<span style="font-family:' + disp + ';font-weight:800;font-size:27px;line-height:1;letter-spacing:-0.01em;color:' + vColor + '">' + esc(v.verdict || '—') + '</span>' +
            '<span style="font-size:13px;color:var(--ink-2);line-height:1.4">' + verdictLine + '</span></div>' +
          // 판정과 아래 근거가 반대 방향일 때만 뜬다 — 큰 글씨를 본 사람이 스크롤하기 전에
          // "왜 아래는 반대로 말하나"를 먼저 읽게 한다. 반대가 아니면 아무 말도 하지 않는다(#69).
          // 문장은 파이썬(commentary.verdict_conflict)이 만들고 여기서는 자리만 잡는다.
          // esc()를 걸지 않는 이유: 이 문자열은 판정 어휘(고정 목록)와 숫자 포맷만으로
          // 서버가 조립한 것이라 외부 입력이 섞이지 않는다. <b> 강조를 살리려고 그대로 넣는다.
          (v.conflict && v.conflict.short
            ? '<div style="margin-top:11px;padding:9px 12px;border:1px solid var(--line-strong);border-left:3px solid var(--warning);border-radius:var(--radius-sm);background:var(--paper-2);font-size:12px;color:var(--ink-2);line-height:1.6">' + v.conflict.short + '</div>'
            : '') +
          // 3존 라벨(저평가·적정·고평가) + 연속 마커
          // 지금 어느 구간인지는 **진하기**로 말한다 — 활성 --ink(굵게) / 비활성 --ink-3.
          // 눈금 막대 자체는 자(scale)라서 초록·클레이를 유지한다(판단이 아니라 눈금이다).
          '<div style="position:relative;margin-top:15px;padding-bottom:28px">' +
            '<div style="display:flex;font-size:11.5px;letter-spacing:.02em;color:var(--ink-3);margin-bottom:7px">' +
              '<span style="flex:1;text-align:left' + (tone === 'positive' ? ';color:var(--ink);font-weight:700' : '') + '">저평가</span>' +
              '<span style="flex:1;text-align:center' + (tone === 'neutral' ? ';color:var(--ink);font-weight:700' : '') + '">적정</span>' +
              '<span style="flex:1;text-align:right' + (tone === 'negative' ? ';color:var(--ink);font-weight:700' : '') + '">고평가</span></div>' +
            '<div style="display:flex;height:13px;border-radius:var(--radius-pill);overflow:hidden">' +
              '<span style="flex:1;background:var(--dv-green);opacity:.82"></span><span style="flex:1;background:var(--dv-green);opacity:.45"></span><span style="flex:1;background:var(--paper-3)"></span><span style="flex:1;background:var(--dv-clay);opacity:.45"></span><span style="flex:1;background:var(--dv-clay);opacity:.82"></span></div>' +
            '<div style="position:absolute;left:' + mpos + '%;top:26px;transform:translateX(-50%);width:2px;height:22px;background:var(--ink)"></div>' +
            '<div style="position:absolute;left:' + mpos + '%;top:22px;transform:translateX(-50%);width:11px;height:11px;border-radius:50%;background:var(--ink);border:2px solid var(--paper);box-shadow:var(--shadow-sm)"></div>' +
            '<div style="position:absolute;left:' + mpos + '%;top:51px;transform:translateX(-50%);white-space:nowrap;font-family:' + mono + ';font-size:10.5px;font-weight:700;color:var(--ink)">현재가</div></div>' +
          // 현재가 vs 적정가 vs 괴리율 — 수치 요약
          '<div style="margin-top:6px;display:flex;gap:20px;flex-wrap:wrap;font-size:13px;color:var(--ink-2)">' +
            '<span>현재가 <b style="font-family:' + mono + ';color:var(--ink)">' + fmtPrice(m.price) + '</b></span>' +
            '<span>가중 종합 적정가 <b style="font-family:' + mono + ';color:var(--ink)">' + fmtPrice(v.fair_mid) + '</b></span>' +
            '<span>괴리율 <b style="font-family:' + mono + ';color:' + gapCol + '">' + fmtSigned(v.gap) + '</b></span></div>' +
          '<div style="margin-top:9px;font-family:' + mono + ';font-size:10.5px;color:var(--ink-3)">기준 · 주가 ' + esc(m.asof || '—') + (finYear ? ' · 재무 FY' + esc(String(finYear)) : '') + (D.computed_at ? ' · 계산 ' + esc(D.computed_at) : '') + ' <span class="na" tabindex="0" data-tip="주가·지표는 표시된 거래일 종가 기준입니다. 결과는 서버에서 30분간 캐시되어 같은 종목 재조회는 즉시 뜹니다(AI 해설은 6시간).">ⓘ</span></div></div>' +
        '<div style="display:flex;flex-direction:column;gap:8px"><button id="basketBtn" class="btn btn-primary btn-sm">＋ 포트폴리오에 담기</button><button class="btn btn-secondary btn-sm">관심종목</button></div></div>';
    var bb = $('basketBtn'); if (bb) bb.addEventListener('click', addToBasket);
  }

  /* 포트폴리오 담기 — localStorage 공유(채권·포트폴리오 페이지와 동일 키) */
  function addToBasket() {
    var m = D.meta, b;
    try { b = JSON.parse(localStorage.getItem('invportfolio') || '{}'); } catch (e) { b = {}; }
    b[m.yahoo_ticker] = { name: m.name, yahoo: m.yahoo_ticker, ticker: m.ticker,
      type: (m.market === 'KR' ? '국내주식' : '해외주식'), currency: m.currency, 'class': '주식' };
    localStorage.setItem('invportfolio', JSON.stringify(b));
    var btn = $('basketBtn'); if (btn) { btn.textContent = '✓ 담았어요 — 🧺 포트폴리오에서 확인'; setTimeout(function () { btn.textContent = '＋ 포트폴리오에 담기'; }, 1800); }
  }

  function renderTiles() {
    var t = D.tiles, fin = D.meta.is_financial;
    var items = [
      ['시가총액', t.market_cap != null ? fmtMoney(t.market_cap) : na('주가 또는 상장주식수를 확인하지 못했습니다.')],
      ['PER (TTM)', t.per != null ? fmtX(t.per) : na('적자(EPS≤0)이거나 이익 데이터가 없어 PER를 계산할 수 없습니다.')],
      ['PBR', t.pbr != null ? fmtX(t.pbr) : na('자본(BPS) 데이터가 없어 PBR를 계산할 수 없습니다.')],
      ['ROE (TTM)', t.roe != null ? fmtPct(t.roe) : na('순이익 또는 자기자본을 받지 못해 ROE를 계산할 수 없습니다.')],
      ['베타 (β)', t.beta != null ? t.beta.toFixed(2) : na('상장 기간이 짧아 회귀 표본이 부족합니다 — 자본비용 탭은 β=1을 가정합니다.')],
      ['WACC', t.wacc != null ? fmtPct(t.wacc) : na(fin ? '금융업은 자금조달 구조가 달라 WACC를 제공하지 않습니다.' : '부채·베타 재료가 부족해 WACC를 계산하지 못했습니다.')]];
    $('tiles').innerHTML = items.map(function (it, i) {
      return '<div style="padding:0 16px' + (i === 0 ? ' 0 0' : '') + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + it[0] + '</div><div class="mono" style="font-size:22px;font-weight:500;margin-top:7px;white-space:nowrap">' + it[1] + '</div></div>';
    }).join('');
  }

  function renderWarnings() {
    var w = D.warnings || [];
    if (!w.length) { $('warnWrap').innerHTML = ''; return; }
    $('warnWrap').innerHTML =
      '<div style="border:1px solid var(--line-strong);border-radius:var(--radius-sm);background:var(--paper-2)">' +
        '<button id="warnToggle" style="appearance:none;background:none;border:none;cursor:pointer;width:100%;display:flex;align-items:center;gap:9px;padding:10px 13px;color:var(--warning)">' +
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>' +
        '<span style="font-size:13px;font-weight:600;color:var(--ink-2)">데이터 품질 경고 ' + w.length + '건</span>' +
        '<svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--ink-3)" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto"><path d="m6 9 6 6 6-6"/></svg></button>' +
        '<div id="warnBody" style="display:none;padding:0 13px 12px 38px;font-size:12.5px;color:var(--ink-2);line-height:1.9">' + w.map(function (x) { return '· ' + esc(x); }).join('<br/>') + '</div></div>';
    wireCollapse('warnToggle', 'warnBody', 'block');
  }

  var CMT = { good: ['var(--dv-positive)', 'M20 6 9 17l-5-5'], bad: ['var(--dv-negative)', 'M18 6 6 18M6 6l12 12'], warn: ['var(--warning)', 'm21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z'], info: ['var(--dv-navy)', 'M12 16v-4M12 8h.01'] };
  function renderSummary() {
    $('bulletChart').innerHTML = bulletChart();
    // 방법별 표
    var est = D.verdict.estimates || [], v = D.verdict;
    var head = '<div class="row head" style="grid-template-columns:1.6fr 1.2fr 0.9fr 1.5fr"><span class="col-label">방법</span><span class="col-label r">적정가 범위</span><span class="col-label r">중심</span><span class="col-label">근거</span></div>';
    // 방법 → 적정가 재료 번호·재료 탭 (요약 표에서 근거가 되는 탭으로 바로 이동)
    var METHOD_TAB = { '업종 상대가치': ['①', 'peers'], '역사적 밴드': ['②', 'valuation'], '수익가치(RIM)': ['③', 'financials'], '선행 이익(컨센서스)': ['④', null] };
    var CANON = ['업종 상대가치', '역사적 밴드', '수익가치(RIM)', '선행 이익(컨센서스)'];
    var estMap = {}; est.forEach(function (e) { estMap[e.method] = e; });
    var skipMap = {}; (v.skipped || []).forEach(function (sk) { skipMap[sk.method] = sk.reason; });
    var order = CANON.concat(est.map(function (e) { return e.method; }).filter(function (m) { return CANON.indexOf(m) < 0; }));
    var rows = order.map(function (name) {
      var mt = METHOD_TAB[name];
      var e = estMap[name];
      if (e) {
        var nameCell = mt && mt[1]
          ? '<span style="font-size:13.5px;font-weight:600"><span class="methods-mno">' + mt[0] + '</span><button type="button" class="methods-goto" data-goto="' + mt[1] + '">' + esc(name) + ' ↗</button></span>'
          : mt
            ? '<span style="font-size:13.5px;font-weight:600"><span class="methods-mno">' + mt[0] + '</span>' + esc(name) + '</span>'
            : '<span style="font-size:13.5px;font-weight:600">' + esc(name) + '</span>';
        var wgt = (v.weights || {})[name];
        // 가중 배지는 '방법' 칸 안에 들어가야 한다. 형제 span으로 붙이면 격자 자식이 5개가 되어
        // 4열 표의 열이 한 칸씩 밀린다(범위가 '중심' 머리 아래로, 근거는 다음 줄로).
        if (wgt != null) nameCell = '<span>' + nameCell + ' <span class="mono" style="font-size:10.5px;color:var(--ink-3)">가중 ' + Math.round(wgt * 100) + '%</span></span>';
        return '<div class="row" style="grid-template-columns:1.6fr 1.2fr 0.9fr 1.5fr">' + nameCell + '<span class="mono r" style="font-size:13.5px;color:var(--ink-2)">' + won(e.low) + '–' + won(e.high) + '</span><span class="mono r" style="font-size:13.5px">' + won(e.mid) + '</span><span style="font-size:12px;color:var(--ink-3)">' + esc(e.note) + '</span></div>';
      }
      if (skipMap[name] != null) {
        // 건너뛴 방법도 번호 자리를 유지해 ①~④가 항상 순서대로 보이게 한다
        return '<div class="row" style="grid-template-columns:1.6fr 1.2fr 0.9fr 1.5fr;opacity:.55"><span style="font-size:13px;color:var(--ink-3)">' + (mt ? '<span class="methods-mno">' + mt[0] + '</span>' : '') + esc(name) + '</span><span class="mono r" style="font-size:12.5px;color:var(--ink-3)">—</span><span class="r" style="font-size:12px;color:var(--ink-3)">제외</span><span style="font-size:12px;color:var(--ink-3)">' + esc(skipMap[name]) + '</span></div>';
      }
      return '';
    }).join('');
    var total = '<div class="row total" style="grid-template-columns:1.6fr 1.2fr 0.9fr 1.5fr;border-bottom:none"><span style="font-size:13.5px;font-weight:700">종합 적정가 (가중평균)</span><span></span><span class="mono r" style="font-size:15px;font-weight:700">' + won(v.fair_mid) + '</span><span style="font-size:12px;font-weight:600;color:' + (v.gap >= 0 ? 'var(--dv-green)' : 'var(--dv-clay)') + '">현재가 대비 ' + fmtSigned(v.gap) + '</span></div>';
    // 동일가중 민감도 — 가중치 선택이 결론을 좌우하지 않는지 투명하게 병기
    var sens = '';
    if (v.fair_mid_equal != null) {
      var flip = v.verdict_equal && v.verdict_equal !== v.verdict;
      sens = '<div style="font-size:11.5px;color:var(--ink-3);margin-top:7px;padding-top:8px;border-top:1px dashed var(--line)">민감도 · 동일가중(단순평균)이면 적정가 <b class="mono" style="color:var(--ink-2)">' + won(v.fair_mid_equal) + '</b> (현재가 대비 ' + fmtSigned(v.gap_equal) + ')' + (flip ? ' → 판정 <b style="color:var(--warning)">' + esc(v.verdict_equal) + '</b>로 갈림' : ' → 판정 동일') + '. 가중치는 순위 근거의 정성적 인코딩입니다.</div>';
    }
    var formula = '<div style="font-size:11px;color:var(--ink-3);line-height:1.75;margin-top:10px">공식 · ① 피어 중앙값 배수(PER·PBR·EV/EBITDA) × 자사 펀더멘털 &nbsp;② 자기 5년 PER·PBR 25~75분위 × 현재 EPS·BPS &nbsp;③ RIM: V = B + B(ROE−r)·w/(1+r−w), r = CAPM 자기자본비용 &nbsp;④ 컨센서스 12개월 EPS × 자기 5년 PER 중앙값 — 종합 = 가중평균 ④35 · ①25 · ②25 · ③15% (순위 근거: Liu·Nissim·Thomas 2002·2007 국제 + 국내 가치관련성; 국내 컨센서스 낙관편의 유의 — 상세 docs/adr/0003) · 출처: 재무 OpenDART·Yahoo Finance / 컨센서스 FnGuide(네이버금융)·LSEG I/B/E/S(Yahoo)</div>';
    // 건너뛴 방법이 있으면 가중치가 재정규화됐음을 명시 — 각 행의 '가중 %'가 실제 적용값
    var renorm = (v.skipped || []).length && est.length
      ? '<div style="font-size:11px;color:var(--ink-3);margin-top:6px">제외된 방법의 가중치는 사용 가능한 방법으로 <b>재정규화</b>되었습니다 — 각 행의 "가중 %"가 실제 적용값입니다.</div>' : '';
    $('methodsTable').innerHTML = est.length ? head + rows + total + sens + renorm + formula : '<div style="color:var(--ink-3);font-size:13px;padding:16px 0">적정주가를 계산할 방법이 없습니다(데이터 부족).</div>';
    // 점수
    $('scoreOverall').textContent = D.scores.overall != null ? Math.round(D.scores.overall) : '—';
    $('radarChart').innerHTML = radarChart();
    $('scoreBars').innerHTML = scoreBars();
    // 금융업이면 두 축이 '—'로 비는 이유를 한 줄로 밝힌다(05·07 탭 안내와 톤 통일).
    $('scoreFinNote').innerHTML = (D.meta && D.meta.is_financial)
      ? '<div style="font-size:11.5px;color:var(--ink-3);line-height:1.65;margin-top:18px;padding-top:12px;border-top:1px dashed var(--line)">금융업(은행·보험·증권)은 부채 대부분이 예금·보험부채라 일반 <b>재무 안정성·현금흐름</b> 지표(부채비율·유동비율·FCF수익률 등)가 부적합해 상대점수에서 제외합니다. 은행 건전성은 BIS 자기자본비율·고정이하여신비율 등 <b>감독당국 공시</b>로 평가해야 하며, 본 도구는 무료 공개 데이터 범위상 이를 제공하지 않습니다.</div>'
      : '';
    // 해설 — 두 무리로 나눠 그린다. 무리는 파이썬이 group으로 붙여 보낸다.
    // 문장을 뒤져서 가르지 않는 이유는 R3 발견 7과 같다 — 문자열 규약은 조용히 깨진다(#68).
    var cmts = D.commentary || [];
    var basis = cmts.filter(function (c) { return c.group !== 'reading'; });
    var reading = cmts.filter(function (c) { return c.group === 'reading'; });

    function cmtCard(c, extraClass) {
      // 강조 조건은 commentary.py가 실제로 만드는 문자열과 정확히 같아야 한다.
      // '순수 저평가'로 두었던 동안 이 강조는 한 번도 켜진 적이 없었다(파이썬은 '순수한 저평가').
      // scripts/check_screen_language.py의 C 검사가 이 규약의 생사를 감시한다.
      var m = CMT[c.kind] || CMT.info, key = c.text.indexOf('밸류트랩') >= 0 || c.text.indexOf('순수한 저평가') >= 0;
      var strokeW = c.kind === 'good' ? 1.9 : 1.75;
      var icon = c.kind === 'info' ? '<circle cx="12" cy="12" r="10"/><path d="' + m[1] + '"/>' : c.kind === 'warn' ? '<path d="' + m[1] + '"/><path d="M12 9v4"/><path d="M12 17h.01"/>' : '<path d="' + m[1] + '"/>';
      return '<div class="cmt' + (key ? ' key' : '') + (extraClass || '') + '"><span style="color:' + m[0] + ';flex:none;margin-top:1px"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + strokeW + '" stroke-linecap="round" stroke-linejoin="round">' + icon + '</svg></span><div style="font-size:12.5px;color:var(--ink-2);line-height:1.55">' + esc(c.text) + '</div></div>';
    }

    $('commentary').innerHTML = basis.map(function (c) { return cmtCard(c); }).join('')
      || '<div style="color:var(--ink-3);font-size:13px">해설을 생성할 수 없습니다.</div>';

    // 판정↔근거 충돌 설명(있을 때만)은 이 블록의 첫 카드로 오고, 그것만 한 단 띄운다.
    var clash = (D.verdict && D.verdict.conflict) ? D.verdict.conflict.detail : null;
    var block = $('readingBlock');
    if (block) {
      block.hidden = !reading.length;
      $('reading').innerHTML = reading.map(function (c, i) {
        return cmtCard(c, (clash && i === 0 && c.text === clash) ? ' clash' : '');
      }).join('');
    }
    renderConsensus();
  }

  /* ── 시장 컨센서스 교차검증 (요약 탭 02) ── */
  function renderConsensus() {
    var body = $('consensusBody'), meta = $('consensusMeta');
    if (!body) return;
    var c = D.consensus;
    if (!c || c.error) {
      meta.textContent = '커버리지 없음';
      body.innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:4px 0">애널리스트 컨센서스가 없는 종목입니다 — 증권사가 분석 리포트를 내지 않는 소형주에 흔합니다. 이 경우 위 적정가 추정(①~③)만으로 판단 근거를 삼습니다.</div>';
      return;
    }
    meta.textContent = (c.n_analysts != null ? '애널리스트 ' + c.n_analysts + '명 평균'
      : D.meta.market === 'KR' ? 'FnGuide · 42개 증권사 집계' : '애널리스트 평균') + (c.as_of ? ' · ' + c.as_of : '');
    function tone(v) { return v == null ? 'var(--ink)' : v >= 0 ? 'var(--dv-green)' : 'var(--dv-clay)'; }
    var tiles = [
      ['현재가', fmtPrice(D.meta.price), '', 'var(--ink)'],
      ['모형 종합 적정가 · 이 대시보드', fmtPrice(D.verdict.fair_mid), D.verdict.gap != null ? '현재가 대비 ' + fmtSigned(D.verdict.gap) : '', tone(D.verdict.gap)],
      ['컨센서스 목표주가 · 증권가', fmtPrice(c.target_mean), c.target_upside != null ? '현재가 대비 ' + fmtSigned(c.target_upside) : '', tone(c.target_upside)],
      ['투자의견 평균', c.recomm_label || '—', c.recomm_score != null ? c.recomm_score.toFixed(2) + ' / 5.0' : '', 'var(--ink)']
    ];
    var tilesHtml = tiles.map(function (t, i) {
      return '<div style="flex:1;min-width:168px;padding:' + (i === 0 ? '0 18px 0 0' : '0 18px') + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + t[0] + '</div><div class="mono" style="font-size:21px;font-weight:500;margin-top:6px;color:' + t[3] + '">' + t[1] + '</div><div style="font-size:11.5px;color:var(--ink-3);margin-top:3px">' + t[2] + '</div></div>';
    }).join('');
    var rows = [];
    if (c.forward_eps != null) rows.push('12개월 선행 EPS(컨센서스) <b class="mono">' + fmtPrice(c.forward_eps) + '</b>' + (c.implied_growth != null ? ' — 최근 12개월 실적 대비 <b style="color:' + tone(c.implied_growth) + '">' + fmtSigned(c.implied_growth) + '</b>의 이익 변화를 전제합니다' : ''));
    if (c.forward_per != null) rows.push('선행 PER <b class="mono">' + fmtX(c.forward_per) + '</b> — 트레일링 PER와의 차이가 시장이 반영 중인 실적 전망입니다');
    if (c.model_vs_target != null) rows.push('모형 종합 적정가는 컨센서스 목표주가보다 <b style="color:' + tone(c.model_vs_target) + '">' + fmtSigned(c.model_vs_target) + '</b> — 두 값이 가까울수록 서로 다른 접근이 같은 결론을 가리킨다는 뜻입니다');
    // 목표주가 역산 — 증권가가 어떤 멀티플을 깔았는지 되짚어 차이의 원인을 보여준다
    if (c.target_mean != null && c.forward_eps) {
      var impliedPer = c.target_mean / c.forward_eps;
      var e4 = null, ests = D.verdict.estimates || [];
      for (var ei = 0; ei < ests.length; ei++) if (ests[ei].method === '선행 이익(컨센서스)') e4 = ests[ei];
      var ourMult = (e4 && e4.mid != null) ? e4.mid / c.forward_eps : null;
      rows.push('<b>목표주가 역산</b>: 증권가 목표가(' + fmtPrice(c.target_mean) + ')는 선행 EPS × <b class="mono">' + fmtX(impliedPer) + '</b>를 적용한 셈입니다' +
        (ourMult != null ? ' — 이 대시보드 ④는 보수 원칙으로 <b class="mono">' + fmtX(ourMult) + '</b>를 적용했습니다. 두 값 차이의 대부분은 "정당한 멀티플이 몇 배냐"(성장 프리미엄) 가정에서 나옵니다' : '') +
        '. 증권사 리포트의 정성적 근거(수주·신제품·업황 전망)는 무료 데이터에 포함되지 않아 이렇게 역산으로만 추정합니다');
    }
    body.innerHTML =
      '<div style="display:flex;flex-wrap:wrap;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:16px 0">' + tilesHtml + '</div>' +
      (rows.length ? '<ul style="margin:14px 0 0;padding-left:18px;display:flex;flex-direction:column;gap:5px">' + rows.map(function (r) { return '<li style="font-size:12.5px;color:var(--ink-2);line-height:1.6">' + r + '</li>'; }).join('') + '</ul>' : '') +
      '<div style="font-size:11.5px;color:var(--ink-3);margin-top:12px">출처: ' + esc(c.source || '') + ' · 목표주가·추정 EPS는 증권사 애널리스트 평균이며 매수 편향이 있을 수 있습니다. 판정에는 ④ 선행 이익 방법(추정 EPS × 시장 멀티플)만 반영하고 목표주가 자체는 계산에 넣지 않습니다.</div>';
  }

  function renderPriceTab() {
    var p = D.price;
    if (!p || p.error) { $('priceTiles').innerHTML = '<div style="color:var(--ink-3);font-size:13px">주가 데이터를 불러오지 못했습니다.</div>'; $('priceChart').innerHTML = ''; return; }
    var tiles = [['현재가', fmtPrice(p.cur)], ['52주 최고 / 최저', won(p.hi52) + ' <span style="color:var(--ink-3)">/</span> ' + won(p.lo52)], ['최근 1년 수익률', '<span style="color:' + (p.ret1y >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)') + '">' + fmtSigned(p.ret1y) + '</span>'], ['52주 밴드 내 위치', p.pos52 != null ? p.pos52.toFixed(0) + '%' : '—']];
    $('priceTiles').innerHTML = tiles.map(function (t, i) { return '<div style="flex:1;min-width:150px;padding:' + (i === 0 ? '0 18px 0 0' : '0 18px') + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + t[0] + '</div><div class="mono" style="font-size:' + (i === 1 ? 17 : 22) + 'px;font-weight:500;margin-top:6px">' + t[1] + '</div></div>'; }).join('');
    // 같은 블록 안에서 52주(실거래가)와 최근 1년 수익률(수정주가)이 다른 기준을 쓴다.
    // 차트 출처 문구는 차트만 설명하므로 여기서 한 번 더 밝힌다(R3 발견 6 · #57).
    // 타일 컨테이너는 모바일에서 가로 스크롤이라 안내문은 그 바깥에 둔다.
    var bn = $('priceBasisNote');
    if (bn) bn.innerHTML = p.basis_note ? esc(p.basis_note) : '';
    renderPrice();
  }

  function renderValuation() {
    var head = '<div class="row head" style="grid-template-columns:1.1fr 1fr 1fr 1fr 1.1fr"><span class="col-label">지표</span><span class="col-label r">현재</span><span class="col-label r">업종 중앙값</span><span class="col-label r">자기 5년</span><span class="col-label r">vs 업종</span></div>';
    var rows = (D.multiples || []).map(function (r, i) {
      var vs = '<span style="color:var(--ink-3)">— 참고</span>';
      if (r.vs != null && r.cheaper != null) { var col = r.cheaper ? 'var(--dv-positive)' : 'var(--dv-negative)'; vs = '<span style="color:' + col + '"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:' + col + ';margin-right:5px"></span>' + r.vs.toFixed(0) + '% ' + (r.cheaper ? '낮음' : '높음') + '</span>'; }
      var last = i === D.multiples.length - 1;
      return '<div class="row" style="grid-template-columns:1.1fr 1fr 1fr 1fr 1.1fr' + (last ? ';border-bottom:none' : '') + '"><span style="font-size:13.5px">' + esc(r.label) + '</span><span class="mono r" style="font-size:13.5px">' + fmtMult(r.key, r.current) + '</span><span class="mono r" style="font-size:13.5px;color:var(--ink-3)">' + fmtMult(r.key, r.med) + '</span><span class="mono r" style="font-size:13.5px;color:var(--ink-3)">' + (r.own5y != null ? fmtX(r.own5y) : '—') + '</span><span class="r" style="font-size:12.5px">' + vs + '</span></div>';
    }).join('');
    $('multiplesTable').innerHTML = head + rows;
    renderBand();
    renderScenario();
  }

  /* ── 시나리오 분석 (밸류에이션 탭 03) ── */
  function renderScenario() {
    var body = $('scenarioBody');
    if (!body) return;
    var s = D.scenario;
    if (!s || s.error || !s.cases || !s.cases.length) {
      body.innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:4px 0">이익(EPS)이 적자이거나 밴드·피어 데이터가 부족해 이익 기반 시나리오를 만들 수 없습니다.</div>';
      return;
    }
    var CASE_TONE = { '비관': 'var(--dv-clay)', '기준': 'var(--ink)', '낙관': 'var(--dv-green)' };
    function caseDelta(name) { return name === '비관' ? state.scnBear : name === '낙관' ? state.scnBull : 0; }
    function tilesHtml() {
      return s.cases.map(function (cs, i) {
        var dlt = caseDelta(cs.name);
        var m = cs.multiple * (1 + state.scnMult);
        var p = s.eps_base * (1 + dlt) * m;
        var up = D.meta.price ? p / D.meta.price - 1 : null;
        return '<div style="flex:1;min-width:168px;padding:' + (i === 0 ? '0 18px 0 0' : '0 18px') + (i ? ';border-left:1px solid var(--line)' : '') + '">' +
          '<div class="kick" style="color:' + CASE_TONE[cs.name] + '">' + cs.name + '</div>' +
          '<div class="mono" style="font-size:22px;font-weight:500;margin-top:6px">' + fmtPrice(p) + '</div>' +
          '<div style="font-size:11.5px;color:var(--ink-3);margin-top:3px">EPS ' + fmtSigned(dlt) + ' × ' + fmtX(m) +
          (up != null ? ' · 현재가 대비 <b style="color:' + (up >= 0 ? 'var(--dv-green)' : 'var(--dv-clay)') + '">' + fmtSigned(up) + '</b>' : '') + '</div></div>';
      }).join('');
    }
    // 자동 해석 한 줄 — 그리드에서 현재가 위 칸 수 + 비관 케이스의 완충 여부
    function readLine() {
      var parts = [];
      if (s.grid && s.grid.values && D.meta.price) {
        var tot = 0, green = 0;
        s.grid.values.forEach(function (row) { row.forEach(function (v) { if (v != null) { tot++; if (v > D.meta.price) green++; } }); });
        if (tot) parts.push('민감도 ' + tot + '칸 중 <b>' + green + '칸(' + Math.round(green / tot * 100) + '%)</b>이 현재가 위');
      }
      var bear = s.cases[0], up = null;
      if (bear && D.meta.price) up = s.eps_base * (1 + state.scnBear) * bear.multiple * (1 + state.scnMult) / D.meta.price - 1;
      if (up != null) parts.push('비관 케이스는 현재가 대비 <b style="color:' + (up >= 0 ? 'var(--dv-green)' : 'var(--dv-clay)') + '">' + fmtSigned(up) + '</b>' + (up >= 0 ? ' (하방 완충이 있는 편)' : ' (비관 가정 실현 시 하락 여지)'));
      return parts.length ? '지금 가정에서는 ' + parts.join(', ') + '입니다.' : '';
    }
    function slider(label, id, val, min, max) {
      return '<label style="display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--ink-2)">' + label +
        '<input type="range" id="' + id + '" min="' + min + '" max="' + max + '" step="5" value="' + Math.round(val * 100) + '" style="width:150px">' +
        '<span class="mono" id="' + id + 'Val" style="min-width:44px">' + fmtSigned(val) + '</span></label>';
    }
    // 민감도 그리드 — 축은 서버가 고정(EPS ±30% × 밴드 분위), 셀 색은 현재가 대비 괴리
    var gridHtml = '';
    if (s.grid && s.grid.values) {
      var cols = s.grid.mult_labels.length;
      var cells = '<div style="display:grid;grid-template-columns:86px repeat(' + cols + ',1fr);border:1px solid var(--line);border-radius:var(--radius-sm);overflow:hidden;font-variant-numeric:tabular-nums">';
      cells += '<div style="padding:8px 10px;background:var(--paper-2);font-size:10.5px;letter-spacing:.06em;color:var(--ink-3)">EPS \\ 배수</div>';
      s.grid.mult_labels.forEach(function (m) { cells += '<div class="mono" style="padding:8px 10px;background:var(--paper-2);font-size:11.5px;color:var(--ink-2);text-align:right">' + esc(m) + '</div>'; });
      s.grid.values.forEach(function (row, ri) {
        cells += '<div class="mono" style="padding:8px 10px;background:var(--paper-2);font-size:11.5px;color:var(--ink-2);border-top:1px solid var(--line)">' + esc(s.grid.eps_labels[ri]) + '</div>';
        row.forEach(function (v, ci) {
          var up = (v != null && D.meta.price) ? v / D.meta.price - 1 : null;
          var pct = up == null ? 0 : Math.min(Math.abs(up) * 55, 24);
          var bg = up == null ? 'transparent' : 'color-mix(in srgb, ' + (up >= 0 ? 'var(--dv-green)' : 'var(--dv-clay)') + ' ' + pct.toFixed(0) + '%, transparent)';
          var isBase = ri === Math.floor(s.grid.values.length / 2) && ci === Math.floor(cols / 2);
          cells += '<div class="mono" style="padding:8px 10px;font-size:12px;text-align:right;border-top:1px solid var(--line);background:' + bg + (isBase ? ';box-shadow:inset 0 0 0 1.5px var(--ink-3)' : '') + '">' +
            (v == null ? '—' : compactWon(v)) + '<span style="display:block;font-size:10px;color:var(--ink-3)">' + (up == null ? '' : fmtSigned(up)) + '</span></div>';
        });
      });
      cells += '</div>';
      gridHtml = '<div style="margin-top:22px"><div class="kick" style="margin-bottom:10px">민감도 — EPS 가정 × 멀티플</div>' + cells +
        '<div style="font-size:11.5px;color:var(--ink-3);margin-top:8px">셀 = 해당 가정의 이론 가격(위)과 현재가 대비 괴리율(아래). 초록 = 현재가보다 높음, 클레이 = 낮음. 테두리 셀이 기준 가정입니다.</div></div>';
    }
    var howto =
      '<div style="margin-top:18px;border:1px solid var(--line);border-radius:var(--radius-md);padding:14px 16px">' +
      '<div class="kick" style="margin-bottom:8px">어떻게 읽나</div>' +
      '<ul style="margin:0;padding-left:17px;display:flex;flex-direction:column;gap:5px">' +
      '<li style="font-size:12.5px;color:var(--ink-2);line-height:1.65">이 표는 예측이 아니라 <b>가정 조합의 지도</b>입니다. 초록 칸이 많다 = 표에 깔린 가정 범위(EPS ±30% × 자기 5년 배수 폭) 안에서 이론 가격이 현재가보다 높은 조합이 많다는 뜻 — 현재가가 그 가정들 대비 낮게 거래된다는 신호이지 상승 보장이 아닙니다.</li>' +
      '<li style="font-size:12.5px;color:var(--ink-2);line-height:1.65"><b>비관 케이스까지 플러스</b>면 가정이 다소 빗나가도 버티는 하방 완충(안전마진)이 있다고 읽고, <b>낙관에서만 플러스</b>면 수익이 낙관 가정의 실현에 의존한다고 읽습니다.</li>' +
      '<li style="font-size:12.5px;color:var(--ink-2);line-height:1.65">출발점이 컨센서스 EPS라서 시장의 이익 전망 자체가 꺾이면 표 전체가 아래로 이동합니다. 멀티플 슬라이더는 위 케이스 카드에 적용되며, 민감도 표는 열 자체가 멀티플 축이라 고정입니다.</li>' +
      '</ul><div id="scnRead" style="font-size:12.5px;color:var(--ink);margin-top:10px;line-height:1.6">' + readLine() + '</div></div>';
    body.innerHTML =
      '<div id="scnTiles" style="display:flex;flex-wrap:wrap;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:16px 0">' + tilesHtml() + '</div>' +
      '<div style="display:flex;gap:26px;flex-wrap:wrap;margin-top:14px;align-items:center">' +
      slider('비관 EPS 조정', 'scnBearSlider', state.scnBear, -40, 0) +
      slider('낙관 EPS 조정', 'scnBullSlider', state.scnBull, 0, 40) +
      slider('멀티플 조정', 'scnMultSlider', state.scnMult, -30, 30) +
      '<span style="font-size:11.5px;color:var(--ink-3)">기준 EPS: ' + fmtPrice(s.eps_base) + ' (' + esc(s.eps_basis) + ') · 멀티플: ' + esc(s.multiple_basis) + '</span></div>' +
      gridHtml + howto +
      ((s.notes || []).length ? '<div style="font-size:11.5px;color:var(--ink-3);margin-top:12px;line-height:1.7">' + s.notes.map(esc).join('<br/>') + '</div>' : '');
    function bind(id, key) {
      var inp = $(id);
      if (!inp) return;
      inp.addEventListener('input', function () {
        state[key] = Number(inp.value) / 100;
        $(id + 'Val').textContent = fmtSigned(state[key]);
        $('scnTiles').innerHTML = tilesHtml();
        var rd = $('scnRead');
        if (rd) rd.innerHTML = readLine();
      });
    }
    bind('scnBearSlider', 'scnBear');
    bind('scnBullSlider', 'scnBull');
    bind('scnMultSlider', 'scnMult');
  }

  function renderCompany() {
    var c = D.company;
    var info = '';
    if (c && c.summary && !c.error) {
      info += '<p style="font-size:14px;color:var(--ink-2);line-height:1.7;margin:0">' + esc(c.summary) + '</p><div style="display:flex;gap:26px;margin-top:18px;border-top:1px solid var(--line);padding-top:16px">';
      info += '<div><div class="kick">출처</div><div style="font-size:13px;margin-top:5px">' + esc(c.source || '—') + '</div></div>';
      if (c.website) info += '<div><div class="kick">웹사이트</div><div style="font-size:13px;margin-top:5px">' + esc(c.website) + '</div></div>';
      if (c.employees) info += '<div><div class="kick">직원 수</div><div class="mono" style="font-size:13px;margin-top:5px">' + Number(c.employees).toLocaleString('en-US') + '명</div></div>';
      info += '</div>';
    } else info += '<p style="font-size:13px;color:var(--ink-3);margin:0">기업 소개를 불러오지 못했습니다. (무료 데이터 특성상 일부 종목은 개요가 없습니다)</p>';
    $('companyInfo').innerHTML = info;
    // AI 뉴스 분석 버튼(키 있고 뉴스 있을 때) — 서술형 Gemini 분석
    var naw = $('newsAiWrap');
    if (naw) {
      var hasNews = D.news && !D.news.error && D.news.length;
      if (D.meta.ai_available && hasNews) {
        naw.innerHTML = '<button id="newsAiBtn" class="btn btn-secondary btn-sm">✦ AI 뉴스 분석 (Gemini)</button><div id="newsAiOut"></div>';
        var nb = $('newsAiBtn'); nb.addEventListener('click', function () { aiFetch('news', $('newsAiOut'), nb); });
      } else if (!D.meta.ai_available) {
        naw.innerHTML = '<div style="font-size:11.5px;color:var(--ink-3);border-top:1px solid var(--line);padding-top:12px;line-height:1.6">💡 <b style="color:var(--ink-2)">Gemini API 키</b>를 설정하면 위 헤드라인을 감성·핵심이슈·촉매·리스크로 분석해 줍니다. <span style="font-family:var(--font-mono)">.streamlit/secrets.toml</span>에 <span style="font-family:var(--font-mono)">GEMINI_API_KEY</span>를 넣으세요.</div>';
      } else { naw.innerHTML = ''; }
    }
    // 뉴스
    var news = D.news;
    if (!news || news.error || !news.length) { $('newsList').innerHTML = '<div style="font-size:13px;color:var(--ink-3)">관련 뉴스를 찾지 못했습니다.</div>'; return; }
    var CATCOL = { '기업': ['var(--dv-navy)', '종목 직접 관련'], '산업': ['var(--dv-teal)', '업종·경쟁사'], '거시': ['var(--dv-gold)', 'PEST 태그'] };
    var html = '';
    ['기업', '산업', '거시'].forEach(function (cat, ci) {
      var group = news.filter(function (it) { return it.category === cat; });
      if (!group.length) return;
      var cc = CATCOL[cat];
      html += '<div style="' + (ci ? 'margin-top:18px;border-top:1px solid var(--line);padding-top:14px' : '') + '"><div style="display:flex;align-items:center;gap:7px"><span style="width:7px;height:7px;border-radius:50%;background:' + cc[0] + '"></span><span style="font-size:12px;font-weight:600">' + cat + '</span><span style="font-size:11px;color:var(--ink-3)">' + cc[1] + '</span></div>';
      group.forEach(function (it) {
        var tags = (it.tags || []).map(function (t) { var macro = cat === '거시'; return '<span style="font-family:var(--font-mono);font-size:10px;' + (macro ? 'color:var(--ink);border:1px solid var(--dv-navy)' : 'color:#fff;background:var(--dv-navy)') + ';border-radius:var(--radius-sm);padding:1px 6px;margin-left:4px">' + esc(t) + '</span>'; }).join('');
        var meta = [it.source, it.date].filter(Boolean).join(' · ');
        html += '<a href="' + esc(it.link || '#') + '" target="_blank" rel="noopener" style="display:block;font-size:13.5px;margin-top:10px;line-height:1.5">' + esc(it.title) + tags + '</a><div style="font-size:11px;color:var(--ink-3);margin-top:3px">' + esc(meta) + '</div>';
      });
      html += '</div>';
    });
    $('newsList').innerHTML = html || '<div style="font-size:13px;color:var(--ink-3)">분류된 뉴스가 없습니다.</div>';
  }

  function renderFinancials() {
    var f = D.financials;
    if (!f || f.error) { $('finGrowth').innerHTML = '<div style="color:var(--ink-3);font-size:13px">재무 데이터를 불러오지 못했습니다.</div>'; return; }
    var unit = f.unit;
    // 단위 설명 — KR은 조원, US의 B는 '10억 달러'임을 명시(모르는 사용자 배려)
    var unitLabel = unit === 'B' ? 'B (10억 달러)' : unit + '원';
    $('finGrowthUnit').textContent = '단위 · ' + unitLabel;
    $('finCashUnit').textContent = '단위 · ' + unitLabel;
    $('finGrowth').innerHTML = barGroups(f.years, [
      { name: '매출액', color: 'var(--dv-navy)', data: f.revenue }, { name: '영업이익', color: 'var(--dv-teal)', data: f.operating_income }, { name: '순이익', color: 'var(--dv-gold)', data: f.net_income }
    ], { fmt: function (v) { return v.toFixed(0) + unit; }, H: 230 });
    var om = f.op_margin, nm = f.net_margin;
    $('finProfitability').innerHTML = lineMulti((om && om.x) || (nm && nm.x) || f.years, [
      { name: '영업이익률 %', color: 'var(--dv-teal)', data: (om ? om.y : []).map(function (v) { return v == null ? null : v * 100; }) },
      { name: '순이익률 %', color: 'var(--dv-gold)', data: (nm ? nm.y : []).map(function (v) { return v == null ? null : v * 100; }) }
    ], { fmt: function (v) { return v.toFixed(1) + '%'; }, H: 190 });
    // 안정성 (금융업 숨김)
    if (f.is_financial) { $('finStability').innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:20px 0">금융업 — 생략</div>'; }
    else {
      var dr = f.debt_ratio, cr = f.current_ratio;
      $('finStability').innerHTML = lineMulti((dr && dr.x) || f.years, [
        { name: '부채비율 %', color: 'var(--dv-clay)', data: (dr ? dr.y : []).map(function (v) { return v == null ? null : v * 100; }) },
        { name: '유동비율 %', color: 'var(--dv-slate)', data: (cr ? cr.y : []).map(function (v) { return v == null ? null : v * 100; }) }
      ], { fmt: function (v) { return v.toFixed(0) + '%'; }, H: 200 });
    }
    if (f.is_financial) $('finCash').innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:20px 0">금융업 — 생략</div>';
    else {
      $('finCash').innerHTML = barGroups(f.years, [
        { name: '영업현금흐름', color: 'var(--dv-green)', data: f.ocf }, { name: '잉여현금흐름 FCF', color: 'var(--dv-plum)', data: f.fcf }
      ], { fmt: function (v) { return v.toFixed(0) + unit; }, H: 210 });
      // 영업현금흐름 음수 해 — 규칙 기반 고정 문구(AI·추가 요청 없음). 어느 해인지 짚고 해석 방향만 알린다.
      var negY = (f.years || []).filter(function (y, i) { return f.ocf && f.ocf[i] != null && f.ocf[i] < 0; });
      if (negY.length) {
        $('finCash').innerHTML += '<div style="margin-top:10px;font-size:12px;color:var(--ink-2);line-height:1.6">' +
          '<b style="color:var(--dv-negative)">' + negY.join('·') + '년 영업현금흐름 (−)</b> — 본업에서 현금이 들어온 게 아니라 나갔다는 뜻입니다. ' +
          '회계상 이익이 나더라도 재고 증가·매출채권 회수 지연 등으로 생길 수 있어, 일시적인지 반복되는지 추세를 확인하세요.</div>';
      }
    }
    // 표
    var tb = f.table, cols = '1.4fr repeat(' + tb.years.length + ',1fr)';
    var head = '<div style="display:grid;grid-template-columns:' + cols + ';gap:6px;border-top:1px solid var(--line-strong);padding:9px 0;border-bottom:1px solid var(--line)"><span style="font-size:11px;color:var(--ink-3);text-transform:uppercase;letter-spacing:0.06em">항목(' + (unit === 'B' ? '10억 달러' : unit + '원') + ')</span>' + tb.years.map(function (y) { return '<span style="font-size:11px;color:var(--ink-3);text-align:right">' + y + '</span>'; }).join('') + '</div>';
    var body = Object.keys(tb.rows).map(function (name, ri) {
      var isEps = name === 'EPS', krw = CUR === 'KRW';
      // EPS는 통화 원단위(스케일 무관) — KR은 원(정수), US는 달러(센트까지)
      var cells = tb.rows[name].map(function (v) { return '<span style="text-align:right">' + (v == null ? '—' : isEps ? (krw ? Math.round(v).toLocaleString('en-US') : v.toFixed(2)) : v.toFixed(2)) + '</span>'; }).join('');
      return '<div style="display:grid;grid-template-columns:' + cols + ';gap:6px;padding:9px 0;' + (ri < 3 ? 'border-bottom:1px solid var(--line);' : '') + 'font-family:var(--font-mono);font-size:12.5px"><span style="font-family:var(--font-sans)">' + name + (isEps ? (krw ? '(원)' : '($)') : '') + '</span>' + cells + '</div>';
    }).join('');
    $('finTableBody').innerHTML = head + body;
  }

  function renderPeers() {
    var pr = D.peers;
    if (!pr || pr.error) { $('peerTable').innerHTML = '<div style="color:var(--ink-3);font-size:13px">피어 데이터를 불러오지 못했습니다.</div>'; return; }
    $('peerLabel').textContent = '피어 비교 — ' + (pr.sector || '업종');
    if (pr.basis) $('peerBasis').textContent = pr.basis;
    var cols = '1.3fr 0.9fr 0.7fr 0.7fr 0.8fr';
    var head = '<div class="row head" style="grid-template-columns:' + cols + '"><span class="col-label">종목</span><span class="col-label r">시총' + (CUR === 'KRW' ? '(조)' : '') + '</span><span class="col-label r">PER</span><span class="col-label r">PBR</span><span class="col-label r">ROE</span></div>';
    var naPeer = '이 종목의 해당 지표가 원천 데이터(Yahoo·KRX)에 없습니다 — 적자 기업은 PER가 비게 됩니다.';
    var body = pr.rows.map(function (p, i) {
      var mc = p.market_cap == null ? na('주가 또는 상장주식수를 받지 못해 시가총액을 계산할 수 없습니다.') : CUR === 'KRW' ? (p.market_cap / 1e12).toFixed(1) : (p.market_cap / 1e9).toFixed(1);
      var last = i === pr.rows.length - 1;
      // 제외 키: KR은 상장목록 이름, US는 심볼(data-key) — 서버 exclude 매칭과 동일 기준
      var exKey = CUR === 'KRW' ? p.name : (p.key || p.name);
      var xBtn = p.is_self ? '' : '<button type="button" class="peer-x" data-ex="' + esc(exKey) + '" title="이 피어 제외" aria-label="' + esc(p.name) + ' 피어에서 제외">×</button>';
      return '<div class="row' + (p.is_self ? ' self' : '') + '" data-q="' + esc(p.q || '') + '" data-key="' + esc(p.key || '') + '" style="grid-template-columns:' + cols + ';font-family:var(--font-mono);font-size:12.5px;cursor:pointer' + (last ? ';border-bottom:none' : '') + '"><span class="peer-name" style="font-family:var(--font-sans)' + (p.is_self ? ';font-weight:700' : '') + '">' + xBtn + esc(p.name) + '</span><span class="r">' + mc + '</span><span class="r">' + (p.per != null ? p.per.toFixed(1) : na(naPeer)) + '</span><span class="r">' + (p.pbr != null ? p.pbr.toFixed(2) : na(naPeer)) + '</span><span class="r">' + (p.roe != null ? (p.roe * 100).toFixed(1) : na(naPeer)) + '</span></div>';
    }).join('');
    var edited = state.peerEx.length || state.peerAdd.length;
    var addRow = '<div class="peer-addrow">'
      + '<button type="button" class="peer-addbtn">＋ 피어 추가</button>'
      + '<input class="peer-addinput" type="text" placeholder="' + (CUR === 'KRW' ? '종목명 또는 코드 (예: 카카오, 035720)' : '심볼 (예: MSFT)') + '" aria-label="추가할 피어">'
      + (edited ? '<button type="button" class="peer-reset">편집 초기화 (제외 ' + state.peerEx.length + ' · 추가 ' + state.peerAdd.length + ')</button>' : '')
      + '</div>';
    $('peerTable').innerHTML = head + body + addRow;
    wirePeerEdit();
    $('peerScatter').innerHTML = peerScatter();
    // 랭킹
    var rcols = '0.5fr 1.3fr 0.8fr 0.8fr 0.8fr';
    var rhead = '<div class="row head" style="grid-template-columns:' + rcols + '"><span class="col-label c">순위</span><span class="col-label">종목</span><span class="col-label r">종합</span><span class="col-label r">가치</span><span class="col-label r">수익성</span></div>';
    var rbody = (pr.ranking || []).map(function (r, i) {
      var last = i === pr.ranking.length - 1;
      return '<div class="row' + (r.is_self ? ' self' : '') + '" data-q="' + esc(r.q || '') + '" data-key="' + esc(r.key || '') + '" style="grid-template-columns:' + rcols + ';cursor:pointer' + (last ? ';border-bottom:none' : '') + '"><span class="mono c" style="font-size:13px' + (r.is_self ? ';font-weight:700' : '') + '">' + r.rank + '</span><span style="font-size:13px' + (r.is_self ? ';font-weight:700' : '') + '">' + esc(r.name) + '</span><span class="mono r" style="font-size:13px' + (r.is_self ? ';font-weight:700' : '') + '">' + (r.combined != null ? Math.round(r.combined) : '—') + '</span><span class="mono r" style="font-size:13px;color:var(--ink-3)">' + (r.value != null ? Math.round(r.value) : '—') + '</span><span class="mono r" style="font-size:13px;color:var(--ink-3)">' + (r.quality != null ? Math.round(r.quality) : '—') + '</span></div>';
    }).join('');
    $('rankTable').innerHTML = (pr.ranking && pr.ranking.length >= 3) ? rhead + rbody : '<div style="color:var(--ink-3);font-size:13px;padding:12px 0">피어 표본이 적어 랭킹을 만들 수 없습니다.</div>';
    wirePeerLinks();
  }

  function renderWacc() {
    var w = D.wacc;
    if (!w || w.error) { $('waccTiles').innerHTML = '<div style="color:var(--ink-3);font-size:13px">자본비용을 계산하지 못했습니다.</div>'; return; }
    $('waccPeriod').textContent = w.period_label ? '회귀 표본 · ' + w.period_label + ' (벤치마크 ' + D.meta.benchmark + ')' : '';
    var tiles = [['레버드 β_L', w.beta_l != null ? w.beta_l.toFixed(2) : '—'], ['무부채 β_U', w.beta_u != null ? w.beta_u.toFixed(2) : '—'], ['유효세율 t', fmtPct(w.tax, 0)], ['D/E (시가)', fmtPct(w.de, 0)], ['k_e (CAPM)', fmtPct(w.k_e)], ['k_d (세후)', fmtPct(w.k_d_after)]];
    $('waccTiles').innerHTML = tiles.map(function (t, i) { return '<div style="flex:1;min-width:120px;padding:' + (i === 0 ? '0 16px 0 0' : '0 16px') + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + t[0] + '</div><div class="mono" style="font-size:20px;font-weight:500;margin-top:6px">' + t[1] + '</div></div>'; }).join('');
    // 자본비용 경고(세율 폴백·법정세율 상한, 베타 클리핑 등). 직렬화만 되고 화면엔
    // 안 나오던 값이라, 타일 아래에 사유로 붙인다 — 특히 t는 왜 그 값인지가 중요하다.
    var wn = w.warnings || [];
    $('waccNotes').innerHTML = wn.length
      ? '<div class="wacc-notes">' + wn.map(function (m) {
          return '<div class="wacc-note">' + esc(m) + '</div>'; }).join('') + '</div>'
      : '';
    $('betaScatter').innerHTML = betaScatter();
    $('waccWaterfall').innerHTML = waccWaterfall();
    var sp = w.spread;
    var summary = [['WACC', w.wacc != null ? fmtPct(w.wacc) : 'N/A', ''], ['ROIC (TTM)', fmtPct(w.roic), ''], ['ROIC − WACC 스프레드', sp != null ? (sp >= 0 ? '+' : '') + (sp * 100).toFixed(1) + '%p' : '—', sp != null ? (sp >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)') : '']];
    $('waccSummary').innerHTML = summary.map(function (t, i) { return '<div style="flex:1;min-width:150px;padding:16px 18px' + (i ? ';border-left:1px solid var(--line)' : '') + (i === 2 ? ';background:var(--paper-2)' : '') + '"><div class="kick">' + t[0] + '</div><div class="mono" style="font-size:24px;font-weight:500;margin-top:6px' + (t[2] ? ';color:' + t[2] : '') + '">' + t[1] + '</div></div>'; }).join('');
    $('roicSeries').innerHTML = roicSeries();
    if (sp != null) $('roicCaption').innerHTML = 'ROIC가 WACC 위에 있어야 성장이 곧 가치 창출. 현재 스프레드 <b style="color:' + (sp >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)') + '">' + (sp >= 0 ? '양(+)' : '음(−)') + '</b> — ' + (sp >= 0 ? '가치 창출 구간.' : '가치 잠식 구간.');
  }

  function renderBacktest() {
    var bt = D.backtest;
    if (!bt || bt.error || !bt.ok) { $('btTiles').innerHTML = '<div style="color:var(--ink-3);font-size:13px">' + esc((bt && (bt.warnings || [])[0]) || '백테스트를 수행할 수 없습니다 (표본 부족).') + '</div>'; $('btTable').innerHTML = ''; $('backtestScatter').innerHTML = ''; $('equityCurve').innerHTML = ''; if ($('btScatterGuide')) $('btScatterGuide').innerHTML = ''; if ($('equityGuide')) $('equityGuide').innerHTML = ''; return; }
    var tiles = [['비중복 12M 표본', (bt.event_count || 0).toLocaleString('en-US') + '개'], ['신호 후 12M 평균', '<span style="color:' + (bt.ret12 >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)') + '">' + fmtSigned(bt.ret12) + '</span>'], ['그때 플러스 확률', bt.hit12 != null ? (bt.hit12 * 100).toFixed(0) + '%' : '—'], ['저평가↔수익 상관', '<span style="color:' + (bt.spearman >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)') + '">' + (bt.spearman != null ? (bt.spearman >= 0 ? '+' : '') + bt.spearman.toFixed(2) : '—') + '</span>']];
    $('btTiles').innerHTML = tiles.map(function (t, i) { return '<div style="flex:1;min-width:130px;padding:' + (i === 0 ? '0 16px 0 0' : '0 16px') + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + t[0] + '</div><div class="mono" style="font-size:20px;font-weight:500;margin-top:6px">' + t[1] + '</div></div>'; }).join('');
    // 정직한 한 줄 관찰 (저평가 신호 후 vs 아무 때나)
    var h12 = (bt.horizons || []).filter(function (h) { return h.h === '12개월'; })[0] || {};
    var lede;
    if (bt.signal_days > 0 && bt.ret12 != null) {
      var base12 = h12.base_mean;
      var cmp = (base12 != null && bt.ret12 > base12) ? '<b style="color:var(--dv-positive)">더 높았</b>' : '<b>특별히 높지는 않았</b>';
      lede = '저평가 신호는 총 <b class="mono">' + bt.signal_days.toLocaleString('en-US') + '거래일</b> 관찰됐습니다. 겹치는 보유기간을 제거한 <b class="mono">' + (bt.event_count || 0) + '개 표본</b>의 12개월 평균 수익은 <b class="mono" style="color:' + (bt.ret12 >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)') + '">' + fmtSigned(bt.ret12) + '</b>' + (bt.hit12 != null ? ' (플러스 확률 ' + (bt.hit12 * 100).toFixed(0) + '%)' : '') + ' — 비중복 전체 표본 평균(' + fmtSigned(base12) + ')보다 ' + cmp + '습니다.';
    } else {
      lede = '확보된 기간에 이 종목이 우리 기준 <b>저평가(+30%↑)</b>였던 적은 없었습니다 — 아래 관찰 통계가 비어 있는 이유예요. (다른 종목·기간에서는 신호가 잡히기도 합니다.)';
    }
    var mu = bt.methods_used || [];
    var methodNote = '';
    if (mu.length >= 2 && bt.weights) {
      var wB = Math.round((bt.weights['역사적 밴드'] || 0) * 100), wR = Math.round((bt.weights['수익가치(RIM)'] || 0) * 100);
      methodNote = '<div style="font-size:11.5px;color:var(--ink-3);margin-bottom:8px">검증 신호 = ② 역사적 밴드 + ③ RIM 가중 종합(' + wB + ':' + wR + '). ①·④는 사후검증 불가로 제외.</div>';
    } else if (mu.length === 1) {
      methodNote = '<div style="font-size:11.5px;color:var(--ink-3);margin-bottom:8px">검증 신호 = ② 역사적 밴드 단독(③ RIM 복원 불가). 종합 판정 일부만 검증됨.</div>';
    }
    if ($('btLede')) $('btLede').innerHTML = methodNote + lede;
    var head = '<div class="row head" style="grid-template-columns:1fr .7fr 1fr 1fr 1fr"><span class="col-label">보유기간</span><span class="col-label r">표본</span><span class="col-label r">평균수익</span><span class="col-label r">승률</span><span class="col-label r">전체평균</span></div>';
    var rows = (bt.horizons || []).map(function (h, i) { var last = i === bt.horizons.length - 1; return '<div class="row" style="grid-template-columns:1fr .7fr 1fr 1fr 1fr;font-family:var(--font-mono);font-size:12.5px' + (last ? ';border-bottom:none' : '') + '"><span style="font-family:var(--font-sans)">' + h.h + '</span><span class="r">' + (h.ev_n || 0) + '</span><span class="r" style="color:' + (h.ev_mean >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)') + '">' + fmtSigned(h.ev_mean) + '</span><span class="r">' + (h.ev_hit != null ? (h.ev_hit * 100).toFixed(0) + '%' : '—') + '</span><span class="r" style="color:var(--ink-3)">' + fmtSigned(h.base_mean) + '</span></div>'; }).join('');
    $('btTable').innerHTML = head + rows;
    $('backtestScatter').innerHTML = backtestScatter();
    $('equityCurve').innerHTML = equityCurve();

    // ── 산점도 읽는 법 (+ 순위상관 자동 해석) ──
    var li0 = '<li style="font-size:12.5px;color:var(--ink-2);line-height:1.65">';
    var thPct = ((bt.threshold || 0.3) * 100).toFixed(0);
    var rho = bt.spearman, rhoRead = '';
    if (rho != null) {
      rhoRead = rho >= 0.3
        ? '이 종목은 순위상관 <b>+' + rho.toFixed(2) + '</b> — 더 싸 보였던 날일수록 이후 12개월 수익이 높은 경향이 실제로 있었습니다.'
        : rho >= 0
          ? '이 종목은 순위상관 <b>+' + rho.toFixed(2) + '</b> — 관계가 있긴 하지만 약해서, 저평가 신호를 보조 근거 정도로만 쓰는 게 안전합니다.'
          : '이 종목은 순위상관 <b>−' + Math.abs(rho).toFixed(2) + '</b> — 싸 보였을 때 사도 이후 수익이 좋지 않았습니다. 낮은 멀티플이 실적 훼손의 반영(밸류트랩)이었을 가능성을 요약 탭 해설과 함께 확인하세요.';
    }
    if ($('btScatterGuide')) $('btScatterGuide').innerHTML =
      '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:14px 16px">' +
      '<div class="kick" style="margin-bottom:8px">이 그래프 읽는 법</div>' +
      '<ul style="margin:0;padding-left:17px;display:flex;flex-direction:column;gap:5px">' +
      li0 + '점 하나 = 과거의 어느 하루입니다. <b>가로축</b>은 그날의 저평가율(모형 적정가 ÷ 주가 − 1, +30%면 주가가 적정가보다 30% 싸 보였다는 뜻), <b>세로축</b>은 그날 사서 12개월 들고 있었을 때의 실제 수익률입니다.</li>' +
      li0 + '연한 <b>초록 배경</b>이 우리 기준 저평가 신호(+' + thPct + '%↑) 구간입니다. 이 구간의 점들이 가로 0선 위(플러스)에 몰려 있을수록 신호가 과거에 통했다는 뜻입니다.</li>' +
      li0 + '<b>클레이색 사선</b>은 전체 점의 추세선 — 오른쪽 위로 기울수록 "더 싸 보일 때 살수록 이후 수익이 좋았다"입니다. 상단 타일의 <b>저평가↔수익 상관</b>(순위상관, −1~+1)이 이 관계의 일관성을 숫자 하나로 요약합니다.</li>' +
      li0 + '주의: 한 종목의 5년 남짓 표본이고 인접한 날들은 사실상 같은 사건이라, 관계가 보여도 우연일 수 있습니다. 방향 참고용이지 매매 규칙이 아닙니다.</li>' +
      '</ul>' + (rhoRead ? '<div style="font-size:12.5px;color:var(--ink);margin-top:10px;line-height:1.6">' + rhoRead + '</div>' : '') + '</div>';

    // ── 자산곡선: 선 설명 + CAGR 풀이 + 자동 비교 한 줄 ──
    var eqd = bt.equity, cag = (eqd && eqd.cagr) || {};
    var cs = cag['저평가 매수 전략'], cb = cag['단순 보유(Buy&Hold)'];
    var benchKey = Object.keys(cag).filter(function (k) { return k !== '저평가 매수 전략' && k !== '단순 보유(Buy&Hold)'; })[0];
    var cbm = benchKey != null ? cag[benchKey] : null;
    var eqRead = (cs != null && cb != null)
      ? '이 종목·기간에서는 저평가 매수 전략 CAGR <b>' + (cs * 100).toFixed(1) + '%</b> vs 단순 보유 <b>' + (cb * 100).toFixed(1) + '%</b>' +
        (cbm != null ? ' vs ' + esc(benchKey) + ' <b>' + (cbm * 100).toFixed(1) + '%</b>' : '') + ' — ' +
        (cs > cb ? '신호가 종목 보유 대비 초과 성과를 냈습니다. 다만 한 종목의 사후 검증이라 우연·과최적화 가능성은 남습니다.'
                 : '신호가 단순 보유를 이기지 못했습니다. 저평가 신호는 매매 타이밍 도구가 아니라 "지금 가격이 어느 수준인지" 보는 관찰 보조로 쓰는 게 안전합니다.')
      : '';
    if ($('equityGuide')) $('equityGuide').innerHTML =
      '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:14px 16px">' +
      '<div class="kick" style="margin-bottom:8px">선과 숫자의 뜻</div>' +
      '<ul style="margin:0;padding-left:17px;display:flex;flex-direction:column;gap:5px">' +
      li0 + '<b>남색 선 · 저평가 매수 전략</b>: 모형이 저평가(+' + thPct + '%↑)로 본 날에만 주식을 보유하고, 아닌 날은 현금(수익 0)으로 쉬는 규칙의 가상 자산 가치입니다. 신호가 뜬 <b>다음 날</b> 진입해 미래 정보를 미리 쓰는 것(룩어헤드)을 막았습니다.</li>' +
      li0 + '<b>회청색 선 · 단순 보유(Buy&Hold)</b>: 같은 기간 처음에 사서 끝까지 그냥 들고 있었을 때 — 전략을 평가하는 비교 기준선입니다.</li>' +
      (benchKey ? li0 + '<b>클레이색 선 · ' + esc(benchKey) + '</b>: 같은 기간 시장 전체가 얼마나 움직였는지입니다. 종목·전략의 성과가 시장 상승 덕분인지, 종목 자체의 힘인지 가려주는 배경 기준입니다.</li>' : '') +
      li0 + '모든 선은 시작을 100으로 맞춘 상대 가치이고, 거래비용·세금·슬리피지는 반영되지 않았습니다.</li>' +
      li0 + '<b>CAGR</b>(연평균 복리 성장률) = 전체 기간의 최종 결과를 "매년 몇 %씩 복리로 불린 셈인가"로 환산한 값입니다. 예: 5년에 100→200이면 CAGR ≈ 14.9%. 범례 괄호 속 숫자가 이것입니다.</li>' +
      li0 + '읽는 법: 전략 선이 높다고 무조건 좋은 게 아니라, <b>하락 구간을 신호가 피해 갔는지</b>(전략 선이 덜 꺾였는지)를 보는 게 핵심입니다.</li>' +
      '</ul>' + (eqRead ? '<div style="font-size:12.5px;color:var(--ink);margin-top:10px;line-height:1.6">' + eqRead + '</div>' : '') + '</div>';
  }

  function renderAi() {
    var v = D.verdict, m = D.meta, p = D.price;
    var bulls = (D.commentary || []).filter(function (c) { return c.kind === 'good'; }).slice(0, 4);
    var bears = (D.commentary || []).filter(function (c) { return c.kind === 'bad' || c.kind === 'warn'; }).slice(0, 4);
    var stance = vIdx(v.verdict) <= 0 ? '큰 저평가 관찰' : vIdx(v.verdict) === 1 ? '저평가 관찰' : vIdx(v.verdict) === 2 ? '적정 범위 관찰' : vIdx(v.verdict) === 3 ? '고평가 관찰' : '큰 고평가 관찰';
    var up = v.gap != null && v.gap >= 0;
    var target = (v.fair_low != null && v.fair_high != null) ? won(v.fair_low) + '~' + won(v.fair_high) : '—';
    var upside = (v.fair_low != null && v.fair_high != null && m.price) ? fmtSigned(v.fair_low / m.price - 1) + '~' + fmtSigned(v.fair_high / m.price - 1) : '';
    var stop = (p && p.lo52 != null) ? won(p.lo52) : '—';
    function li(arr) { return arr.length ? arr.map(function (c) { return '<li>' + esc(c.text) + '</li>'; }).join('') : '<li>—</li>'; }
    $('aiContent').innerHTML =
      '<div style="background:var(--navy);color:var(--navy-ink);border-radius:var(--radius-md);padding:26px 28px">' +
        '<div style="font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:var(--navy-muted)">한 줄 관찰 · 규칙 기반</div>' +
        '<div style="font-family:var(--font-display);font-weight:700;font-size:28px;letter-spacing:-0.01em;margin-top:8px">' + stance + ' — ' + esc(v.verdict) + ' · 괴리율 ' + fmtSigned(v.gap) + '</div>' +
        '<div style="font-size:13px;color:var(--navy-ink);margin-top:10px;line-height:1.6">대시보드 산출 사실(적정가 범위·상승여력·업종 백분위·자본비용)을 근거로 한 스탠스입니다.' + (m.ai_available ? ' 아래 버튼으로 Gemini 서술형 종합 평가를 생성할 수 있어요.' : ' 서술형 AI 평가는 Gemini 키를 설정하면 생성됩니다.') + '</div>' +
        (m.ai_available ? '<button id="opBtn" class="btn btn-sm" style="margin-top:16px;background:var(--paper);color:var(--ink)">✦ 종합 투자평가 생성 (Gemini)</button>' : '') + '</div>' +
        '<div id="opOut"></div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:20px">' +
        '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:16px 18px"><div style="font-size:13px;font-weight:600;color:var(--dv-positive)">강세 논거</div><ul style="margin:10px 0 0;padding-left:18px;font-size:12.5px;color:var(--ink-2);line-height:1.8">' + li(bulls) + '</ul></div>' +
        '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:16px 18px"><div style="font-size:13px;font-weight:600;color:var(--dv-negative)">약세 논거·리스크</div><ul style="margin:10px 0 0;padding-left:18px;font-size:12.5px;color:var(--ink-2);line-height:1.8">' + li(bears) + '</ul></div></div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">' +
        '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:16px 18px"><div style="font-size:13px;font-weight:600">적정가 추정 범위 · 괴리율</div><div style="display:flex;align-items:baseline;gap:10px;margin-top:10px"><span class="mono" style="font-size:20px;font-weight:600">' + target + '</span><span style="font-size:13px;color:' + (up ? 'var(--dv-positive)' : 'var(--dv-negative)') + '">' + upside + '</span></div><div style="font-size:11.5px;color:var(--ink-3);margin-top:6px">3개 모형의 추정 범위이며 추천 목표가가 아닙니다</div></div>' +
        '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:16px 18px"><div style="font-size:13px;font-weight:600">관찰을 재검토할 기준</div><div style="font-size:12.5px;color:var(--ink-2);line-height:1.7;margin-top:10px">52주 최저 <b class="mono">' + stop + '</b> 이탈 시 현재 추세 해석을 다시 확인하세요. 신뢰도 <b>' + esc(v.confidence || '—') + '</b> — 방법 간 편차가 크면 보수적으로 해석하세요.</div></div></div>' +
      '<div style="font-size:10.5px;color:var(--ink-3);margin-top:14px;line-height:1.6">본 스탠스는 대시보드 산출 데이터에 기반한 규칙적 요약이며, 서술형 AI 평가·최종 판단은 이용자 책임입니다. 특정 종목의 매수·매도 추천이 아닙니다.</div>';
    var ob = $('opBtn'); if (ob) ob.addEventListener('click', function () { aiFetch('opinion', $('opOut'), ob); });
  }

  /* ══════════ 전체 렌더 ══════════ */
  /* ══════════ ETF 뷰 (kind=etf) — 주식 9탭 대신 3축·차트로 ══════════ */
  function setEtfMode(on) {
    $('etfView').style.display = on ? 'block' : 'none';
    $('tabArea').style.display = on ? 'none' : '';
    document.querySelectorAll('.panel').forEach(function (p) { p.style.display = on ? 'none' : ''; });
  }
  function etfTone(v) {
    if (!v) return 'neutral';
    if (v.indexOf('저평가') >= 0) return 'positive';
    if (v.indexOf('고평가') >= 0 || v.indexOf('프리미엄') >= 0) return 'negative';
    return 'neutral';
  }
  function etfMarkerPos(gap, primary) {
    if (gap == null) return null;
    var scale = primary === 'premium' ? 0.03 : 0.30;   // 배당갭 ±30%·괴리 ±3%를 만폭으로
    var g = Math.max(-1, Math.min(1, gap / scale));
    return 50 - g * 40;                                 // gap>0(쌈)=왼쪽(저평가)
  }
  function addEtfToBasket() {
    var b; try { b = JSON.parse(localStorage.getItem('invportfolio') || '{}'); } catch (e) { b = {}; }
    // 포트폴리오는 야후 티커를 키로 쓴다 — 국내 ETF는 6자리 코드에 .KS를 붙여야 시세가 붙는다.
    var kr = D.currency === 'KRW', yahoo = kr ? D.symbol + '.KS' : D.symbol;
    b[yahoo] = { name: D.name, yahoo: yahoo, ticker: D.symbol,
      type: kr ? '국내기타ETF' : '해외ETF', currency: D.currency, 'class': 'ETF' };
    localStorage.setItem('invportfolio', JSON.stringify(b));
    var btn = $('etfBasketBtn'); if (btn) { btn.textContent = '✓ 담았어요 — 🧺 포트폴리오에서 확인'; setTimeout(function () { btn.textContent = '＋ 포트폴리오에 담기'; }, 1800); }
  }
  function renderEtfHeader() {
    var initial = /[A-Za-z]/.test(D.name[0]) ? D.name[0].toUpperCase() : D.name[0];
    var tone = etfTone(D.verdict);
    // 주식과 같은 규약 — 판단은 무채 잉크, 색은 숫자의 부호에만(R4)
    var vColor = 'var(--ink)';
    var mono = 'var(--font-mono)', disp = 'var(--font-display)';
    var mpos = etfMarkerPos(D.gap, D.primary);
    var held = D.verdict == null;
    var headline = held ? '판정 보류' : D.verdict;
    var primAxis = (D.axes || []).filter(function (a) { return a.key === D.primary; })[0];
    // 판정 보류 유형이라도 상대 위치가 있으면 그걸 헤드라인으로 — "보류"만 크게 띄우면
    // 쓸 정보가 없다는 인상만 남는다. 대신 '적정가 판정은 보류'를 작은 뱃지로 붙여 톤을 지킨다.
    var rel = D.relative || {}, relHead = held && rel.stance && rel.pos != null;
    if (relHead) {
      headline = rel.stance;
      tone = rel.pos >= 65 ? 'negative' : rel.pos <= 35 ? 'positive' : 'neutral';
      mpos = rel.pos;
    }
    var subline = held
      ? (relHead
          ? '시장 대비 가격비율이 5년 분포의 ' + Math.round(rel.pos) + '번째 백분위입니다.'
          : '이 유형은 이익·배당 기반 적정가가 어려워, 아래 참고 지표만 제공합니다.')
      : (primAxis ? primAxis.value : '');
    var holdBadge = relHead ? ' ' + badge('적정가 판정은 보류') : '';
    var confTip = 'ETF 판정 신뢰도 — 주 신호(' + esc(D.primary) + ')의 데이터 충실도와 유형 적합성 기준입니다.';
    $('hv-B').innerHTML =
      '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:22px 24px;display:flex;gap:32px;align-items:center;flex-wrap:wrap">' +
        '<div style="min-width:210px"><div style="display:flex;align-items:center;gap:11px">' +
          '<span style="width:38px;height:38px;flex:none;border-radius:var(--radius-sm);background:var(--ink);color:var(--paper);display:inline-flex;align-items:center;justify-content:center;font-family:' + disp + ';font-weight:900;font-size:18px">' + esc(initial) + '</span>' +
          '<div><div style="display:flex;align-items:center;gap:7px"><span style="font-family:' + mono + ';font-size:12px;color:var(--ink-3)">' + esc(D.symbol) + '</span>' + badge('ETF · ' + D.type_label, 'info') + '</div>' +
          '<div style="font-family:' + disp + ';font-weight:700;font-size:23px;letter-spacing:-0.01em;line-height:1.15;margin-top:3px">' + esc(D.name) + '</div></div></div>' +
          '<div style="font-family:' + mono + ';font-size:29px;font-weight:500;margin-top:14px">' + fmtPrice(D.price) + '</div></div>' +
        '<div style="flex:1;min-width:320px"><div style="display:flex;justify-content:space-between;align-items:baseline"><span class="kick">ETF 판정</span><span style="font-size:12px;color:var(--ink-3)">신뢰도 <b class="na" tabindex="0" data-tip="' + esc(confTip) + '" style="color:var(--ink-2)">' + esc(D.confidence || '—') + '</b></span></div>' +
          '<div style="display:flex;align-items:baseline;gap:12px;margin-top:9px;flex-wrap:wrap">' +
            '<span style="font-family:' + disp + ';font-weight:800;font-size:25px;line-height:1;letter-spacing:-0.01em;color:' + vColor + '">' + esc(headline) + '</span>' + holdBadge +
            '<span style="font-size:13px;color:var(--ink-2);line-height:1.4">' + esc(subline) + '</span></div>' +
          '<div style="position:relative;margin-top:15px;padding-bottom:28px">' +
            // ETF 눈금은 **항상** 관찰 어휘다. ETF는 재무제표가 없어 적정가(내재가치)를 아예
            // 계산하지 않고, 세 축(NAV 괴리·바스켓 상대·배당 밴드) 모두 '이미 있는 기준 안에서의
            // 위치'를 잴 뿐이다. 예전에는 적정가 판정을 보류한 경우에만 싼/비싼 구간으로 바꿔 달았는데
            // (PR #47), 그러면 배당 밴드로 판정한 ETF는 헤드라인이 "다소 비싼 구간"인데 눈금은
            // "저평가·적정·고평가"가 되어 한 화면에서 층위가 갈렸다(R3 발견 1의 뒤끝).
            '<div style="display:flex;font-size:11.5px;letter-spacing:.02em;color:var(--ink-3);margin-bottom:7px">' +
              '<span style="flex:1;text-align:left' + (tone === 'positive' ? ';color:var(--ink);font-weight:700' : '') + '">싼 구간</span>' +
              '<span style="flex:1;text-align:center' + (tone === 'neutral' ? ';color:var(--ink);font-weight:700' : '') + '">보통</span>' +
              '<span style="flex:1;text-align:right' + (tone === 'negative' ? ';color:var(--ink);font-weight:700' : '') + '">비싼 구간</span></div>' +
            '<div style="display:flex;height:13px;border-radius:var(--radius-pill);overflow:hidden">' +
              '<span style="flex:1;background:var(--dv-green);opacity:.82"></span><span style="flex:1;background:var(--dv-green);opacity:.45"></span><span style="flex:1;background:var(--paper-3)"></span><span style="flex:1;background:var(--dv-clay);opacity:.45"></span><span style="flex:1;background:var(--dv-clay);opacity:.82"></span></div>' +
            (mpos == null
              ? '<div style="position:absolute;left:50%;top:25px;transform:translateX(-50%);white-space:nowrap;font-size:10.5px;color:var(--ink-3)">판정 보류 — 참고 지표만</div>'
              : '<div style="position:absolute;left:' + mpos + '%;top:26px;transform:translateX(-50%);width:2px;height:22px;background:var(--ink)"></div>' +
                '<div style="position:absolute;left:' + mpos + '%;top:22px;transform:translateX(-50%);width:11px;height:11px;border-radius:50%;background:var(--ink);border:2px solid var(--paper);box-shadow:var(--shadow-sm)"></div>' +
                '<div style="position:absolute;left:' + mpos + '%;top:51px;transform:translateX(-50%);white-space:nowrap;font-family:' + mono + ';font-size:10.5px;font-weight:700;color:' + (relHead ? vColor : 'var(--ink)') + '">' + (relHead ? '상대 위치(참고)' : '현재가') + '</div>') +
          '</div>' +
          '<div style="margin-top:9px;font-family:' + mono + ';font-size:10.5px;color:var(--ink-3)">기준 · 주가 ' + esc((D.asOf && D.asOf.price) || '—') + ' · 벤치마크 ' + esc(D.metrics.bench_label || '—') + '</div></div>' +
        '<div style="display:flex;flex-direction:column;gap:8px"><button id="etfBasketBtn" class="btn btn-primary btn-sm">＋ 포트폴리오에 담기</button></div></div>';
    var bb = $('etfBasketBtn'); if (bb) bb.addEventListener('click', addEtfToBasket);
  }
  function renderEtfTiles() {
    var mk = {}; (D.masked || []).forEach(function (m) { mk[m[0]] = m[1]; });
    var mt = D.metrics, tr = D.trend;
    var perCell = mk['바스켓 PER'] ? na(mk['바스켓 PER']) : (mt.basket_pe != null ? fmtX(mt.basket_pe) : na('구성종목의 이익 지표를 무료 데이터로 모으지 못해 바스켓 PER를 계산할 수 없습니다.'));
    var items = [
      ['현재가', fmtPrice(D.price)],
      ['NAV 대비', D.premium != null ? fmtSigned(D.premium) : na('NAV(순자산가치)를 받지 못했습니다.')],
      ['배당수익률', mt.div_yield != null ? fmtPct(mt.div_yield, 2) : na('분배 이력이 없거나 무료 소스가 제공하지 않아 배당수익률을 계산할 수 없습니다.')],
      ['바스켓 PER', perCell],
      ['52주 위치', tr.w52_pos != null ? Math.round(tr.w52_pos) + '%' : na('시세 이력이 짧습니다.')],
      ['순자산(AUM)', mt.aum != null ? fmtMoney(mt.aum) : na('운용사 순자산(AUM) 공시를 무료 소스에서 받지 못했습니다 — 펀드 규모는 판단에서 빼세요.')]];
    $('tiles').innerHTML = items.map(function (it, i) {
      return '<div style="padding:0 16px' + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + it[0] + '</div><div class="mono" style="font-size:22px;font-weight:500;margin-top:7px;white-space:nowrap">' + it[1] + '</div></div>';
    }).join('');
  }
  function etfChart() {
    var s = D.priceSeries || { x: [], y: [] };
    var ys = s.y || [], xs = s.x || [];
    if (ys.length < 2) return '<div style="color:var(--ink-3);font-size:13px">시세 이력이 부족합니다.</div>';
    var W = 900, H = 240, padL = 6, padR = 6, padT = 12, padB = 22;
    var lo = Math.min.apply(null, ys), hi = Math.max.apply(null, ys);
    var rng = (hi - lo) || 1; lo -= rng * 0.06; hi += rng * 0.06; rng = hi - lo;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    function X(i) { return padL + i / (ys.length - 1) * plotW; }
    function Y(v) { return padT + (1 - (v - lo) / rng) * plotH; }
    var d = 'M' + ys.map(function (v, i) { return X(i).toFixed(1) + ' ' + Y(v).toFixed(1); }).join(' L');
    var area = d + ' L' + X(ys.length - 1).toFixed(1) + ' ' + (padT + plotH) + ' L' + padL + ' ' + (padT + plotH) + ' Z';
    var last = ys[ys.length - 1], up = last >= ys[0], col = up ? 'var(--dv-green)' : 'var(--dv-clay)';
    var els = [
      el('path', { d: area, fill: col, fillOpacity: 0.08, stroke: 'none' }),
      el('path', { d: d, fill: 'none', stroke: col, strokeWidth: 1.8, strokeLinejoin: 'round' }),
      el('circle', { cx: X(ys.length - 1), cy: Y(last), r: 3.5, fill: col }),
      el('text', { x: X(ys.length - 1) - 5, y: Y(last) - 8, textAnchor: 'end', fontFamily: 'var(--font-mono)', fontSize: 11, fill: 'var(--ink)' }, fmtPrice(last)),
      el('text', { x: padL, y: H - 5, fontFamily: 'var(--font-mono)', fontSize: 10, fill: 'var(--ink-3)' }, esc(xs[0] || '')),
      el('text', { x: W - padR, y: H - 5, textAnchor: 'end', fontFamily: 'var(--font-mono)', fontSize: 10, fill: 'var(--ink-3)' }, esc(xs[xs.length - 1] || ''))];
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }
  var EMONO = 'var(--font-mono)';
  function etfSectionHead(idx, title, desc, meta) {
    return '<div class="analysis-section-head"><span class="analysis-section-index">' + idx + '</span>' +
      '<div><h3 class="analysis-section-title">' + title + '</h3>' + (desc ? '<p class="analysis-section-desc">' + desc + '</p>' : '') + '</div>' +
      (meta ? '<span class="analysis-section-meta">' + esc(meta) + '</span>' : '') + '</div>';
  }
  // 축 신호 → 색·라벨 (싼 0 ↔ 비쌈 100). 신호가 약한 축은 중립색으로 눌러 오독을 막는다.
  // 라벨은 '쪽'이 아니라 '구간' — '쪽'은 방향을 가리키는 구어체라 분석 화면 톤에서 겉돈다.
  // col = 게이지 마커·배지 틴트(면의 색) / ink = 글자색.
  // 축별 신호도 '이미 있는 기준 안에서 지금이 어디냐'는 판단이라 글자는 무채 잉크로 쓴다(R4).
  // 색은 면에 남겨 한눈에 훑을 수 있게 하고, 읽는 글자는 대비를 지킨다.
  function etfSigTone(pos, weak) {
    if (pos == null) return { col: 'var(--ink-3)', ink: 'var(--ink-3)', label: '자료 없음', soft: true };
    if (weak) return { col: 'var(--ink-3)', ink: 'var(--ink-3)', label: '신호 약함', soft: true };
    if (pos >= 65) return { col: 'var(--dv-clay)', ink: 'var(--ink)', label: '비싼 구간', soft: false };
    if (pos <= 35) return { col: 'var(--dv-green)', ink: 'var(--ink)', label: '싼 구간', soft: false };
    return { col: 'var(--ink-3)', ink: 'var(--ink-3)', label: '중립', soft: true };
  }
  // 미니 게이지 — 서로 단위가 다른 축을 같은 자로 훑어보게 하는 핵심 장치
  function etfGauge(pos, tone, showEnds) {
    var track = '<div style="position:relative;height:6px;border-radius:var(--radius-pill);background:linear-gradient(90deg,rgba(47,125,91,.28),var(--paper-3) 45%,var(--paper-3) 55%,rgba(181,100,60,.28))">' +
      (pos == null ? '' :
        '<span style="position:absolute;left:' + pos.toFixed(0) + '%;top:-4px;transform:translateX(-50%);width:11px;height:14px;border-radius:var(--radius-sm);background:' + tone.col + ';border:2px solid var(--paper);box-shadow:var(--shadow-sm)' + (tone.soft ? ';opacity:.75' : '') + '"></span>') +
      '</div>';
    var ends = showEnds ? '<div style="display:flex;justify-content:space-between;font-size:10px;letter-spacing:.02em;color:var(--ink-3);margin-top:5px"><span>싼 구간</span><span>비싼 구간</span></div>' : '';
    return track + ends;
  }
  // ① 판정 — 종합 신호 한 줄 + 축별 게이지 행
  function etfPanelVerdict() {
    var rel = D.relative || {}, sc = D.signalCounts || {};
    var rows = (D.axes || []).map(function (a, i) {
      var tone = etfSigTone(a.available ? a.pos : null, a.weak);
      var lead = a.available ? (a.lead || a.note) : a.note;
      var tip = a.note && a.lead ? ' <span class="na" tabindex="0" data-tip="' + esc(a.note) + '" style="color:var(--ink-3);cursor:help">ⓘ</span>' : '';
      return '<div style="display:grid;grid-template-columns:186px minmax(190px,1fr) 180px 76px;gap:18px;align-items:center;padding:15px 0' + (i ? ';border-top:1px solid var(--line)' : '') + '">' +
        '<div class="kick" style="color:' + (a.available ? 'var(--dv-navy)' : 'var(--ink-3)') + '">' + esc(a.title) + '</div>' +
        '<div><div style="font-family:' + EMONO + ';font-size:16px;font-weight:600;color:' + (a.available ? 'var(--ink)' : 'var(--ink-3)') + '">' + esc(a.value) + '</div>' +
          '<div style="font-size:12px;color:var(--ink-2);margin-top:4px;line-height:1.5">' + esc(lead) + tip + '</div></div>' +
        '<div>' + etfGauge(a.available ? a.pos : null, tone, i === 0) + '</div>' +
        '<div style="text-align:right"><span style="display:inline-block;padding:3px 9px;border-radius:var(--radius-pill);font-size:11.5px;font-weight:700;color:' + tone.ink + ';background:' + (tone.soft ? 'var(--paper-3)' : (tone.col === 'var(--dv-clay)' ? 'color-mix(in srgb, var(--dv-clay) 14%, var(--paper))' : 'color-mix(in srgb, var(--dv-green) 14%, var(--paper))')) + '">' + tone.label + '</span></div></div>';
    }).join('');

    // 종합 — 판정 보류 유형에서도 "그래서 지금 어느 쪽인가"를 먼저 보여준다.
    var summary = '';
    if (rel.stance && rel.pos != null) {
      var st = etfSigTone(rel.pos, false);
      var parts = [];
      // "지표 4개 중 2개가 비싼 구간, 2개는 중립입니다" — 조각 나열이 아니라 한 문장으로.
      var total = (sc.expensive || 0) + (sc.cheap || 0) + (sc.neutral || 0);
      if (sc.expensive) parts.push(sc.expensive + '개가 비싼 구간');
      if (sc.cheap) parts.push(sc.cheap + '개가 싼 구간');
      if (sc.neutral) parts.push(sc.neutral + '개는 중립');
      summary = '<div style="border-left:3px solid ' + st.col + ';padding:2px 0 2px 18px;margin-bottom:22px">' +
        '<div class="kick" style="color:var(--ink-3)">종합 신호 · 시장·역사 대비 위치</div>' +
        '<div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-top:7px">' +
          '<span style="font-family:var(--font-display);font-weight:800;font-size:25px;letter-spacing:-0.01em;color:' + st.col + '">' + esc(rel.stance) + '</span>' +
          '<span style="font-size:13px;color:var(--ink-2)">' + esc(total ? '지표 ' + total + '개 중 ' + parts.join(', ') + '입니다' : '') + '</span></div>' +
        '<div style="max-width:420px;margin-top:12px">' + etfGauge(rel.pos, st, true) + '</div>' +
        '<div style="font-size:11.5px;color:var(--ink-3);margin-top:9px;line-height:1.6">적정가(펀더멘털) 판정이 아니라 <b>시장 대비 가격비율의 5년 위치</b>입니다 — 성장 우위가 구조적이면 약할 수 있어 방향 참고로만 보세요.</div></div>';
    }
    var notesHtml = (D.notes || []).length
      ? '<div style="margin-top:18px;border-left:3px solid var(--dv-navy);background:var(--paper-2);border-radius:var(--radius-sm);padding:13px 16px;font-size:12.5px;color:var(--ink-2);line-height:1.7">' +
        (D.notes || []).map(function (n) { return '· ' + esc(n); }).join('<br/>') + '</div>' : '';
    return '<div class="method-map"><span class="method-chip">ETF 적정가</span><span>기업 재무가 없는 ETF는 여러 축을 <b>같은 자(싼 구간 ↔ 비싼 구간)</b>로 환산해 함께 봅니다 — ①NAV 괴리 ②바스켓 지표(벤치마크 대비) ③배당수익률 역사밴드 ④금리 대비 이익수익률(ERP).</span></div>' +
      '<div style="margin-top:18px">' + summary + rows + '</div>' + notesHtml;
  }
  // ② 구성·보유 — 상위종목·섹터·자산군
  function etfAssetClasses() {
    var ac = D.assetClasses || []; if (!ac.length) return '';
    return '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px">' + ac.map(function (a) {
      return '<span style="font-size:12px;color:var(--ink-2);border:1px solid var(--line);border-radius:var(--radius-pill);padding:4px 11px">' + esc(a.label) + ' <b class="mono">' + (a.weight * 100).toFixed(1) + '%</b></span>';
    }).join('') + '</div>';
  }
  // 국가 비중 — 한국(네이버)에서만 온다. 해외형이 실제로 어디에 투자하는지 확인용.
  function etfCountries() {
    var cs = (D.countries || []).filter(function (c) { return c.weight > 0.0005; });
    if (!cs.length) return '';
    return '<div style="margin-top:14px"><div class="kick" style="margin-bottom:6px">투자 국가</div>' +
      '<div style="display:flex;flex-wrap:wrap;gap:8px">' + cs.map(function (c) {
        return '<span style="font-size:12px;color:var(--ink-2);border:1px solid var(--line);border-radius:var(--radius-pill);padding:4px 11px">' + esc(c.label) + ' <b class="mono">' + (c.weight * 100).toFixed(1) + '%</b></span>';
      }).join('') + '</div></div>';
  }
  function etfPanelHoldings() {
    var h = D.holdings || [], secs = D.sectors || [];
    if (!h.length && !secs.length) {
      return etfSectionHead('01', '자산군 구성', '이 ETF는 개별 종목 구성을 제공하지 않습니다(채권·원자재 등). 자산군 비중만 표시합니다.', null) +
        '<div class="analysis-section-body">' + (etfAssetClasses() || '<span style="color:var(--ink-3)">구성 데이터가 없습니다.</span>') + '</div>';
    }
    var maxW = h.length ? (h[0].weight || 1) : 1;
    var holdRows = h.map(function (x, i) {
      // 비중이 없는 경우(국내 상장 해외 ETF는 네이버가 구성비를 '-'로 준다)는 0%가 아니라 '—'.
      // 0%로 그리면 '거의 안 담았다'는 반대 뜻으로 읽힌다.
      var w = x.weight, has = w != null;
      var bar = has
        ? '<span style="width:52px;height:6px;background:var(--paper-3);border-radius:var(--radius-pill);overflow:hidden"><span style="display:block;height:100%;width:' + (w / maxW * 100).toFixed(0) + '%;background:var(--dv-navy)"></span></span><b class="mono" style="font-size:12px">' + (w * 100).toFixed(1) + '%</b>'
        : '<span class="na" tabindex="0" data-tip="운용사가 구성 비중을 공개하지 않아 순서만 표시합니다." style="font-size:12px">—</span>';
      // 보유종목은 그 자체가 분석 가능한 기업이라 종목 페이지로 연결한다.
      // 시장은 심볼 형태로 가른다 — 6자리 숫자=국내(005930), 알파벳=미국(NVDA).
      // 심볼이 없으면(현금·기타) 링크하지 않는다.
      var label = (x.symbol ? '<b style="font-family:' + EMONO + ';font-size:12px">' + esc(x.symbol) + '</b> ' : '') +
        '<span style="font-size:12.5px;color:var(--ink-2)">' + esc(x.name) + '</span>';
      var cell = label;
      if (x.symbol) {
        var mkt = /^\d{6}$/.test(x.symbol) ? 'KR' : 'US';
        cell = '<a class="etf-hold-link" href="stock.html?market=' + mkt + '&q=' + encodeURIComponent(x.symbol) + '"' +
          ' title="' + esc(x.name) + ' 분석 보기" style="display:inline-block;max-width:100%">' + label + '</a>';
      }
      return '<div style="display:grid;grid-template-columns:22px 1fr 96px;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)">' +
        '<span class="mono" style="font-size:11.5px;color:var(--ink-3)">' + (i + 1) + '</span>' +
        '<span style="min-width:0">' + cell + '</span>' +
        '<span style="display:flex;align-items:center;gap:7px;justify-content:flex-end">' + bar + '</span></div>';
    }).join('');
    var maxS = secs.length ? Math.max.apply(null, secs.map(function (s) { return s.weight; })) : 1;
    var secRows = secs.map(function (s) {
      return '<div style="display:grid;grid-template-columns:104px 1fr 50px;gap:10px;align-items:center;margin-bottom:8px">' +
        '<span style="font-size:12.5px;color:var(--ink-2)">' + esc(s.label) + '</span>' +
        '<span style="height:8px;background:var(--paper-3);border-radius:var(--radius-md);overflow:hidden"><span style="display:block;height:100%;width:' + (s.weight / maxS * 100).toFixed(0) + '%;background:var(--dv-gold)"></span></span>' +
        '<b class="mono" style="font-size:12px;text-align:right">' + (s.weight * 100).toFixed(1) + '%</b></div>';
    }).join('');
    return '<div style="display:flex;flex-wrap:wrap;gap:34px">' +
      '<div style="flex:1;min-width:320px">' + etfSectionHead('01', '상위 보유 종목', '비중이 큰 순서입니다. 상위 종목이 성과를 좌우합니다. 종목을 누르면 그 기업 분석으로 이동합니다.', '상위 ' + h.length + '개') +
        '<div class="analysis-section-body">' + (holdRows || '<span style="color:var(--ink-3)">보유 종목 데이터가 없습니다.</span>') + '</div></div>' +
      '<div style="flex:1;min-width:300px">' + etfSectionHead('02', '섹터 비중', '어느 산업에 얼마나 노출돼 있는지입니다.', null) +
        '<div class="analysis-section-body">' + (secRows || '<span style="color:var(--ink-3)">섹터 데이터가 없습니다.</span>') +
        '<div style="margin-top:14px"><div class="kick" style="margin-bottom:6px">자산군</div>' + etfAssetClasses() + '</div>' +
        etfCountries() + '</div></div></div>';
  }
  // ③ 성과·추이 — 벤치마크 대비 오버레이 + 수익률표
  function etfRelChart() {
    var rs = D.relSeries || {}, xs = rs.x || [], e = rs.etf || [], b = rs.bench || [];
    if (e.length < 2) return etfChart();
    var all = e.concat(b), lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    var W = 900, H = 280, padL = 6, padR = 6, padT = 14, padB = 22, rng = (hi - lo) || 1; lo -= rng * 0.06; hi += rng * 0.06; rng = hi - lo;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    function X(i) { return padL + i / (e.length - 1) * plotW; }
    function Y(v) { return padT + (1 - (v - lo) / rng) * plotH; }
    function path(a) { return 'M' + a.map(function (v, i) { return X(i).toFixed(1) + ' ' + Y(v).toFixed(1); }).join(' L'); }
    var els = [
      el('line', { x1: padL, y1: Y(100), x2: W - padR, y2: Y(100), stroke: 'var(--line-strong)', strokeWidth: 1, strokeDasharray: '3 3' }),
      el('path', { d: path(b), fill: 'none', stroke: 'var(--ink-3)', strokeWidth: 1.4 }),
      el('path', { d: path(e), fill: 'none', stroke: 'var(--dv-navy)', strokeWidth: 2 }),
      el('text', { x: X(e.length - 1) - 4, y: Y(e[e.length - 1]) - 6, textAnchor: 'end', fontFamily: EMONO, fontSize: 11, fill: 'var(--dv-navy)', fontWeight: 700 }, 'ETF ' + e[e.length - 1].toFixed(0)),
      el('text', { x: X(b.length - 1) - 4, y: Y(b[b.length - 1]) + 13, textAnchor: 'end', fontFamily: EMONO, fontSize: 11, fill: 'var(--ink-3)' }, '벤치 ' + b[b.length - 1].toFixed(0)),
      el('text', { x: padL, y: H - 5, fontFamily: EMONO, fontSize: 10, fill: 'var(--ink-3)' }, esc(xs[0] || '')),
      el('text', { x: W - padR, y: H - 5, textAnchor: 'end', fontFamily: EMONO, fontSize: 10, fill: 'var(--ink-3)' }, esc(xs[xs.length - 1] || ''))];
    return el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }
  function etfPanelPerf() {
    var r = D.returns || {}, hasBench = D.relSeries && (D.relSeries.x || []).length > 1;
    var retItems = [['연초대비(YTD)', r.ytd], ['3년(연평균)', r.y3], ['5년(연평균)', r.y5]];
    var retHtml = retItems.map(function (it) {
      var v = it[1], col = v == null ? 'var(--ink-3)' : v >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)';
      return '<div style="flex:1;min-width:120px;border:1px solid var(--line);border-radius:var(--radius-md);padding:14px 16px"><div class="kick">' + it[0] + '</div><div class="mono" style="font-size:22px;font-weight:500;margin-top:6px;color:' + col + '">' + (v == null ? '—' : fmtSigned(v)) + '</div></div>';
    }).join('');
    return etfSectionHead('01', hasBench ? '벤치마크 대비 누적 성과' : '최근 5년 추이', (hasBench ? '시작점을 100으로 맞춰 ' + esc(D.metrics.bench_label || '벤치마크') + '와 겹쳐 봅니다. 위에 있을수록 벤치마크보다 잘했다는 뜻입니다.' : '최근 5년 종가 흐름입니다.'), null) +
      '<div class="analysis-section-body">' + etfRelChart() +
        // 한 화면에 두 계열이 산다 — 종가 라인은 총수익, 배당 밴드·NAV 괴리는 실거래가.
        // 어느 쪽으로 통일해도 한쪽이 틀리므로 통일하는 대신 기준을 밝힌다(#57).
        (D.priceBasisNote
          ? '<div style="margin-top:10px;font-size:11.5px;color:var(--ink-3);line-height:1.65">기준 · ' + esc(D.priceBasisNote) + '</div>'
          : '') + '</div>' +
      etfSectionHead('02', '기간별 수익률', '야후 파이낸스 기준 총수익률(분배금 포함). 과거 실적이며 미래를 보장하지 않습니다.', null) +
      '<div class="analysis-section-body"><div style="display:flex;flex-wrap:wrap;gap:12px">' + retHtml + '</div></div>';
  }
  // ④ 비용·추적 — 총보수·추적오차·순자산 + 분배금 이력
  function etfDistChart() {
    var ds = D.distributions || []; if (!ds.length) return '<div style="color:var(--ink-3);font-size:12.5px">분배금(배당) 이력이 없습니다.</div>';
    var maxA = Math.max.apply(null, ds.map(function (d) { return d.amount || 0; })) || 1;
    return '<div style="display:flex;align-items:flex-end;gap:16px;height:130px;padding-top:8px">' + ds.map(function (d) {
      return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;justify-content:flex-end;height:100%">' +
        '<span class="mono" style="font-size:10.5px;color:var(--ink-2)">' + fmtPrice(d.amount) + '</span>' +
        '<span style="width:66%;background:var(--dv-green);border-radius:var(--radius-sm) var(--radius-sm) 0 0;height:' + (d.amount / maxA * 92).toFixed(0) + 'px"></span>' +
        '<span class="mono" style="font-size:10.5px;color:var(--ink-3)">' + d.year + '</span></div>';
    }).join('') + '</div>';
  }
  function etfPanelCost() {
    var mt = D.metrics || {}, fi = D.fundInfo || {};
    var teTip = D.trackingErrorNote || '';
    // 추적오차는 같은 이름이라도 출처에 따라 뜻이 다르다 — 한국은 운용사가 낸 기초지수 복제오차,
    // 미국은 우리가 대용 벤치마크로 낸 추정치. 부제목도 그에 맞춰 갈라 준다.
    // 이름까지 갈라 준다 — 미국의 추정값은 8~11%도 흔해서, 한국 공시값(0.4% 수준)과
    // 같은 '추적오차'로 나란히 놓이면 미국 ETF가 형편없이 운용되는 것처럼 읽힌다.
    var pub = !!fi.base_index && D.trackingError != null;
    var teName = pub ? '추적오차' : '벤치마크 대비 변동성';
    var teSub = pub ? '기초지수 복제 정확도 (운용사 공시)' : '비교 벤치마크와의 일간 수익률 차이 (추정)';
    var rows = [
      // 국내 ETF 보수 인하 경쟁으로 0.0068% 같은 값이 흔하다 — 2자리로 자르면 0.01%로 뭉개져
      // 비교가 안 되므로, 아주 낮은 구간에서만 소수 자리를 늘린다.
      ['총보수(연)', mt.expense_ratio != null ? fmtPct(mt.expense_ratio, mt.expense_ratio < 0.0005 ? 4 : 2) : na('운용사 총보수(expense ratio) 데이터를 무료 소스에서 받지 못했습니다.'), '펀드 운용 수수료'],
      [teName, D.trackingError != null ? '<span class="na" tabindex="0" data-tip="' + esc(teTip) + '">' + fmtPct(D.trackingError, 2) + ' ⓘ</span>' : na('벤치마크가 없어 계산하지 못했습니다.'), teSub],
      ['순자산(AUM)', mt.aum != null ? fmtMoney(mt.aum) : na('운용사 순자산(AUM) 공시를 무료 소스에서 받지 못했습니다 — 펀드 규모는 판단에서 빼세요.'), '펀드 규모'],
      ['비교 벤치마크', esc(mt.bench_label || '—'), '상대 비교 기준']];
    // 아래 네 줄은 한국(네이버)에서만 오는 값 — 미국은 없으므로 행 자체를 만들지 않는다.
    if (fi.base_index) rows.push(['기초지수', esc(fi.base_index), '이 ETF가 따라가기로 한 지수']);
    if (fi.issuer) rows.push(['운용사', esc(fi.issuer), '펀드를 만들고 운용하는 회사']);
    if (fi.listed_date && fi.listed_date.length === 8) {
      rows.push(['상장일', fi.listed_date.slice(0, 4) + '.' + fi.listed_date.slice(4, 6) + '.' + fi.listed_date.slice(6, 8), '거래가 시작된 날']);
    }
    var rowHtml = rows.map(function (r) {
      return '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:16px;padding:12px 0;border-bottom:1px solid var(--line)"><div><div style="font-size:13.5px;color:var(--ink);font-weight:600">' + r[0] + '</div><div style="font-size:11.5px;color:var(--ink-3);margin-top:2px">' + r[2] + '</div></div><div class="mono" style="font-size:16px;text-align:right">' + r[1] + '</div></div>';
    }).join('');
    return '<div style="display:flex;flex-wrap:wrap;gap:34px">' +
      '<div style="flex:1;min-width:300px">' + etfSectionHead('01', '비용·추적 품질', '수수료가 낮고 벤치마크를 잘 따라갈수록 좋습니다.', null) +
        '<div class="analysis-section-body">' + rowHtml + '</div></div>' +
      '<div style="flex:1;min-width:300px">' + etfSectionHead('02', '분배금(배당) 이력', '연도별 주당 분배금입니다. 꾸준할수록 인컴 관점에서 안정적입니다.', '연간') +
        '<div class="analysis-section-body">' + etfDistChart() + '</div></div></div>';
  }
  // ⑤ 뉴스 — Google News 헤드라인
  function etfPanelNews() {
    var news = D.news || [];
    if (!news.length) return etfSectionHead('01', '관련 뉴스', null, null) + '<div class="analysis-section-body"><span style="color:var(--ink-3)">최근 관련 뉴스를 가져오지 못했습니다.</span></div>';
    var list = news.map(function (n) {
      var cat = n.category ? '<span style="flex:none;font-size:10.5px;color:var(--dv-navy);border:1px solid var(--line);border-radius:var(--radius-pill);padding:2px 8px">' + esc(n.category) + '</span>' : '';
      return '<a href="' + esc(n.link || '#') + '" target="_blank" rel="noopener" style="display:flex;gap:12px;align-items:baseline;padding:11px 0;border-bottom:1px solid var(--line);color:inherit;text-decoration:none">' +
        cat + '<span style="flex:1;min-width:0"><span style="font-size:13.5px;color:var(--ink);line-height:1.5">' + esc(n.title) + '</span>' +
        '<div style="font-size:11px;color:var(--ink-3);margin-top:3px">' + esc(n.source || '') + (n.date ? ' · ' + esc(n.date) : '') + '</div></span></a>';
    }).join('');
    return etfSectionHead('01', '관련 뉴스', 'Google News 헤드라인입니다. 클릭하면 새 탭에서 원문이 열립니다.', news.length + '건') +
      '<div class="analysis-section-body">' + list + '</div>';
  }
  function renderEtf() {
    CUR = D.currency || 'USD';
    document.title = D.name + ' — 투자지표';
    setEtfMode(true);
    $('warnWrap').innerHTML = '';
    renderEtfHeader();
    renderEtfTiles();
    var tabs = [['verdict', '판정'], ['holdings', '구성·보유'], ['perf', '성과·추이'], ['cost', '비용·추적'], ['news', '뉴스']];
    var builders = { verdict: etfPanelVerdict, holdings: etfPanelHoldings, perf: etfPanelPerf, cost: etfPanelCost, news: etfPanelNews };
    var bar = '<div class="tabbar" id="etfTabBar">' + tabs.map(function (t, i) {
      return '<button class="tabbtn' + (i === 0 ? ' on' : '') + '" data-etab="' + t[0] + '"><span class="tn">0' + (i + 1) + '</span><span class="tl">' + t[1] + '</span></button>';
    }).join('') + '</div>';
    var panelHtml = tabs.map(function (t, i) {
      return '<div class="etf-panel" data-etab="' + t[0] + '" style="' + (i === 0 ? '' : 'display:none;') + 'padding-top:22px">' + builders[t[0]]() + '</div>';
    }).join('');
    var disc = '<div style="margin-top:22px;border-top:1px solid var(--line);padding-top:14px;display:flex;align-items:center;gap:8px;color:var(--ink-3)">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>' +
      '<span style="font-size:11.5px">학습·분석 보조 도구이며 투자 자문이 아닙니다. 무료 공개 데이터의 결측·지연을 포함할 수 있습니다. 출처 · ' + esc((D.sources || []).join(' · ')) + '</span></div>';
    $('etfView').innerHTML = bar + panelHtml + disc;
    var etb = $('etfTabBar');
    etb.addEventListener('click', function (e) {
      var b = e.target.closest('.tabbtn'); if (!b) return;
      var k = b.getAttribute('data-etab');
      etb.querySelectorAll('.tabbtn').forEach(function (x) { x.classList.toggle('on', x.getAttribute('data-etab') === k); });
      $('etfView').querySelectorAll('.etf-panel').forEach(function (p) { p.style.display = p.getAttribute('data-etab') === k ? '' : 'none'; });
    });
  }

  function renderAll() {
    setEtfMode(false);
    CUR = D.meta.currency;
    document.title = D.meta.name + ' — 투자지표';
    var sources = D.meta.sources || {};
    var sourceLines = Object.keys(sources).map(function (k) {
      return '<b style="color:var(--ink-2)">' + esc(k) + '</b> · ' + esc(sources[k]);
    });
    $('finSource').innerHTML = sourceLines.length ? sourceLines.join('<br/>') : esc(D.meta.fin_source || '출처 정보 없음');
    renderHeader(); renderTiles(); renderWarnings();
    renderSummary(); renderPriceTab(); renderValuation(); renderCompany();
    renderFinancials(); renderPeers(); renderWacc(); renderBacktest(); renderAi();
    renderExamples();
  }

  /* ══════════ 데이터 로드 ══════════ */
  function setStatus(on, msg, isErr) {
    var s = $('status'); s.classList.toggle('on', on);
    if (msg) $('statusMsg').innerHTML = (isErr ? '<span style="color:var(--danger)">⚠ ' + esc(msg) + '</span><div style="font-size:12px;color:var(--ink-3);margin-top:8px">종목을 바꿔 다시 시도하세요. (클릭하면 닫힘)</div>' : esc(msg));
    s.querySelector('.spin').style.display = isErr ? 'none' : 'block';
  }
  var _reqSeq = 0;
  var _progT = null;
  function stopProgress() { if (_progT) { clearInterval(_progT); _progT = null; } }
  function startProgress(seq) {
    // 서버의 진행 상태(피어 수집 n/m)를 폴링해 대기 체감을 줄인다 — 실측값이라 정직하다
    stopProgress();
    var pu = 'api/progress?market=' + encodeURIComponent(state.market) + '&query=' + encodeURIComponent(state.query);
    _progT = setInterval(function () {
      if (seq !== _reqSeq) { stopProgress(); return; }
      fetch(pu).then(function (r) { return r.json(); }).then(function (p) {
        if (seq !== _reqSeq || !p || !p.total) return;
        $('statusMsg').textContent = "'" + state.query + "' " + p.stage + ' ' + p.done + '/' + p.total + '…';
      }).catch(function () {});
    }, 700);
  }
  function load() {
    var seq = ++_reqSeq;
    // ETF는 재무제표·피어가 없어 별도 엔드포인트(3축 분석)로 — 주식 9탭 대신 ETF 뷰를 그린다
    if (state.kind === 'etf') {
      setStatus(true, "'" + state.query + "' ETF 분석 중…");
      fetch('api/analyze_etf?market=' + encodeURIComponent(state.market) + '&query=' + encodeURIComponent(state.query))
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (seq !== _reqSeq) return;
          if (!res.ok || res.j.error) { setStatus(true, res.j.error || 'ETF 분석에 실패했습니다.', true); return; }
          D = res.j; state.hover = null; renderEtf(); setStatus(false);
        })
        .catch(function (e) { if (seq !== _reqSeq) return; setStatus(true, '서버에 연결하지 못했습니다: ' + e.message, true); });
      return;
    }
    // 종목·시장이 바뀌면 피어 편집은 초기화(편집은 종목별 상태)
    var ek = state.market + ':' + state.query;
    if (state._editKey !== ek) { state._editKey = ek; state.peerEx = []; state.peerAdd = []; }
    setStatus(true, "'" + state.query + "' 데이터 수집 중… (첫 조회는 피어 지표를 병렬로 모으느라 10초 안팎 걸릴 수 있어요)");
    var url = 'api/analyze?market=' + encodeURIComponent(state.market) + '&query=' + encodeURIComponent(state.query) + '&peer_count=' + (state.peer_count || 9);
    if (state.peerEx.length) url += '&exclude=' + encodeURIComponent(state.peerEx.join(','));
    if (state.peerAdd.length) url += '&add=' + encodeURIComponent(state.peerAdd.join(','));
    startProgress(seq);
    fetch(url).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (seq !== _reqSeq) return; // 최신 요청만 반영
        stopProgress();
        // kind 없이 들어온 링크가 사실 ETF였다면(서버가 kind:'etf'로 알려 줌) 오류 대신 ETF 분석으로.
        if (!res.ok && res.j && res.j.kind === 'etf') { state.kind = 'etf'; load(); return; }
        if (!res.ok || res.j.error) { setStatus(true, res.j.error || '분석에 실패했습니다.', true); return; }
        D = res.j; state.hover = null; renderAll(); setStatus(false);
      })
      .catch(function (e) { if (seq !== _reqSeq) return; stopProgress(); setStatus(true, '서버에 연결하지 못했습니다: ' + e.message, true); });
  }

  /* ══════════ 인터랙션 ══════════ */
  function wireSeg(id, onChange) {
    var seg = $(id); if (!seg) return;
    seg.querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', x.classList.contains('on') ? 'true' : 'false'); });
    seg.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      seg.querySelectorAll('button').forEach(function (x) { x.classList.remove('on'); x.setAttribute('aria-pressed', 'false'); });
      b.classList.add('on'); b.setAttribute('aria-pressed', 'true'); onChange(b.getAttribute('data-val'));
    });
  }
  function wireCollapse(btnId, bodyId, disp) { var btn = $(btnId), body = $(bodyId); if (!btn || !body) return; btn.addEventListener('click', function () { var open = body.style.display !== 'none' && body.style.display !== ''; body.style.display = open ? 'none' : (disp || 'block'); var ch = btn.querySelector('.chev'); if (ch) ch.classList.toggle('open', !open); }); }
  function renderExamples() {
    $('examples').innerHTML = EXAMPLES[state.market].map(function (e) { var on = e[1] === state.query; return '<span data-code="' + e[1] + '" style="font-size:12px;cursor:pointer;border-radius:var(--radius-sm);padding:4px 9px;' + (on ? 'color:var(--ink);font-weight:600;border:1px solid var(--ink)' : 'color:var(--ink-2);border:1px solid var(--line)') + '">' + esc(e[0]) + '</span>'; }).join('');
  }

  function switchTab(tab) {
    var bar = $('tabBar');
    bar.querySelectorAll('.tabbtn').forEach(function (x) { x.classList.toggle('on', x.getAttribute('data-tab') === tab); });
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.toggle('on', p.getAttribute('data-tab') === tab); });
    state.hover = null;
    if (tab === 'price' && D) renderPrice();
  }

  function init() {
    wireSeg('marketSeg', function (v) { state.market = v; renderExamples(); });
    // 탭
    var bar = $('tabBar');
    bar.addEventListener('click', function (e) { var b = e.target.closest('.tabbtn'); if (!b) return; switchTab(b.getAttribute('data-tab')); });
    // 방법별 표의 ①②③ 방법명 → 해당 재료 탭으로 이동
    $('methodsTable').addEventListener('click', function (e) { var g = e.target.closest('.methods-goto'); if (!g) return; switchTab(g.getAttribute('data-goto')); $('tabBar').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    // 종목 입력
    // Enter 직접 제출은 submitQuery가 ETF 여부를 먼저 확인하고 알맞은 분석으로 보낸다.
    $('tickerForm').addEventListener('submit', function (e) { e.preventDefault(); var q = $('tickerInput').value.trim().split(/\s+/)[0]; if (q) submitQuery(q); });
    $('navSearch').addEventListener('submit', function (e) { e.preventDefault(); var q = $('navSearchInput').value.trim().split(/\s+/)[0]; if (q) submitQuery(q); });
    // 자동완성 — 헤더 검색창과 사이드바 티커 입력 둘 다. ETF면 3축 ETF 분석, 주식이면 9탭 분석.
    attachAutocomplete($('navSearchInput'), function (it) { $('navSearchInput').value = ''; pickTicker(it); });
    attachAutocomplete($('tickerInput'), function (it) { pickTicker(it); });
    // 예시 칩은 모두 기업 종목이라 kind를 주식으로 되돌린다(ETF 보다가 눌러도 9탭으로).
    $('examples').addEventListener('click', function (e) { var s = e.target.closest('[data-code]'); if (!s) return; state.kind = 'stock'; state.query = s.getAttribute('data-code'); $('tickerInput').value = state.query; load(); });
    // 주가 컨트롤
    wireSeg('priceModeSeg', function (v) { state.priceMode = v; state.hover = null; var showAbs = v === 'abs'; $('maToggles').style.display = showAbs ? 'inline-flex' : 'none'; var cts = $('chartTypeSeg'); if (cts) cts.style.display = showAbs ? '' : 'none'; if (D) renderPrice(); });
    wireSeg('chartTypeSeg', function (v) { state.chartType = v; state.hover = null; if (D) renderPrice(); });
    wireSeg('periodSeg', function (v) { state.pricePeriod = v; state.hover = null; if (D) renderPrice(); });
    $('priceReset').addEventListener('click', function () { if (priceChartInst && priceChartInst.reset) priceChartInst.reset(); });
    document.querySelectorAll('#maToggles .ma-btn').forEach(function (btn) { btn.setAttribute('aria-pressed', btn.classList.contains('on') ? 'true' : 'false'); btn.addEventListener('click', function () { var k = btn.getAttribute('data-ma'); state.ma[k] = !state.ma[k]; btn.classList.toggle('on', state.ma[k]); btn.setAttribute('aria-pressed', state.ma[k] ? 'true' : 'false'); var col = { m20: 'var(--dv-gold)', m60: 'var(--dv-slate)', m120: 'var(--dv-plum)' }[k]; btn.style.borderColor = state.ma[k] ? col : 'var(--line-strong)'; btn.style.color = state.ma[k] ? 'var(--ink)' : 'var(--ink-3)'; btn.querySelector('.dash').style.background = state.ma[k] ? col : 'var(--line-strong)'; if (D) renderPrice(); }); });
    wireSeg('bandSeg', function (v) { state.bandMetric = v; if (D) renderBand(); });
    // 접이식·사이드바
    wireCollapse('assumeToggle', 'assumeBody', 'flex');
    wireCollapse('finTableToggle', 'finTableBody', 'block');
    var wrap = $('sidebarWrap'), tgl = $('sidebarToggle'), chev = $('sidebarChev');
    tgl.addEventListener('click', function () { var c = wrap.classList.toggle('collapsed'); tgl.style.left = (c ? 0 : 279) - 13 + 'px'; chev.style.transform = c ? 'rotate(180deg)' : 'none'; tgl.title = c ? '사이드바 펼치기' : '사이드바 접기'; });
    var peer = $('peerSlider'); if (peer) { peer.addEventListener('input', function () { $('peerCountVal').textContent = peer.value; }); peer.addEventListener('change', function () { state.peer_count = +peer.value; load(); }); }
    $('status').addEventListener('click', function () { $('status').classList.remove('on'); });

    // 창 크기 변경 시 주가 차트(캔버스)만 다시 — 활성 탭일 때.
    // 적정가 차트도 같이: 폰↔데스크톱 배치가 갈리고(≤560px) 좁은 배치는 컨테이너 실폭을
    // viewBox로 쓰므로, 회전·크기 변경 뒤에는 다시 그려야 크기가 맞는다.
    var rzT; window.addEventListener('resize', function () { clearTimeout(rzT); rzT = setTimeout(function () { var p = $('panel-price'); if (D && p && p.classList.contains('on')) renderPrice(); var s = $('panel-summary'); if (D && s && s.classList.contains('on')) $('bulletChart').innerHTML = bulletChart(); }, 180); });
    // 딥링크: ?q=&market= (홈 예시카드·교차검색 착지)
    try {
      var sp = new URLSearchParams(location.search);
      var mk = (sp.get('market') || '').toUpperCase();
      if (mk === 'KR' || mk === 'US') { state.market = mk; var seg = $('marketSeg'); if (seg) seg.querySelectorAll('button').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-val') === mk); }); }
      var qq = (sp.get('q') || sp.get('query') || '').trim();
      if (qq) { state.query = qq; var ti = $('tickerInput'); if (ti) ti.value = qq; }
      if ((sp.get('kind') || '').toLowerCase() === 'etf') state.kind = 'etf';
    } catch (e) {}

    load();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
