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
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]; }); }
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
  function compactWon(v) { if (v == null) return '—'; return CUR === 'KRW' ? Math.round(v / 1000).toLocaleString('en-US') + '천' : '$' + Math.round(v).toLocaleString('en-US'); }

  var VERDICTS = ['크게 저평가', '저평가', '적정 수준', '고평가', '크게 고평가'];
  function vIdx(v) { var i = VERDICTS.indexOf(v); return i < 0 ? 2 : i; }
  function vPos(v) { return [12, 31, 50, 69, 88][vIdx(v)]; }
  function vTone(v) { var i = vIdx(v); return i <= 1 ? 'positive' : i === 2 ? 'neutral' : 'negative'; }

  /* ── 상태 ── */
  var state = { market: 'KR', query: '035420', pricePeriod: '1Y', priceMode: 'abs', ma: { m20: true, m60: true, m120: false }, hover: null, bandMetric: 'PER', scnBear: -0.15, scnBull: 0.15, scnMult: 0 };
  var D = null;
  var EXAMPLES = { KR: [['삼성전자', '005930'], ['현대차', '005380'], ['NAVER', '035420'], ['KB금융', '105560']], US: [['Apple', 'AAPL'], ['Microsoft', 'MSFT'], ['Coca-Cola', 'KO'], ['Rivian', 'RIVN']] };

  /* ══════════ 차트 (데이터 구동) ══════════ */

  function bulletChart() {
    var est = D.verdict.estimates || [];
    var cur = D.meta.price, avg = D.verdict.fair_mid;
    var vals = [cur]; est.forEach(function (e) { if (e.low != null) vals.push(e.low); if (e.high != null) vals.push(e.high); if (e.mid != null) vals.push(e.mid); });
    if (avg != null) vals.push(avg);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), sp = (hi - lo) || hi * 0.2 || 1;
    var dmin = lo - sp * 0.18, dmax = hi + sp * 0.12;
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
          el('div', { style: { position: 'relative', height: '30px', background: 'var(--paper-3)', border: '1px solid var(--line-strong)', borderRadius: '5px', overflow: 'hidden' } },
            // 의미색(강점=green·약점=clay)은 유지하되 종이 쪽으로 68% 톤다운 — 판정 막대보다 조용하게.
            el('div', { style: { position: 'absolute', top: 0, bottom: 0, left: 0, width: w + '%', background: good ? 'color-mix(in srgb, var(--dv-green) 68%, var(--paper))' : 'color-mix(in srgb, var(--dv-clay) 68%, var(--paper))' } }),
            el('div', { style: { position: 'absolute', left: '50%', top: 0, bottom: 0, width: '2px', background: 'var(--ink)', opacity: 0.5 } }),
            el('span', { style: { position: 'absolute', left: '50%', top: '2px', transform: 'translateX(-50%)', fontSize: '8px', color: 'var(--ink-3)', fontFamily: 'var(--font-sans)' } }, '50'),
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
    function fmtCompactVolume(v) { if (v == null || !isFinite(v)) return '—'; var a = Math.abs(v); return a >= 1e9 ? (v / 1e9).toFixed(1) + 'B' : a >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : a >= 1e3 ? (v / 1e3).toFixed(0) + 'K' : Math.round(v).toLocaleString('en-US'); }
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
        if (state.ma.m120) strokeArr(ma120, COL.plum, 1.3);
        if (state.ma.m60) strokeArr(ma60, COL.slate, 1.3);
        if (state.ma.m20) strokeArr(ma20, COL.gold, 1.3);
        strokeArr(close, COL.ink, 1.9);
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
    if (b && b.percentile != null) { var p = b.percentile; cap.innerHTML = '밴드는 5년 배수 분포의 10–90분위를 펀더멘털(EPS/BPS)에 곱한 가격대. 주가(네이비 선)가 위쪽 선에 가까울수록 역사적으로 비쌈. 현재 배수는 5년 분포 <b style="color:var(--ink-2)">하위 ' + p.toFixed(0) + '%</b> — ' + (p < 35 ? '역사적으로도 저평가 구간.' : p > 65 ? '역사적으로 비싼 구간.' : '중간 구간.'); }
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

  function peerScatter() {
    var pts = (D.peers && D.peers.scatter) || [];
    if (pts.length < 2) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px' } }, '피어 표본이 부족합니다.');
    var perMax = Math.max.apply(null, pts.map(function (p) { return p.per; })) * 1.10;
    var roeMax = Math.max(1, Math.max.apply(null, pts.map(function (p) { return p.roe; }))) * 1.14;
    var roeMin = Math.min(0, Math.min.apply(null, pts.map(function (p) { return p.roe; })));
    var W = 580, H = 372, padL = 52, padR = 18, top = 18, plotH = H - 54, xw = W - padL - padR;
    var X = function (v) { return padL + v / perMax * xw; }, Y = function (v) { return top + (1 - (v - roeMin) / (roeMax - roeMin)) * plotH; };
    var medPer = pts.map(function (p) { return p.per; }).sort(function (a, b) { return a - b; })[Math.floor(pts.length / 2)];
    var medRoe = pts.map(function (p) { return p.roe; }).sort(function (a, b) { return a - b; })[Math.floor(pts.length / 2)];
    var els = [];
    els.push(el('rect', { x: padL, y: top, width: X(medPer) - padL, height: Y(medRoe) - top, fill: 'var(--dv-green)', fillOpacity: 0.06 }));
    els.push(el('text', { x: padL + 9, y: top + 18, fontSize: 12.5, fill: 'var(--dv-green)', fontFamily: 'var(--font-sans)', fontWeight: 600 }, '저PER · 고ROE (매력)'));
    for (var g = 0; g <= 3; g++) { var yy = top + g / 3 * plotH; els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL - 8, y: yy + 4, fontSize: 12, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'end' }, (roeMax - (roeMax - roeMin) * g / 3).toFixed(0))); }
    for (var t = 0; t <= 4; t++) { var pv = perMax * t / 4; els.push(el('text', { x: X(pv), y: top + plotH + 20, fontSize: 12, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, pv.toFixed(0) + '×')); }
    // 점 = 클릭 가능한 그룹(data-q 검색키 · data-key 매칭키). 넓은 투명 히트원으로 클릭/hover 쉬움.
    pts.forEach(function (p) {
      var cx = X(Math.min(p.per, perMax)), cy = Y(Math.max(roeMin, Math.min(p.roe, roeMax)));
      var kids = [
        el('circle', { cx: cx, cy: cy, r: 15, fill: 'transparent', className: 'hit' }),
        el('circle', { cx: cx, cy: cy, r: p.self ? 8 : 6, fill: p.self ? 'var(--dv-navy)' : 'var(--paper)', stroke: p.self ? 'var(--dv-navy)' : 'var(--ink-3)', strokeWidth: 1.8, className: 'dot' }),
        el('text', { x: cx, y: cy - 12, fontSize: p.self ? 13 : 12, fontWeight: p.self ? 700 : 500, fill: p.self ? 'var(--ink)' : 'var(--ink-2)', fontFamily: 'var(--font-sans)', textAnchor: 'middle', className: 'lbl' }, esc(p.n))
      ];
      els.push(el('g', { className: 'pt', 'data-q': p.q || '', 'data-key': p.key || '', style: { cursor: 'pointer' } }, kids));
    });
    els.push(el('text', { x: padL + xw, y: top + plotH + 38, fontSize: 12, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, '→ PER (배)'));
    els.push(el('text', { x: padL - 40, y: top + 10, fontSize: 12, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)' }, 'ROE %'));
    return el('svg', { viewBox: '0 0 ' + W + ' ' + (H + 8), style: { width: '100%', height: 'auto', display: 'block' } }, els);
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
    // ── B (기본) ──
    $('hv-B').innerHTML =
      '<div style="border:1px solid var(--line);border-radius:var(--radius-md);padding:22px 24px;display:flex;gap:32px;align-items:center;flex-wrap:wrap">' +
        '<div style="min-width:210px"><div style="display:flex;align-items:center;gap:11px">' +
          '<span style="width:38px;height:38px;flex:none;border-radius:var(--radius-sm);background:var(--ink);color:var(--paper);display:inline-flex;align-items:center;justify-content:center;font-family:' + disp + ';font-weight:900;font-size:18px">' + esc(initial) + '</span>' +
          '<div><div style="display:flex;align-items:center;gap:7px"><span style="font-family:' + mono + ';font-size:12px;color:var(--ink-3)">' + esc(m.ticker) + '</span>' + badge(m.market + ' · ' + m.benchmark, 'info') + '</div>' +
          '<div style="font-family:' + disp + ';font-weight:700;font-size:29px;letter-spacing:-0.01em;line-height:1;margin-top:3px">' + esc(m.name) + '</div></div></div>' +
          '<div style="font-family:' + mono + ';font-size:29px;font-weight:500;margin-top:14px">' + fmtPrice(m.price) + '</div></div>' +
        '<div style="flex:1;min-width:320px"><div style="display:flex;justify-content:space-between;align-items:baseline"><span class="kick">밸류에이션 판정</span><span style="font-size:12px;color:var(--ink-3)">신뢰도 <b style="color:var(--ink-2)">' + esc(v.confidence || '—') + '</b></span></div>' +
          '<div style="position:relative;margin-top:14px"><div style="display:flex;height:11px;border-radius:var(--radius-pill);overflow:hidden">' +
            '<span style="flex:1;background:var(--dv-green);opacity:.82"></span><span style="flex:1;background:var(--dv-green);opacity:.45"></span><span style="flex:1;background:var(--paper-3)"></span><span style="flex:1;background:var(--dv-clay);opacity:.45"></span><span style="flex:1;background:var(--dv-clay);opacity:.82"></span></div>' +
            '<div style="position:absolute;top:-5px;left:' + pos + '%;transform:translateX(-50%);width:2px;height:21px;background:var(--ink)"></div>' +
            '<div style="position:absolute;top:-13px;left:' + pos + '%;transform:translateX(-50%);width:9px;height:9px;border-radius:50%;background:var(--ink);border:2px solid var(--paper)"></div>' +
            '<div style="display:flex;margin-top:9px;font-size:10.5px;color:var(--ink-3);text-align:center">' + VERDICTS.map(function (t, i) { return '<span style="flex:1' + (t === v.verdict ? ';color:' + (tone === 'negative' ? 'var(--dv-negative)' : 'var(--dv-positive)') + ';font-weight:600' : '') + '">' + t.replace(' ', '<br>') + '</span>'; }).join('') + '</div></div>' +
          '<div style="margin-top:12px;font-size:13px;color:var(--ink-2)">괴리율 <b style="font-family:' + mono + ';color:' + gapCol + '">' + fmtSigned(v.gap) + '</b> · 4방법 가중 적정가 <b style="font-family:' + mono + ';color:var(--ink)">' + fmtPrice(v.fair_mid) + '</b></div></div>' +
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
    var t = D.tiles;
    var items = [['시가총액', fmtMoney(t.market_cap)], ['PER (TTM)', fmtX(t.per)], ['PBR', fmtX(t.pbr)], ['ROE (TTM)', fmtPct(t.roe)], ['베타 (β)', t.beta != null ? t.beta.toFixed(2) : '—'], ['WACC', t.wacc != null ? fmtPct(t.wacc) : 'N/A']];
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
        if (wgt != null) nameCell += ' <span class="mono" style="font-size:10.5px;color:var(--ink-3)">가중 ' + Math.round(wgt * 100) + '%</span>';
        return '<div class="row" style="grid-template-columns:1.6fr 1.2fr 0.9fr 1.5fr">' + nameCell + '<span class="mono r" style="font-size:13.5px;color:var(--ink-2)">' + won(e.low) + '–' + won(e.high) + '</span><span class="mono r" style="font-size:13.5px">' + won(e.mid) + '</span><span style="font-size:12px;color:var(--ink-3)">' + esc(e.note) + '</span></div>';
      }
      if (skipMap[name] != null) {
        // 건너뛴 방법도 번호 자리를 유지해 ①~④가 항상 순서대로 보이게 한다
        return '<div class="row" style="grid-template-columns:1.6fr 1.2fr 0.9fr 1.5fr;opacity:.55"><span style="font-size:13px;color:var(--ink-3)">' + (mt ? '<span class="methods-mno">' + mt[0] + '</span>' : '') + esc(name) + '</span><span class="mono r" style="font-size:12.5px;color:var(--ink-3)">—</span><span class="r" style="font-size:12px;color:var(--ink-3)">건너뜀</span><span style="font-size:12px;color:var(--ink-3)">' + esc(skipMap[name]) + '</span></div>';
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
    $('methodsTable').innerHTML = est.length ? head + rows + total + sens + formula : '<div style="color:var(--ink-3);font-size:13px;padding:16px 0">적정주가를 계산할 방법이 없습니다(데이터 부족).</div>';
    // 점수
    $('scoreOverall').textContent = D.scores.overall != null ? Math.round(D.scores.overall) : '—';
    $('radarChart').innerHTML = radarChart();
    $('scoreBars').innerHTML = scoreBars();
    // 금융업이면 두 축이 '—'로 비는 이유를 한 줄로 밝힌다(05·07 탭 안내와 톤 통일).
    $('scoreFinNote').innerHTML = (D.meta && D.meta.is_financial)
      ? '<div style="font-size:11.5px;color:var(--ink-3);line-height:1.65;margin-top:18px;padding-top:12px;border-top:1px dashed var(--line)">금융업(은행·보험·증권)은 부채 대부분이 예금·보험부채라 일반 <b>재무 안정성·현금흐름</b> 지표(부채비율·유동비율·FCF수익률 등)가 부적합해 상대점수에서 제외합니다. 은행 건전성은 BIS 자기자본비율·고정이하여신비율 등 <b>감독당국 공시</b>로 평가해야 하며, 본 도구는 무료 공개 데이터 범위상 이를 제공하지 않습니다.</div>'
      : '';
    // 해설
    $('commentary').innerHTML = (D.commentary || []).map(function (c) {
      var m = CMT[c.kind] || CMT.info, key = c.text.indexOf('밸류트랩') >= 0 || c.text.indexOf('순수 저평가') >= 0;
      var strokeW = c.kind === 'good' ? 1.9 : 1.75;
      var icon = c.kind === 'info' ? '<circle cx="12" cy="12" r="10"/><path d="' + m[1] + '"/>' : c.kind === 'warn' ? '<path d="' + m[1] + '"/><path d="M12 9v4"/><path d="M12 17h.01"/>' : '<path d="' + m[1] + '"/>';
      return '<div class="cmt' + (key ? ' key' : '') + '"><span style="color:' + m[0] + ';flex:none;margin-top:1px"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + strokeW + '" stroke-linecap="round" stroke-linejoin="round">' + icon + '</svg></span><div style="font-size:12.5px;color:var(--ink-2);line-height:1.55">' + esc(c.text) + '</div></div>';
    }).join('') || '<div style="color:var(--ink-3);font-size:13px">해설을 생성할 수 없습니다.</div>';
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
        var tags = (it.tags || []).map(function (t) { var macro = cat === '거시'; return '<span style="font-family:var(--font-mono);font-size:10px;' + (macro ? 'color:var(--ink);border:1px solid var(--dv-navy)' : 'color:#fff;background:var(--dv-navy)') + ';border-radius:2px;padding:1px 6px;margin-left:4px">' + esc(t) + '</span>'; }).join('');
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
    $('finGrowthUnit').textContent = ('단위 · ' + unit + '원').replace('B원', 'B');
    $('finCashUnit').textContent = ('단위 · ' + unit + '원').replace('B원', 'B');
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
    else $('finCash').innerHTML = barGroups(f.years, [
      { name: '영업현금흐름', color: 'var(--dv-green)', data: f.ocf }, { name: '잉여현금흐름 FCF', color: 'var(--dv-plum)', data: f.fcf }
    ], { fmt: function (v) { return v.toFixed(0) + unit; }, H: 210 });
    // 표
    var tb = f.table, cols = '1.4fr repeat(' + tb.years.length + ',1fr)';
    var head = '<div style="display:grid;grid-template-columns:' + cols + ';gap:6px;border-top:1px solid var(--line-strong);padding:9px 0;border-bottom:1px solid var(--line)"><span style="font-size:11px;color:var(--ink-3);text-transform:uppercase;letter-spacing:0.06em">항목(' + unit + ')</span>' + tb.years.map(function (y) { return '<span style="font-size:11px;color:var(--ink-3);text-align:right">' + y + '</span>'; }).join('') + '</div>';
    var body = Object.keys(tb.rows).map(function (name, ri) {
      var isEps = name === 'EPS';
      var cells = tb.rows[name].map(function (v) { return '<span style="text-align:right">' + (v == null ? '—' : isEps ? Math.round(v).toLocaleString('en-US') : v.toFixed(2)) + '</span>'; }).join('');
      return '<div style="display:grid;grid-template-columns:' + cols + ';gap:6px;padding:9px 0;' + (ri < 3 ? 'border-bottom:1px solid var(--line);' : '') + 'font-family:var(--font-mono);font-size:12.5px"><span style="font-family:var(--font-sans)">' + name + (isEps ? '(원)' : '') + '</span>' + cells + '</div>';
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
    var body = pr.rows.map(function (p, i) {
      var mc = p.market_cap == null ? '—' : CUR === 'KRW' ? (p.market_cap / 1e12).toFixed(1) : (p.market_cap / 1e9).toFixed(1);
      var last = i === pr.rows.length - 1;
      return '<div class="row' + (p.is_self ? ' self' : '') + '" data-q="' + esc(p.q || '') + '" data-key="' + esc(p.key || '') + '" style="grid-template-columns:' + cols + ';font-family:var(--font-mono);font-size:12.5px;cursor:pointer' + (last ? ';border-bottom:none' : '') + '"><span style="font-family:var(--font-sans)' + (p.is_self ? ';font-weight:700' : '') + '">' + esc(p.name) + '</span><span class="r">' + mc + '</span><span class="r">' + (p.per != null ? p.per.toFixed(1) : '—') + '</span><span class="r">' + (p.pbr != null ? p.pbr.toFixed(2) : '—') + '</span><span class="r">' + (p.roe != null ? (p.roe * 100).toFixed(1) : '—') + '</span></div>';
    }).join('');
    $('peerTable').innerHTML = head + body;
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
      '<div style="background:var(--navy);color:#E9EDF5;border-radius:var(--radius-md);padding:26px 28px">' +
        '<div style="font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#9BA8C4">한 줄 관찰 · 규칙 기반</div>' +
        '<div style="font-family:var(--font-display);font-weight:700;font-size:28px;letter-spacing:-0.01em;margin-top:8px">' + stance + ' — ' + esc(v.verdict) + ' · 괴리율 ' + fmtSigned(v.gap) + '</div>' +
        '<div style="font-size:13px;color:#C4CDE0;margin-top:10px;line-height:1.6">대시보드 산출 사실(적정가 범위·상승여력·업종 백분위·자본비용)을 근거로 한 스탠스입니다.' + (m.ai_available ? ' 아래 버튼으로 Gemini 서술형 종합 평가를 생성할 수 있어요.' : ' 서술형 AI 평가는 Gemini 키를 설정하면 생성됩니다.') + '</div>' +
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
  function renderAll() {
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
  function load() {
    var seq = ++_reqSeq;
    setStatus(true, "'" + state.query + "' 데이터 수집 중… (첫 조회는 피어 지표를 병렬로 모으느라 10초 안팎 걸릴 수 있어요)");
    var url = 'api/analyze?market=' + encodeURIComponent(state.market) + '&query=' + encodeURIComponent(state.query) + '&peer_count=' + (state.peer_count || 9);
    fetch(url).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (seq !== _reqSeq) return; // 최신 요청만 반영
        if (!res.ok || res.j.error) { setStatus(true, res.j.error || '분석에 실패했습니다.', true); return; }
        D = res.j; state.hover = null; renderAll(); setStatus(false);
      })
      .catch(function (e) { if (seq !== _reqSeq) return; setStatus(true, '서버에 연결하지 못했습니다: ' + e.message, true); });
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
    $('tickerForm').addEventListener('submit', function (e) { e.preventDefault(); var q = $('tickerInput').value.trim().split(/\s+/)[0]; if (q) { state.query = q; load(); } });
    $('navSearch').addEventListener('submit', function (e) { e.preventDefault(); var q = $('navSearchInput').value.trim().split(/\s+/)[0]; if (q) { state.query = q; $('tickerInput').value = q; load(); } });
    $('examples').addEventListener('click', function (e) { var s = e.target.closest('[data-code]'); if (!s) return; state.query = s.getAttribute('data-code'); $('tickerInput').value = state.query; load(); });
    // 주가 컨트롤
    wireSeg('priceModeSeg', function (v) { state.priceMode = v; state.hover = null; $('maToggles').style.display = v === 'abs' ? 'inline-flex' : 'none'; if (D) renderPrice(); });
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

    // 창 크기 변경 시 주가 차트(캔버스)만 다시 — 활성 탭일 때
    var rzT; window.addEventListener('resize', function () { clearTimeout(rzT); rzT = setTimeout(function () { var p = $('panel-price'); if (D && p && p.classList.contains('on')) renderPrice(); }, 180); });
    // 딥링크: ?q=&market= (홈 예시카드·교차검색 착지)
    try {
      var sp = new URLSearchParams(location.search);
      var mk = (sp.get('market') || '').toUpperCase();
      if (mk === 'KR' || mk === 'US') { state.market = mk; var seg = $('marketSeg'); if (seg) seg.querySelectorAll('button').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-val') === mk); }); }
      var qq = (sp.get('q') || sp.get('query') || '').trim();
      if (qq) { state.query = qq; var ti = $('tickerInput'); if (ti) ti.value = qq; }
    } catch (e) {}

    load();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
