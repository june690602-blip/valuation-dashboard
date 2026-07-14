/* ══════════════════════════════════════════════════════════════════════
   투자지표 — 포트폴리오(Meridian). localStorage 바스켓 → /api/portfolio →
   σ-E(r) 평면·상관 히트맵·성과지표·세금. 주식/채권 페이지의 '담기'와 공유.
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var ATTR = { strokeWidth: 'stroke-width', strokeDasharray: 'stroke-dasharray', strokeLinecap: 'stroke-linecap', strokeLinejoin: 'stroke-linejoin', fillOpacity: 'fill-opacity', textAnchor: 'text-anchor', fontFamily: 'font-family', fontSize: 'font-size', fontWeight: 'font-weight', className: 'class' };
  function kebab(s) { return s.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); }); }
  function styleStr(o) { var s = ''; for (var k in o) s += kebab(k) + ':' + o[k] + ';'; return s; }
  function el(tag, attrs) {
    var kids = Array.prototype.slice.call(arguments, 2); attrs = attrs || {}; var style = {};
    if (attrs.style) for (var sk in attrs.style) style[sk] = attrs.style[sk];
    var s = '<' + tag;
    for (var k in attrs) { if (k === 'style' || attrs[k] == null) continue; var val = attrs[k]; if (typeof val === 'string' && val.indexOf('var(') >= 0) { style[k] = val; continue; } s += ' ' + (ATTR[k] || k) + '="' + String(val).replace(/"/g, '&quot;') + '"'; }
    var st = styleStr(style); if (st) s += ' style="' + st + '"'; s += '>';
    for (var i = 0; i < kids.length; i++) { var c = kids[i]; if (c == null || c === false) continue; s += Array.isArray(c) ? c.join('') : c; }
    return s + '</' + tag + '>';
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]; }); }
  function $(id) { return document.getElementById(id); }
  function pctS(v) { return v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; }
  function pct(v, d) { return v == null ? '—' : (v * 100).toFixed(d == null ? 1 : d) + '%'; }

  /* ── 상태 · localStorage ── */
  var PRESETS = [
    ['KODEX 국고채3년', '114260.KS', '국내ETF', 'KRW', '채권'],
    ['KOSEF 국고채10년', '148070.KS', '국내ETF', 'KRW', '채권'],
    ['iShares 미국채 7-10년 (IEF)', 'IEF', '해외ETF', 'USD', '채권'],
    ['iShares 미국채 20년+ (TLT)', 'TLT', '해외ETF', 'USD', '채권'],
    ['ACE KRX금현물', '411060.KS', '국내ETF', 'KRW', '금'],
    ['TIGER 리츠부동산인프라', '329200.KS', '국내ETF', 'KRW', '리츠(부동산 대용)'],
    ['달러 현금 (USD/KRW)', 'KRW=X', '달러현금', 'KRW', '외화']
  ];
  var DEFAULT_AMOUNT = 500;
  var state = { months: 60, bench: 'KR' };
  var PA = null, reqSeq = 0, recalcTimer = null;

  function loadBasket() { try { return JSON.parse(localStorage.getItem('invportfolio') || '{}'); } catch (e) { return {}; } }
  function saveBasket(b) { localStorage.setItem('invportfolio', JSON.stringify(b)); }
  function loadAmounts() { try { return JSON.parse(localStorage.getItem('invamounts') || '{}'); } catch (e) { return {}; } }
  function saveAmounts(a) { localStorage.setItem('invamounts', JSON.stringify(a)); }

  function tangency(erM, rf, sigM, A) { var y = (A <= 0 || sigM <= 0) ? 0 : (erM - rf) / (A * sigM * sigM); return { y: y, sigma_p: Math.abs(y) * sigM, er_p: rf + y * (erM - rf) }; }
  function diamond(cx, cy, r) { return 'M' + cx + ' ' + (cy - r) + 'L' + (cx + r) + ' ' + cy + 'L' + cx + ' ' + (cy + r) + 'L' + (cx - r) + ' ' + cy + 'Z'; }

  /* ── 차트: 상관 히트맵 ── */
  function heatmap(labels, mat) {
    var n = labels.length; if (!n) return '';
    var cell = 44, lblW = 120, top = 90, W = lblW + n * cell + 10, H = top + n * cell + 10;
    function color(v) {
      if (v == null) return 'var(--paper-3)';
      var t = Math.max(-1, Math.min(1, v));
      if (t >= 0) { var a = t; return 'rgba(181,100,60,' + (0.12 + a * 0.7).toFixed(2) + ')'; }   // + → clay
      return 'rgba(43,74,130,' + (0.12 + (-t) * 0.7).toFixed(2) + ')';                              // − → navy
    }
    var els = [];
    labels.forEach(function (lb, j) { els.push(el('text', { x: lblW + j * cell + cell / 2, y: top - 8, fontSize: 10.5, fill: 'var(--ink-2)', fontFamily: 'var(--font-sans)', textAnchor: 'start', transform: 'rotate(-40 ' + (lblW + j * cell + cell / 2) + ' ' + (top - 8) + ')' }, esc(lb.length > 10 ? lb.slice(0, 9) + '…' : lb))); });
    labels.forEach(function (lb, i) {
      els.push(el('text', { x: lblW - 8, y: top + i * cell + cell / 2 + 4, fontSize: 11, fill: 'var(--ink-2)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, esc(lb.length > 14 ? lb.slice(0, 13) + '…' : lb)));
      mat[i].forEach(function (v, j) {
        els.push(el('rect', { x: lblW + j * cell, y: top + i * cell, width: cell - 2, height: cell - 2, fill: color(v), rx: 2 }));
        els.push(el('text', { x: lblW + j * cell + cell / 2 - 1, y: top + i * cell + cell / 2 + 4, fontSize: 10.5, fill: 'var(--ink)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, v == null ? '—' : v.toFixed(2)));
      });
    });
    return el('div', { style: { overflowX: 'auto' } }, el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: Math.min(W, 640) + 'px', maxWidth: '100%', height: 'auto', display: 'block' } }, els));
  }

  /* ── 차트: σ-E(r) 평면 ── */
  function planeChart(d) {
    var assets = d.assets || [], port = d.port, cml = d.cml || {};
    var W = 900, padL = 48, padR = 20, top = 16, plotH = 320, xw = W - padL - padR;
    var xsAll = assets.map(function (a) { return a.sigma * 100; }).concat([port.sigma * 100]);
    var sigMs = []; for (var mk in cml) if (cml[mk].sigma_m != null) sigMs.push(cml[mk].sigma_m * 100);
    if (d.optimal) xsAll.push(d.optimal.sigma * 100);
    var sigMax = Math.max(Math.max.apply(null, xsAll.concat(sigMs)) * 1.2, 5);
    var ysAll = assets.map(function (a) { return a.mu * 100; }).concat([port.er * 100, d.rf * 100]);
    for (mk in cml) if (cml[mk].er_m != null) ysAll.push(cml[mk].er_m * 100);
    if (d.optimal) ysAll.push(d.optimal.er * 100);
    var ymin = Math.min.apply(null, ysAll), ymax = Math.max.apply(null, ysAll); var pd = (ymax - ymin) * 0.15 || 2; ymin -= pd; ymax += pd;
    var X = function (s) { return padL + s / sigMax * xw; }, Y = function (v) { return top + (1 - (v - ymin) / (ymax - ymin)) * plotH; };
    var els = [];
    for (var g = 0; g <= 4; g++) { var yy = top + g / 4 * plotH, val = ymax - (ymax - ymin) * g / 4; els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL - 8, y: yy + 4, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'end' }, val.toFixed(0) + '%')); }
    for (var t = 0; t <= 4; t++) { var sv = sigMax * t / 4; els.push(el('text', { x: X(sv), y: top + plotH + 18, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, sv.toFixed(0) + '%')); }
    // CML 참고선
    var cmlCol = { KR: 'var(--dv-teal)', US: 'var(--dv-gold)' };
    for (mk in cml) { var c = cml[mk]; if (c.sigma_m == null || c.sigma_m <= 0) continue; var slope = (c.er_m - c.rf) / c.sigma_m; var y2 = (c.rf + slope * (sigMax / 100)) * 100; els.push(el('line', { x1: X(0), y1: Y(c.rf * 100), x2: X(sigMax), y2: Y(y2), stroke: cmlCol[mk] || 'var(--ink-3)', strokeWidth: 1.5, strokeDasharray: '4 3' })); els.push(el('text', { x: X(sigMax) - 4, y: Y(y2) - 5, fontSize: 10.5, fill: cmlCol[mk] || 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, 'CML ' + esc(c.label))); }
    // 자산 점 (비중 비례 크기)
    var wmax = Math.max.apply(null, assets.map(function (a) { return a.weight || 0; })) || 1;
    assets.forEach(function (a) { var r = 6 + 16 * Math.sqrt((a.weight || 0) / wmax); var cx = X(Math.min(a.sigma * 100, sigMax)), cy = Y(a.mu * 100); els.push(el('circle', { cx: cx, cy: cy, r: r, fill: 'var(--dv-navy)', fillOpacity: 0.5, stroke: 'var(--dv-navy)', strokeWidth: 1 })); els.push(el('text', { x: cx, y: cy - r - 4, fontSize: 11, fill: 'var(--ink-2)', fontFamily: 'var(--font-sans)', textAnchor: 'middle' }, esc(a.name.length > 12 ? a.name.slice(0, 11) + '…' : a.name))); });
    // 내 포트폴리오 (별 대용: 링 + 라벨)
    var px = X(Math.min(port.sigma * 100, sigMax)), py = Y(port.er * 100);
    els.push(el('circle', { cx: px, cy: py, r: 8, fill: 'var(--dv-clay)', stroke: 'var(--paper)', strokeWidth: 2 }));
    els.push(el('text', { x: px + 12, y: py + 4, fontSize: 12, fill: 'var(--dv-clay)', fontFamily: 'var(--font-sans)', fontWeight: 700, textAnchor: 'start' }, '★ 내 포트폴리오'));
    if (d.optimal) { var ox = X(Math.min(d.optimal.sigma * 100, sigMax)), oy = Y(d.optimal.er * 100); els.push(el('path', { d: diamond(ox, oy, 7), fill: 'var(--dv-plum)', stroke: 'var(--paper)', strokeWidth: 1.5 })); els.push(el('text', { x: ox + 11, y: oy + 4, fontSize: 11.5, fill: 'var(--dv-plum)', fontFamily: 'var(--font-sans)', fontWeight: 600, textAnchor: 'start' }, '◆ ' + esc(d.optimal.label))); }
    els.push(el('text', { x: padL + xw, y: H2() - 2, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, '연 변동성 σ →'));
    els.push(el('text', { x: padL - 34, y: top + 6, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)' }, 'E(r)'));
    function H2() { return top + plotH + 30; }
    return el('svg', { viewBox: '0 0 ' + W + ' ' + (top + plotH + 34), style: { width: '100%', height: 'auto', display: 'block' } }, els);
  }

  /* ── 구성 렌더 ── */
  function renderComposition() {
    var basket = loadBasket(), amounts = loadAmounts(), keys = Object.keys(basket);
    // 프리셋 셀렉트
    var avail = PRESETS.filter(function (p) { return !(p[1] in basket); });
    $('presetSel').innerHTML = avail.length ? avail.map(function (p) { return '<option value="' + p[1] + '">' + esc(p[0]) + '</option>'; }).join('') : '<option>(모두 추가됨)</option>';
    $('addPreset').disabled = !avail.length;
    $('addCash').disabled = ('CASH' in basket);

    if (!keys.length) {
      $('emptyMsg').style.display = 'block';
      $('emptyMsg').innerHTML = '아직 담은 자산이 없습니다. 위에서 <b>프리셋</b>(국채 ETF·금·리츠·달러)을 추가하거나, 📈 <b>주식 가치평가</b> 페이지에서 종목을 분석한 뒤 <b>＋ 포트폴리오에 담기</b>를, 🏦 <b>채권</b> 페이지에서 <b>이 국채를 담기</b>를 눌러 시작하세요.';
      $('composition').innerHTML = ''; $('compTotal').textContent = ''; $('analysis').style.display = 'none';
      return;
    }
    $('emptyMsg').style.display = 'none';
    var total = 0; keys.forEach(function (k) { total += (+amounts[k] || DEFAULT_AMOUNT); });
    var cols = '2.4fr 1.3fr 1.4fr 0.9fr 0.4fr';
    var head = '<div class="row head" style="grid-template-columns:' + cols + '"><span class="col-label">자산</span><span class="col-label">구분</span><span class="col-label">금액(만원)</span><span class="col-label r">비중</span><span></span></div>';
    var rows = keys.map(function (k) {
      var a = basket[k], amt = (+amounts[k] || DEFAULT_AMOUNT), w = total > 0 ? amt / total * 100 : 0;
      var rateInput = (a.type === '예금') ? '<div style="margin-top:6px"><input class="num rate" data-key="' + k + '" type="number" step="0.1" min="0.5" max="8" value="' + ((a.cash_rate || 0.03) * 100).toFixed(1) + '" style="width:90px" title="예금 금리(연 %)"> <span style="font-size:10.5px;color:var(--ink-3)">예금 금리 %</span></div>' : '';
      return '<div class="row" style="grid-template-columns:' + cols + '"><span style="font-size:13.5px;font-weight:600">' + esc(a.name) + rateInput + '</span><span style="font-size:12px;color:var(--ink-3)">' + esc(a['class'] || a.type || '') + '</span><span><input class="num amt" data-key="' + k + '" type="number" step="50" min="0" value="' + amt + '"></span><span class="mono r" style="font-size:13px">' + w.toFixed(0) + '%</span><span style="text-align:center"><button class="delx" data-key="' + k + '" title="빼기">🗑</button></span></div>';
    }).join('');
    $('composition').innerHTML = '<div class="tbl">' + head + rows + '</div>';
    $('compTotal').innerHTML = '합계 <b class="mono" style="color:var(--ink-2)">' + total.toLocaleString('en-US') + '만원</b> — 금액은 비중 계산에만 쓰이며 브라우저에만 저장됩니다.';
    $('analysis').style.display = 'block';

    // 이벤트
    $('composition').querySelectorAll('.amt').forEach(function (inp) { inp.addEventListener('input', function () { var am = loadAmounts(); am[inp.getAttribute('data-key')] = +inp.value || 0; saveAmounts(am); refreshWeights(); scheduleRecalc(); }); });
    $('composition').querySelectorAll('.rate').forEach(function (inp) { inp.addEventListener('input', function () { var b = loadBasket(); var kk = inp.getAttribute('data-key'); if (b[kk]) { b[kk].cash_rate = (+inp.value || 3) / 100; saveBasket(b); } scheduleRecalc(); }); });
    $('composition').querySelectorAll('.delx').forEach(function (btn) { btn.addEventListener('click', function () { var b = loadBasket(), am = loadAmounts(), kk = btn.getAttribute('data-key'); delete b[kk]; delete am[kk]; saveBasket(b); saveAmounts(am); renderComposition(); recalc(); }); });
  }
  function refreshWeights() {
    var amounts = loadAmounts(), keys = Object.keys(loadBasket()), total = 0; keys.forEach(function (k) { total += (+amounts[k] || DEFAULT_AMOUNT); });
    var rows = $('composition').querySelectorAll('.row'); var i = 0;
    keys.forEach(function (k) { var amt = (+amounts[k] || DEFAULT_AMOUNT); var cell = rows[i + 1] && rows[i + 1].querySelector('.r'); if (cell) cell.textContent = (total > 0 ? amt / total * 100 : 0).toFixed(0) + '%'; i++; });
    var t = 0; keys.forEach(function (k) { t += (+amounts[k] || DEFAULT_AMOUNT); });
    $('compTotal').innerHTML = '합계 <b class="mono" style="color:var(--ink-2)">' + t.toLocaleString('en-US') + '만원</b> — 금액은 비중 계산에만 쓰이며 브라우저에만 저장됩니다.';
  }

  /* ── 분석 렌더 ── */
  function tiles(container, items) { container.innerHTML = items.map(function (t, i) { return '<div class="tile" style="padding:' + (i === 0 ? '0 16px 0 0' : '0 16px') + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + t[0] + '</div><div class="v">' + t[1] + '</div></div>'; }).join(''); }

  function renderAnalysis() {
    var d = PA;
    if (!d || d.error) { $('statsTable').innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:16px 0">' + esc((d && d.error) || '통계를 계산할 수 없습니다.') + '</div>'; $('heatmap').innerHTML = ''; $('planeChart').innerHTML = ''; $('perfTiles').innerHTML = ''; $('taxTable').innerHTML = ''; $('portTiles').innerHTML = ''; $('taxTiles').innerHTML = ''; return; }
    // 성향 최적점 (테스트 결과가 localStorage에 있으면)
    d.optimal = null;
    var prof = null; try { prof = JSON.parse(localStorage.getItem('invriskprofile') || 'null'); } catch (e) {}
    if (prof && d.cml) { var c = d.cml[prof.market] || d.cml[d.bench]; if (c && c.sigma_m) { var tp = tangency(c.er_m, c.rf, c.sigma_m, prof.A); d.optimal = { sigma: tp.sigma_p, er: tp.er_p, label: '성향 최적점 (' + prof.label + ')' }; } }
    // 제외
    $('excludedNote').innerHTML = (d.excluded && d.excluded.length) ? '⚠️ 시세 이력이 부족해 통계에서 제외: ' + d.excluded.map(esc).join(', ') : '';
    // 통계 표
    var cols = '2.2fr 0.9fr 1.1fr 1.1fr';
    var head = '<div class="row head" style="grid-template-columns:' + cols + '"><span class="col-label">자산</span><span class="col-label r">비중</span><span class="col-label r">기대수익(연)</span><span class="col-label r">변동성 σ(연)</span></div>';
    var body = d.assets.map(function (a, i) { var last = i === d.assets.length - 1; return '<div class="row" style="grid-template-columns:' + cols + ';font-family:var(--font-mono);font-size:12.5px' + (last ? ';border-bottom:none' : '') + '"><span style="font-family:var(--font-sans)">' + esc(a.name) + '</span><span class="r">' + (a.weight * 100).toFixed(0) + '%</span><span class="r" style="color:' + (a.mu >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)') + '">' + pctS(a.mu) + '</span><span class="r">' + pct(a.sigma) + '</span></div>'; }).join('');
    $('statsTable').innerHTML = head + body;
    $('statsCaption').innerHTML = '표본 ' + d.n_months + '개월 · 달러 자산은 환율 변화 포함(환노출) · 기대수익은 과거 실측 연율화라 <b>추정 오차가 큽니다</b>.';
    // 히트맵
    $('heatmap').innerHTML = heatmap(d.labels, d.corr);
    // 평면
    $('planeChart').innerHTML = planeChart(d);
    if (d.optimal) { var diff = d.port.sigma - d.optimal.sigma; var msg = Math.abs(diff) < 0.03 ? '현재 포트폴리오 위험이 성향 최적점과 비슷한 수준입니다.' : (diff > 0 ? '현재 σ(' + (d.port.sigma * 100).toFixed(1) + '%)가 성향 최적점(' + (d.optimal.sigma * 100).toFixed(1) + '%)보다 <b>높습니다</b> — 성향 기준으론 위험을 더 지고 있는 셈.' : '현재 σ(' + (d.port.sigma * 100).toFixed(1) + '%)가 성향 최적점(' + (d.optimal.sigma * 100).toFixed(1) + '%)보다 <b>낮습니다</b> — 더 감수할 여지가 있다는 뜻이기도.'); $('planeChart').insertAdjacentHTML('beforeend', '<div style="font-size:12px;color:var(--ink-2);margin-top:10px;padding:10px 12px;border:1px solid var(--line);border-radius:var(--radius-md);background:var(--paper-2)">🧭 ' + msg + '</div>'); }
    tiles($('portTiles'), [['내 포트폴리오 기대수익', pctS(d.port.er) + ' (연)'], ['내 포트폴리오 변동성 σ', pct(d.port.sigma) + ' (연)']]);
    // 성과지표
    var p = d.performance;
    if (p) {
      tiles($('perfTiles'), [
        ['샤프비율', p.sharpe != null ? p.sharpe.toFixed(2) : '—'],
        ['베타 β', p.beta != null ? p.beta.toFixed(2) : '—'],
        ['트레이너', p.treynor != null ? (p.treynor * 100).toFixed(1) + '%' : '—'],
        ['젠센 알파', p.jensen != null ? (p.jensen >= 0 ? '+' : '') + (p.jensen * 100).toFixed(1) + '%p' : '—'],
        ['M²', p.m2 != null ? (p.m2 * 100).toFixed(1) + '%' : '—']
      ]);
      $('perfCaption').innerHTML = '표본 ' + p.n + '개월 · 포트폴리오 ' + pctS(p.er_p) + ' / σ ' + pct(p.sigma_p) + ' · ' + esc(d.bench_label) + '(원화 환산) ' + pctS(p.er_b) + ' / σ ' + pct(p.sigma_b) + ' · R_f ' + pct(d.rf);
    } else { $('perfTiles').innerHTML = '<div style="color:var(--ink-3);font-size:12.5px">겹치는 표본이 12개월 미만이라 성과지표를 계산하지 않았습니다.</div>'; $('perfCaption').textContent = ''; }
    // 세금
    var tcols = '2.2fr 2fr 1.1fr 1fr 1.1fr';
    var thead = '<div class="row head" style="grid-template-columns:' + tcols + '"><span class="col-label">자산</span><span class="col-label">과세 방식</span><span class="col-label r">세전</span><span class="col-label r">실효세율</span><span class="col-label r">세후</span></div>';
    var tbody = d.tax.rows.map(function (r, i) { var last = i === d.tax.rows.length - 1; return '<div class="row" style="grid-template-columns:' + tcols + ';font-size:12.5px' + (last ? ';border-bottom:none' : '') + '"><span style="font-weight:600">' + esc(r.name) + '</span><span style="color:var(--ink-3)">' + esc(r.rule) + '</span><span class="mono r">' + pctS(r.mu) + '</span><span class="mono r" style="color:var(--ink-3)">' + pct(r.eff_rate) + '</span><span class="mono r">' + pctS(r.mu_after) + '</span></div>'; }).join('');
    $('taxTable').innerHTML = thead + tbody;
    tiles($('taxTiles'), [['포트폴리오 세전 기대수익', pctS(d.tax.port_pretax) + ' (연)'], ['포트폴리오 세후(어림)', pctS(d.tax.port_aftertax) + ' (연)']]);
  }

  /* ── 서버 계산 ── */
  function buildRequest() {
    var basket = loadBasket(), amounts = loadAmounts();
    var assets = Object.keys(basket).map(function (k) { var a = basket[k]; return { key: k, name: a.name, yahoo: a.yahoo, ticker: a.ticker, type: a.type, currency: a.currency, 'class': a['class'], amount: (+amounts[k] || DEFAULT_AMOUNT), cash_rate: a.cash_rate || 0.03 }; });
    return { months: state.months, bench: state.bench, assets: assets };
  }
  function scheduleRecalc() { clearTimeout(recalcTimer); recalcTimer = setTimeout(recalc, 600); }
  function recalc() {
    if (!Object.keys(loadBasket()).length) return;
    var seq = ++reqSeq; $('status').classList.add('on');
    fetch('api/portfolio', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildRequest()) })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (seq !== reqSeq) return; $('status').classList.remove('on'); PA = d; renderAnalysis(); })
      .catch(function (e) { if (seq !== reqSeq) return; $('status').classList.remove('on'); PA = { error: '서버 연결 실패: ' + e.message }; renderAnalysis(); });
  }

  /* ── 인터랙션 ── */
  function wireSeg(id, onChange) { var seg = $(id); if (!seg) return; seg.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return; seg.querySelectorAll('button').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on'); onChange(b.getAttribute('data-val')); }); }

  function init() {
    $('addPreset').addEventListener('click', function () { var code = $('presetSel').value; var p = PRESETS.filter(function (x) { return x[1] === code; })[0]; if (!p) return; var b = loadBasket(); b[p[1]] = { name: p[0], yahoo: p[1], ticker: p[1], type: p[2], currency: p[3], 'class': p[4] }; saveBasket(b); renderComposition(); recalc(); });
    $('addCash').addEventListener('click', function () { var b = loadBasket(); b['CASH'] = { name: '예금(무위험)', yahoo: null, ticker: 'CASH', type: '예금', currency: 'KRW', 'class': '무위험', cash_rate: 0.03 }; saveBasket(b); renderComposition(); recalc(); });
    wireSeg('monthsSeg', function (v) { state.months = +v; recalc(); });
    wireSeg('benchSeg', function (v) { state.bench = v; recalc(); });
    renderComposition();
    if (Object.keys(loadBasket()).length) recalc();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
