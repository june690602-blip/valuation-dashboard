/* ══════════════════════════════════════════════════════════════════════
   투자지표 — 투자성향 테스트(Meridian). risk_profile.py 이식(문항·채점·CML 접점).
   결과는 localStorage 'invriskprofile'에 저장 → 포트폴리오 '성향 최적점'에서 재사용.
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]; }); }

  var QUESTIONS = [
    { t: '이 돈, 얼마나 오래 묻어둘 수 있나요?', o: [['1년 미만 — 곧 쓸 돈이에요', 1], ['1~3년', 2], ['3~5년', 3], ['5~10년', 4], ['10년 이상 — 잊고 지낼 수 있어요', 5]] },
    { t: '투자 경험이 가장 멀리 닿아본 곳은?', o: [['예금·적금까지', 1], ['펀드·ETF 간접투자까지', 2], ['국내 주식 직접투자까지', 3], ['해외주식·채권까지', 4], ['파생상품·대체투자까지', 5]] },
    { t: '이 투자금에 손실이 나면 생활에 어떤 영향이 있나요?', o: [['생활비에 바로 타격이 옵니다', 1], ['결혼·주택 등 계획이 흔들립니다', 2], ['불편하지만 감당할 수 있습니다', 3], ['여유자금이라 영향 없습니다', 4], ['손실이 나도 추가 투자 여력이 있습니다', 5]] },
    { t: '투자한 주식이 한 달 만에 −20%. 뉴스는 온통 비관론입니다. 나는?', b: true, o: [['전부 팔고 발 뻗고 잔다', 1], ['절반은 팔아 위험을 줄인다', 2], ['판단을 유지하고 버틴다', 3], ['오히려 조금 더 산다', 4], ['계획대로 크게 추가 매수한다', 5]] },
    { t: '동전이 앞면이면 1,000만원, 뒷면이면 0원을 받는 게임권. 남에게 넘긴다면 최소 얼마는 받아야 하나요?', b: true, o: [['300만원 — 불확실한 건 빨리 확정 짓고 싶다', 1], ['400만원은 받아야 한다', 2], ['500만원 — 기댓값만큼은 받아야 공평하다', 4], ['550만원 이상 아니면 그냥 게임하겠다', 5]] },
    { t: '동전이 앞면이면 +150만원, 뒷면이면 −100만원인 게임을 제안받았다면?', b: true, o: [['절대 안 한다 — 100만원 잃는 게 더 크게 느껴진다', 1], ['내키지 않아 거절한다', 2], ['고민 끝에 한 번은 해본다', 3], ['기댓값이 +니까 기꺼이 한다', 4], ['이런 기회는 반복해서 잡는다', 5]] },
    { t: '투자 목표에 가장 가까운 것은?', o: [['원금은 무조건 지킨다', 1], ['물가상승률보다 조금 더', 2], ['시장(지수)만큼이면 충분', 3], ['시장보다 초과수익을 노린다', 4], ['몇 배 수익을 노린다 — 변동은 감수', 5]] },
    { t: '전체 금융자산 중, 넣어두고도 잠이 오는 위험자산 비율은?', o: [['10% 미만', 1], ['10~25%', 2], ['25~50%', 3], ['50~75%', 4], ['75% 이상', 5]] }
  ];
  var LEVELS = [
    [8, '안정형', '성벽을 지키는 파수꾼', '🛡️', '원금 보전이 최우선입니다. 손실의 아픔이 수익의 기쁨보다 훨씬 크게 느껴지는 유형으로, 예금·국공채 중심이 마음 편한 구성입니다. 다만 물가상승을 감안하면 ‘무위험’도 구매력 기준으론 위험이 있다는 점은 알아둘 만합니다.', { '주식': 10, '채권': 50, '예금·현금': 40 }],
    [15, '안정추구형', '천천히 자라는 나무', '🌳', '원금을 크게 다치지 않는 선에서 이자보다 나은 수익을 원합니다. 채권 비중을 축으로 우량주·배당주를 소폭 곁들이는 구성이 어울립니다. 시장 급락 시 계획을 지키는 것이 가장 중요한 유형입니다.', { '주식': 25, '채권': 50, '예금·현금': 25 }],
    [22, '위험중립형', '균형의 저울', '⚖️', '위험과 수익의 교환을 이해하고, 기댓값이 맞으면 변동을 감수합니다. 주식과 채권을 비슷한 무게로 두고 정기적으로 리밸런싱하는 전략이 잘 맞습니다.', { '주식': 40, '채권': 40, '예금·현금': 20 }],
    [29, '적극투자형', '기회를 노리는 매', '🦅', '초과수익을 위해 변동성을 적극 감수합니다. 주식 중심 구성이 어울리지만, 하락장에서 추가 매수할 현금을 남겨두는 규율이 성과를 가릅니다.', { '주식': 60, '채권': 30, '예금·현금': 10 }],
    [35, '공격투자형', '파도를 타는 서퍼', '🏄', '높은 변동성 자체를 기회로 봅니다. 이론상 차입(레버리지)까지 허용되는 유형이지만, 실제로는 최대낙폭(MDD)을 버틸 수 있는지가 핵심입니다. 집중투자일수록 이 대시보드의 가치평가·백테스트로 근거를 확인하세요.', { '주식': 80, '채권': 15, '예금·현금': 5 }]
  ];
  var SMIN = 8, SMAX = 40, AMAX = 9.0, AMIN = 1.3;
  function riskA(score) { var s = Math.max(SMIN, Math.min(SMAX, score)); return Math.round((AMAX - (s - SMIN) * (AMAX - AMIN) / (SMAX - SMIN)) * 100) / 100; }
  function grade(idx) {
    var score = 0; for (var i = 0; i < QUESTIONS.length; i++) score += QUESTIONS[i].o[idx[i]][1];
    var lv = 0; for (i = 0; i < LEVELS.length; i++) if (score >= LEVELS[i][0]) lv = i;
    var beh = [], gen = [];
    for (i = 0; i < QUESTIONS.length; i++) { var sc = QUESTIONS[i].o[idx[i]][1]; (QUESTIONS[i].b ? beh : gen).push(sc); }
    var notes = [];
    if (beh.length && gen.length) {
      var ba = beh.reduce(function (a, b) { return a + b; }, 0) / beh.length, ga = gen.reduce(function (a, b) { return a + b; }, 0) / gen.length;
      if (ba + 0.8 < ga) notes.push('계획(목표·기간)은 공격적인데 <b>심리 문항에선 손실회피가 강하게</b> 나타났습니다. 실제 하락장에서 계획보다 보수적으로 행동할 가능성이 높으니, 목표 비중을 한 단계 낮춰 잡는 편이 오래 버티는 데 유리할 수 있습니다.');
      else if (ga + 0.8 < ba) notes.push('심리는 위험을 잘 견디는데 <b>계획 여건(기간·여유자금)이 보수적</b>입니다. 여건이 허락하는 범위 안에서만 공격적으로 — 비상금·투자기간부터 확보하는 게 순서입니다.');
    }
    var L = LEVELS[lv];
    return { score: score, level: lv + 1, label: L[1], nickname: L[2], emoji: L[3], description: L[4], allocation: L[5], A: riskA(score), notes: notes };
  }
  function tangency(erM, rf, sigM, A) {
    var y = (A <= 0 || sigM <= 0) ? 0 : (erM - rf) / (A * sigM * sigM);
    var sigP = Math.abs(y) * sigM, erP = rf + y * (erM - rf);
    return { y: y, sigma_p: sigP, er_p: erP, utility: erP - 0.5 * A * sigP * sigP, sharpe: sigM > 0 ? (erM - rf) / sigM : 0, mrs: A * sigP };
  }

  /* ── CML 접점 차트 (cml_tangency_chart 이식) ── */
  function cmlChart(m, A) {
    var t = tangency(m.er_m, m.rf, m.sigma_m, A);
    var sigMax = Math.max(m.sigma_m * 1.6, t.sigma_p * 1.25, 0.01), N = 90;
    var W = 760, padL = 52, padR = 20, top = 18, plotH = 330, xw = W - padL - padR;
    var yCap = Math.max(m.rf + t.sharpe * sigMax, t.er_p) * 1.32, yMin = 0;
    var X = function (s) { return padL + s / sigMax * xw; }, Y = function (v) { return top + (1 - (v - yMin) / (yCap - yMin)) * plotH; };
    function P(fn) { var p = ''; for (var i = 0; i < N; i++) { var s = sigMax * i / (N - 1), v = fn(s); if (v > yCap) { p += ''; continue; } p += (p ? 'L' : 'M') + X(s).toFixed(1) + ' ' + Y(v).toFixed(1) + ' '; } return p; }
    var svg = [];
    svg.push('<svg viewBox="0 0 ' + W + ' ' + (top + plotH + 34) + '" style="width:100%;height:auto;display:block">');
    // grid + y labels
    for (var g = 0; g <= 4; g++) { var yy = top + g / 4 * plotH, val = yCap - (yCap - yMin) * g / 4; svg.push('<line x1="' + padL + '" x2="' + (padL + xw) + '" y1="' + yy + '" y2="' + yy + '" stroke="var(--line)" stroke-width="1"/>'); svg.push('<text x="' + (padL - 8) + '" y="' + (yy + 4) + '" font-size="11" fill="var(--ink-3)" font-family="var(--font-mono)" text-anchor="end">' + (val * 100).toFixed(1) + '%</text>'); }
    for (var tk = 0; tk <= 4; tk++) { var sv = sigMax * tk / 4; svg.push('<text x="' + X(sv) + '" y="' + (top + plotH + 18) + '" font-size="11" fill="var(--ink-3)" font-family="var(--font-mono)" text-anchor="middle">' + (sv * 100).toFixed(0) + '%</text>'); }
    // 무차별곡선 (dashed) + CML
    svg.push('<path d="' + P(function (s) { return t.utility + 0.5 * A * s * s; }) + '" fill="none" stroke="var(--dv-plum)" stroke-width="2" stroke-dasharray="5 4"/>');
    svg.push('<path d="' + P(function (s) { return m.rf + t.sharpe * s; }) + '" fill="none" stroke="var(--dv-navy)" stroke-width="2.4"/>');
    // 보조선(접점→축)
    svg.push('<line x1="' + X(t.sigma_p) + '" x2="' + X(t.sigma_p) + '" y1="' + Y(0) + '" y2="' + Y(t.er_p) + '" stroke="var(--line-strong)" stroke-width="1" stroke-dasharray="3 3"/>');
    svg.push('<line x1="' + X(0) + '" x2="' + X(t.sigma_p) + '" y1="' + Y(t.er_p) + '" y2="' + Y(t.er_p) + '" stroke="var(--line-strong)" stroke-width="1" stroke-dasharray="3 3"/>');
    // 점: 무위험 / 시장 M / 접점
    svg.push('<circle cx="' + X(0) + '" cy="' + Y(m.rf) + '" r="5" fill="var(--dv-slate)"/><text x="' + (X(0) + 9) + '" y="' + (Y(m.rf) + 4) + '" font-size="11" fill="var(--ink-2)" font-family="var(--font-mono)">R_f</text>');
    svg.push('<circle cx="' + X(m.sigma_m) + '" cy="' + Y(m.er_m) + '" r="6" fill="var(--dv-gold)"/><text x="' + X(m.sigma_m) + '" y="' + (Y(m.er_m) - 9) + '" font-size="12" fill="var(--ink)" font-family="var(--font-sans)" font-weight="600" text-anchor="middle">M · ' + esc(m.label) + '</text>');
    svg.push('<circle cx="' + X(t.sigma_p) + '" cy="' + Y(t.er_p) + '" r="8" fill="var(--dv-clay)" stroke="var(--paper)" stroke-width="2"/><text x="' + (X(t.sigma_p) + 12) + '" y="' + (Y(t.er_p) + 4) + '" font-size="12.5" fill="var(--dv-clay)" font-family="var(--font-sans)" font-weight="700">★ 나의 최적점</text>');
    if (t.y > 1) svg.push('<text x="' + X(m.sigma_m) + '" y="' + (Y(m.er_m) + 20) + '" font-size="10.5" fill="var(--ink-3)" font-family="var(--font-sans)" text-anchor="middle">M 오른쪽 = 차입(레버리지) 구간</text>');
    svg.push('<text x="' + (padL + xw) + '" y="' + (top + plotH + 32) + '" font-size="11" fill="var(--ink-3)" font-family="var(--font-sans)" text-anchor="end">연 변동성 σ →</text>');
    svg.push('<text x="' + (padL - 36) + '" y="' + (top + 6) + '" font-size="11" fill="var(--ink-3)" font-family="var(--font-sans)">E(r)</text>');
    svg.push('</svg>');
    // 범례
    var lg = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:12px;color:var(--ink-2)"><span style="display:inline-flex;align-items:center;gap:6px"><span style="width:14px;height:2px;background:var(--dv-navy);display:inline-block"></span>자본시장선(CML)</span><span style="display:inline-flex;align-items:center;gap:6px"><span style="width:14px;height:2px;background:var(--dv-plum);display:inline-block"></span>나의 무차별곡선 (A=' + A.toFixed(1) + ')</span></div>';
    return svg.join('') + lg;
  }

  /* ── 상태 ── */
  var state = { step: 0, answers: [], A: null, market: 'KR' };
  var MP = null, profile = null;

  function renderWizard() {
    var n = QUESTIONS.length, step = state.step, q = QUESTIONS[step];
    var prev = state.answers[step];
    var opts = q.o.map(function (o, i) { return '<div class="opt' + (prev === i ? ' sel' : '') + '" data-i="' + i + '">' + esc(o[0]) + '</div>'; }).join('');
    var isLast = step === n - 1;
    $('wizard').innerHTML =
      '<div class="progbar"><i style="width:' + (step / n * 100) + '%"></i></div>' +
      '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:12px"><span class="kick">질문 ' + (step + 1) + ' / ' + n + '</span>' + (q.b ? '<span style="font-size:11px;color:var(--dv-plum)">심리 문항</span>' : '') + '</div>' +
      '<div style="font-family:var(--font-display);font-weight:700;font-size:21px;letter-spacing:-0.01em;line-height:1.4;margin:10px 0 6px">' + esc(q.t) + '</div>' +
      '<div id="opts">' + opts + '</div>' +
      '<div style="display:flex;gap:10px;margin-top:22px">' +
        (step > 0 ? '<button id="prevBtn" class="btn btn-secondary btn-sm">← 이전</button>' : '') +
        '<button id="nextBtn" class="btn btn-primary btn-sm"' + (prev == null ? ' disabled' : '') + '>' + (isLast ? '결과 보기 🎉' : '다음 →') + '</button>' +
      '</div>';
    $('opts').addEventListener('click', function (e) { var d = e.target.closest('.opt'); if (!d) return; state.answers[step] = +d.getAttribute('data-i'); renderWizard(); });
    if ($('prevBtn')) $('prevBtn').addEventListener('click', function () { state.step--; renderWizard(); });
    $('nextBtn').addEventListener('click', function () { if (state.answers[step] == null) return; if (isLast) finish(); else { state.step++; renderWizard(); } });
  }

  function finish() {
    profile = grade(state.answers); state.A = profile.A;
    $('wizard').style.display = 'none'; $('result').style.display = 'block';
    renderResult();
    save();
  }

  function renderResult() {
    var p = profile, m = MP ? MP[state.market] : { rf: 0.035, er_m: 0.095, sigma_m: 0.17, label: state.market === 'KR' ? 'KOSPI200' : 'S&P 500' };
    var t = tangency(m.er_m, m.rf, m.sigma_m, state.A);
    var notes = p.notes.map(function (nn) { return '<div style="display:flex;gap:8px;margin-top:10px;padding:12px 14px;border:1px solid var(--line);border-radius:var(--radius-md);background:var(--paper-2)"><span>🧠</span><span style="font-size:12.5px;color:var(--ink-2);line-height:1.6">' + nn + '</span></div>'; }).join('');
    var alloc = Object.keys(p.allocation).map(function (k) { return '<span style="font-size:13px;color:var(--ink-2)"><b class="mono" style="color:var(--ink)">' + p.allocation[k] + '%</b> ' + esc(k) + '</span>'; }).join('<span style="color:var(--line-strong)">·</span>');
    $('result').innerHTML =
      '<div style="border:1px solid var(--line);border-left:3px solid var(--dv-clay);border-radius:var(--radius-md);padding:22px 24px">' +
        '<div style="font-size:34px">' + p.emoji + '</div>' +
        '<div style="font-family:var(--font-display);font-weight:700;font-size:28px;letter-spacing:-0.01em;margin-top:6px">' + esc(p.label) + ' — “' + esc(p.nickname) + '”</div>' +
        '<div style="font-size:12px;color:var(--ink-3);margin-top:4px">총점 ' + p.score + '점 (' + QUESTIONS.length + '문항) · 5단계 중 ' + p.level + '단계 · 위험회피계수 A ≈ <b class="mono">' + p.A.toFixed(1) + '</b></div>' +
        '<p style="font-size:13.5px;color:var(--ink-2);line-height:1.7;margin:14px 0 0">' + esc(p.description) + '</p>' + notes +
      '</div>' +
      '<div style="margin-top:30px"><div style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap"><span class="kick">자본시장선(CML) 위 나의 위치</span>' +
        '<div style="display:flex;gap:12px;align-items:center"><div class="seg" id="mktSeg"><button class="' + (state.market === 'KR' ? 'on' : '') + '" data-val="KR">🇰🇷 KOSPI200</button><button class="' + (state.market === 'US' ? 'on' : '') + '" data-val="US">🇺🇸 S&amp;P 500</button></div></div></div>' +
        '<p style="font-size:12.5px;color:var(--ink-3);line-height:1.6;margin:8px 0 4px;max-width:74ch">무위험자산과 시장포트폴리오(M)를 잇는 CML 위에서, 내 효용을 최대화하는 지점이 <b>무차별곡선이 CML에 접하는 곳</b>입니다. A를 움직이면 접점이 미끄러집니다.</p>' +
        '<div style="display:flex;align-items:center;gap:12px;margin:14px 0 6px"><span style="font-size:12px;color:var(--ink-2);white-space:nowrap">위험회피계수 A</span><input type="range" min="1" max="10" step="0.1" value="' + state.A + '" id="aSlider"><span class="mono" id="aVal" style="font-size:13px;min-width:34px">' + state.A.toFixed(1) + '</span></div>' +
        '<div id="cmlWrap"></div>' +
        '<div id="cmlTiles" style="display:flex;flex-wrap:wrap;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:16px 0;margin-top:14px"></div>' +
        '<div id="cmlCheck" style="font-size:11.5px;color:var(--ink-3);margin-top:8px"></div>' +
      '</div>' +
      '<div style="margin-top:30px"><span class="kick">유형별 참고 배분 예시</span><div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:12px;align-items:center">' + alloc + '</div><div style="font-size:11.5px;color:var(--ink-3);margin-top:8px">교과서적 예시일 뿐 정답이 아닙니다. 위 CML의 y*와 함께 참고만 하세요.</div></div>' +
      '<div style="display:flex;gap:10px;margin-top:26px;flex-wrap:wrap"><a class="btn btn-primary" href="portfolio.html" style="text-decoration:none">🧺 포트폴리오에서 내 위치 비교 →</a><button id="retryBtn" class="btn btn-secondary">다시 검사하기</button></div>';
    drawCml();
    $('mktSeg').addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return; $('mktSeg').querySelectorAll('button').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on'); state.market = b.getAttribute('data-val'); drawCml(); save(); });
    $('aSlider').addEventListener('input', function () { state.A = +this.value; $('aVal').textContent = state.A.toFixed(1); drawCml(); save(); });
    $('retryBtn').addEventListener('click', function () { state = { step: 0, answers: [], A: null, market: state.market }; $('result').style.display = 'none'; $('wizard').style.display = 'block'; renderWizard(); });
  }

  function drawCml() {
    var m = MP ? MP[state.market] : { rf: 0.035, er_m: 0.095, sigma_m: 0.17, label: state.market === 'KR' ? 'KOSPI200' : 'S&P 500' };
    var t = tangency(m.er_m, m.rf, m.sigma_m, state.A);
    $('cmlWrap').innerHTML = cmlChart(m, state.A);
    var y = t.y;
    $('cmlTiles').innerHTML = [
      ['최적 위험자산 비중 y*', (y * 100).toFixed(0) + '%'],
      ['무위험자산', y <= 1 ? (Math.max(1 - y, 0) * 100).toFixed(0) + '%' : '차입 구간'],
      ['나의 기대수익 E(Rp)', (t.er_p * 100).toFixed(1) + '%'],
      ['나의 변동성 σp', (t.sigma_p * 100).toFixed(1) + '%']
    ].map(function (it, i) { return '<div style="flex:1;min-width:120px;padding:' + (i === 0 ? '0 16px 0 0' : '0 16px') + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + it[0] + '</div><div class="mono" style="font-size:20px;font-weight:500;margin-top:6px">' + it[1] + '</div></div>'; }).join('');
    $('cmlCheck').innerHTML = '접점 검산: MRS = A·σ* = <b class="mono">' + t.mrs.toFixed(3) + '</b> ≈ 샤프비율 (E(Rm)−R_f)/σm = <b class="mono">' + t.sharpe.toFixed(3) + '</b> ✓ · E(Rm)=R_f+MRP, σm은 지수 10년 주간 수익률 추정.';
  }

  function save() {
    if (!profile) return;
    var m = MP ? MP[state.market] : null;
    var y = m ? tangency(m.er_m, m.rf, m.sigma_m, state.A).y : null;
    localStorage.setItem('invriskprofile', JSON.stringify({
      label: profile.label, nickname: profile.nickname, emoji: profile.emoji,
      level: profile.level, score: profile.score, A: state.A,
      y_star: y, market: state.market, market_label: m ? m.label : ''
    }));
  }

  function init() {
    renderWizard();
    fetch('api/market').then(function (r) { return r.json(); }).then(function (d) { if (!d.error) MP = d; if (profile) { drawCml(); save(); } }).catch(function () {});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
