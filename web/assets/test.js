/* 투자지표 — 투자 위험 프로파일. 문항·채점은 Python API를 단일 기준으로 사용한다. */
(function () {
  'use strict';

  var RESULT_KEY = 'invriskprofile';
  var DRAFT_KEY = 'invriskprofile_draft';
  var config = null;
  var profile = null;
  var marketParams = null;
  var marketSource = '기본 가정치';
  var state = { step: 0, answers: [], market: 'KR', assessed_A: null, scenario_A: null };

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char];
    });
  }
  function finite(value) { return typeof value === 'number' && isFinite(value); }
  function readJSON(key) {
    try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (error) { return null; }
  }
  function writeJSON(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (error) { return false; }
  }
  function removeStored(key) { try { localStorage.removeItem(key); } catch (error) {} }
  function nowLabel(value) {
    if (!value) return '';
    try { return new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium' }).format(new Date(value)); } catch (error) { return ''; }
  }

  function validDraft(value) {
    if (!value || value.schema_version !== config.schema_version || !Array.isArray(value.answers)) return false;
    if (!Number.isInteger(value.step) || value.step < 0 || value.step >= config.question_count) return false;
    if (value.answers.length > config.question_count) return false;
    return value.answers.every(function (answer, index) {
      var question = config.questions[index];
      return !!question && (answer == null || (Number.isInteger(answer) && answer >= 0 && answer < question.options.length));
    });
  }

  function validResult(value) {
    return !!(value && value.schema_version === config.schema_version && value.label &&
      finite(value.assessed_A) && value.dimension_scores && typeof value.dimension_scores === 'object');
  }

  function loadSavedState() {
    var draft = readJSON(DRAFT_KEY);
    var saved = readJSON(RESULT_KEY);
    if (draft && !validDraft(draft)) { removeStored(DRAFT_KEY); draft = null; }
    if (saved && !validResult(saved)) { removeStored(RESULT_KEY); saved = null; }

    var start = $('startBtn');
    var fresh = $('freshBtn');
    var note = $('resumeNote');
    start.disabled = false;
    fresh.hidden = true;
    note.textContent = '';

    if (draft) {
      state.step = draft.step;
      state.answers = draft.answers.slice();
      state.market = draft.market === 'US' ? 'US' : 'KR';
      start.textContent = '이어서 하기';
      fresh.hidden = false;
      fresh.textContent = '처음부터 시작';
      note.textContent = '질문 ' + (state.step + 1) + '부터 이어집니다.';
    } else if (saved) {
      profile = saved;
      state.market = saved.market === 'US' ? 'US' : 'KR';
      state.assessed_A = saved.assessed_A;
      state.scenario_A = finite(saved.scenario_A) ? saved.scenario_A : saved.assessed_A;
      start.textContent = '저장된 결과 보기';
      fresh.hidden = false;
      fresh.textContent = '새로 검사하기';
      note.textContent = nowLabel(saved.assessed_at) + (saved.assessed_at ? '에 완료한 결과입니다.' : '저장된 결과가 있습니다.');
    } else {
      start.textContent = '내 투자 기준 찾기';
    }
  }

  function saveDraft() {
    writeJSON(DRAFT_KEY, {
      schema_version: config.schema_version,
      step: state.step,
      answers: state.answers,
      market: state.market,
      updated_at: new Date().toISOString()
    });
  }

  function beginFresh() {
    removeStored(DRAFT_KEY);
    removeStored(RESULT_KEY);
    profile = null;
    state = { step: 0, answers: [], market: state.market || 'KR', assessed_A: null, scenario_A: null };
    saveDraft();
    renderWizard();
  }

  function startOrResume() {
    if (profile && !readJSON(DRAFT_KEY)) { renderResult(); return; }
    if (!validDraft(readJSON(DRAFT_KEY))) saveDraft();
    renderWizard();
  }

  function showOnly(id) {
    ['intro', 'wizard', 'result'].forEach(function (name) { $(name).hidden = name !== id; });
  }

  function renderWizard() {
    showOnly('wizard');
    var n = config.question_count;
    var step = state.step;
    var question = config.questions[step];
    var selected = state.answers[step];
    var progress = Math.round((step + 1) / n * 100);
    var options = question.options.map(function (label, index) {
      var checked = selected === index;
      return '<label class="answer-option' + (checked ? ' selected' : '') + '">' +
        '<input class="sr-only" type="radio" name="riskAnswer" value="' + index + '"' + (checked ? ' checked' : '') + '>' +
        '<span class="option-index" aria-hidden="true">' + (index + 1) + '</span><span>' + esc(label) + '</span></label>';
    }).join('');

    $('wizard').innerHTML = '<div class="wizard-card">' +
      '<div class="progress-track" role="progressbar" aria-label="테스트 진행률" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + progress + '"><i style="width:' + progress + '%"></i></div>' +
      '<div class="step-meta"><span class="chapter-label">' + esc(question.chapter) + '</span><span class="step-count">' + (step + 1) + ' / ' + n + '</span></div>' +
      '<fieldset style="border:0;margin:0;padding:0"><legend class="question-title">' + esc(question.text) + '</legend>' +
      '<p class="question-guide">' + esc(question.guide) + '</p><div class="answer-list">' + options + '</div></fieldset>' +
      '<div class="wizard-actions"><button id="prevBtn" class="btn btn-secondary" type="button"' + (step === 0 ? ' disabled' : '') + '>이전</button>' +
      '<button id="nextBtn" class="btn btn-primary" type="button"' + (selected == null ? ' disabled' : '') + '>' + (step === n - 1 ? '결과 확인' : '다음') + '</button></div>' +
      '<div id="wizardStatus" class="wizard-status" aria-live="polite">' + (selected == null ? '가장 가까운 답을 하나 선택해주세요.' : '선택한 답은 이전 버튼으로 돌아가 수정할 수 있습니다.') + '</div></div>';

    $('wizard').querySelectorAll('input[name="riskAnswer"]').forEach(function (input) {
      input.addEventListener('change', function () {
        state.answers[step] = Number(this.value);
        $('wizard').querySelectorAll('.answer-option').forEach(function (label) { label.classList.remove('selected'); });
        this.closest('.answer-option').classList.add('selected');
        $('nextBtn').disabled = false;
        $('wizardStatus').textContent = '선택했습니다. 다음으로 이동하거나 이전 답을 다시 확인할 수 있습니다.';
        saveDraft();
      });
    });
    $('prevBtn').addEventListener('click', function () {
      if (state.step > 0) { state.step -= 1; saveDraft(); renderWizard(); }
    });
    $('nextBtn').addEventListener('click', function () {
      if (state.answers[step] == null) return;
      if (step === n - 1) finish();
      else { state.step += 1; saveDraft(); renderWizard(); }
    });
    var legend = $('wizard').querySelector('legend');
    if (legend) legend.setAttribute('tabindex', '-1'), legend.focus();
  }

  function finish() {
    var next = $('nextBtn');
    next.disabled = true;
    $('wizardStatus').textContent = '응답의 네 가지 축을 정리하고 있습니다…';
    fetch('api/risk-profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers: state.answers })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || data.error) throw new Error(data.error || '결과를 계산하지 못했습니다.');
        return data;
      });
    }).then(function (data) {
      profile = data;
      state.assessed_A = data.assessed_A;
      state.scenario_A = data.assessed_A;
      removeStored(DRAFT_KEY);
      saveResult();
      renderResult();
    }).catch(function (error) {
      next.disabled = false;
      $('wizardStatus').innerHTML = '<span style="color:var(--danger)">' + esc(error.message) + ' 잠시 후 다시 시도해주세요.</span>';
    });
  }

  function list(items, ordered) {
    var tag = ordered ? 'ol' : 'ul';
    return '<' + tag + '>' + (items || []).map(function (item) { return '<li>' + esc(item) + '</li>'; }).join('') + '</' + tag + '>';
  }

  function renderResult() {
    showOnly('result');
    var dimensions = config.dimensions.map(function (dimension) {
      var score = Number(profile.dimension_scores[dimension.key] || 0);
      return '<div class="dimension-card"><div class="dimension-head"><span class="dimension-name">' + esc(dimension.label) + '</span><span class="dimension-score">' + score + '</span></div>' +
        '<div class="dimension-bar" aria-label="' + esc(dimension.label) + ' ' + score + '점"><i style="width:' + score + '%"></i></div><div class="dimension-help">' + esc(dimension.short) + '</div></div>';
    }).join('');
    var notes = [];
    if (profile.guardrail_note) notes.push('<div class="insight guardrail"><b style="color:var(--ink)">감내 여력 우선 원칙</b><br>' + esc(profile.guardrail_note) + '.</div>');
    (profile.behavioral_notes || []).forEach(function (note) { notes.push('<div class="insight">' + esc(note) + '</div>'); });
    if (!notes.length) notes.push('<div class="insight guardrail">응답의 네 가지 축이 비교적 비슷한 방향으로 나타났습니다. 그래도 실제 투자에서는 가까운 지출과 비상자금을 먼저 분리해 주세요.</div>');
    var ranges = Object.keys(profile.allocation_range || {}).map(function (key) {
      var range = profile.allocation_range[key];
      return '<div class="allocation-item"><div class="kick">' + esc(key) + '</div><div class="allocation-range">' + Number(range[0]) + '–' + Number(range[1]) + '%</div></div>';
    }).join('');

    $('result').innerHTML = '<div class="result-hero"><div class="profile-mark" aria-hidden="true">' + esc(profile.symbol) + '</div><div>' +
      '<div class="profile-badges"><span class="profile-badge">자가진단 ' + profile.score + ' / 100</span><span class="profile-badge">참고 분류 · ' + esc(profile.official_label) + '</span><span class="profile-badge">응답 흐름 · ' + esc(profile.consistency) + '</span></div>' +
      '<h1 class="result-label">' + esc(profile.label) + ' <span style="color:var(--ink-3);font-weight:500">· ' + esc(profile.archetype) + '</span></h1>' +
      '<p class="result-summary">' + esc(profile.summary) + '</p><p class="result-description">' + esc(profile.description) + '</p></div></div>' +

      '<section class="result-section" aria-labelledby="basis-title"><span class="kick">판정 근거</span><h2 id="basis-title" class="section-heading">한 점수가 아니라 네 가지 축으로 봤습니다.</h2>' +
      '<p class="section-copy">점수가 높을수록 해당 축에서 더 큰 변동을 받아들일 여지가 있다는 뜻입니다. 우열이나 투자 실력을 나타내지 않습니다.</p><div class="dimension-grid">' + dimensions + '</div><div class="insight-list">' + notes.join('') + '</div></section>' +

      '<section class="result-section" aria-labelledby="rules-title"><span class="kick">실행 원칙</span><h2 id="rules-title" class="section-heading">이 유형에 맞는 규칙을 먼저 정하세요.</h2><div class="principle-grid">' +
      '<div class="principle-card"><h3>잘 맞는 운용 원칙</h3>' + list(profile.principles, true) + '</div>' +
      '<div class="principle-card"><h3>주의해서 볼 행동</h3>' + list(profile.watchouts, false) + '</div></div></section>' +

      '<section class="result-section" aria-labelledby="allocation-title"><span class="kick">교육용 배분 범위</span><h2 id="allocation-title" class="section-heading">정답 대신 검토를 시작할 범위입니다.</h2>' +
      '<p class="section-copy">각 범위는 예시이며 동시에 최댓값을 선택하라는 뜻이 아닙니다. 소득·부채·세금·사용 시점을 반영해 합계 100% 안에서 조정해야 합니다.</p><div class="allocation-grid">' + ranges + '</div></section>' +

      '<section class="result-section" aria-labelledby="lab-title"><span class="kick">고급 분석</span><h2 id="lab-title" class="section-heading">이론값은 가정을 바꾸며 확인하세요.</h2>' +
      '<details class="model-lab"><summary>이론 실험실 · 자본시장선(CML) 열기</summary><div class="model-body">' +
      '<p class="section-copy" style="margin-top:0">아래 값은 평균-분산 모형에서 계산한 <b style="color:var(--ink)">가정 기반 참고점</b>입니다. 개인의 현금흐름·세금·집중위험을 반영하지 않으므로 권장 비중이나 상한이 아닙니다.</p>' +
      '<div style="display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap;margin-top:15px"><div class="seg" id="marketSeg"><button type="button" data-market="KR" class="' + (state.market === 'KR' ? 'on' : '') + '">KR · KOSPI200</button><button type="button" data-market="US" class="' + (state.market === 'US' ? 'on' : '') + '">US · S&amp;P 500</button></div><span id="marketSource" style="font-size:11.5px;color:var(--ink-3)">' + esc(marketSource) + '</span></div>' +
      '<div class="model-controls"><label for="scenarioA" style="font-size:12px;color:var(--ink-2);white-space:nowrap">시나리오 A</label><input id="scenarioA" type="range" min="1" max="10" step=".1" value="' + Number(state.scenario_A || profile.assessed_A).toFixed(1) + '"><output id="scenarioAValue" for="scenarioA" class="mono">' + Number(state.scenario_A || profile.assessed_A).toFixed(1) + '</output></div>' +
      '<div style="font-size:11.5px;color:var(--ink-3)">자가진단 추정치 A ≈ ' + Number(profile.assessed_A).toFixed(1) + ' · 슬라이더 변경은 결과 유형이나 포트폴리오 개인화에 반영되지 않습니다.</div>' +
      '<div id="cmlWrap" style="margin-top:12px"></div><div id="modelTiles" class="model-tiles"></div><div id="modelCheck" style="font-size:11px;color:var(--ink-3);line-height:1.6;margin-top:9px"></div></div></details></section>' +

      '<div class="result-actions"><a class="btn btn-primary" href="portfolio.html" style="text-decoration:none">포트폴리오와 비교하기</a><button id="retryBtn" class="btn btn-secondary" type="button">다시 검사하기</button><button id="deleteBtn" class="btn btn-secondary" type="button">저장 결과 삭제</button></div>';

    bindResult();
    drawCml();
    $('result').focus();
  }

  function bindResult() {
    $('marketSeg').addEventListener('click', function (event) {
      var button = event.target.closest('button');
      if (!button) return;
      state.market = button.getAttribute('data-market');
      this.querySelectorAll('button').forEach(function (item) { item.classList.toggle('on', item === button); });
      drawCml();
      saveResult();
    });
    $('scenarioA').addEventListener('input', function () {
      state.scenario_A = Number(this.value);
      $('scenarioAValue').textContent = state.scenario_A.toFixed(1);
      drawCml();
      saveResult();
    });
    $('retryBtn').addEventListener('click', beginFresh);
    $('deleteBtn').addEventListener('click', function () {
      removeStored(RESULT_KEY); removeStored(DRAFT_KEY); profile = null;
      state = { step: 0, answers: [], market: 'KR', assessed_A: null, scenario_A: null };
      showOnly('intro'); loadSavedState(); $('startBtn').focus();
    });
  }

  function getMarket() {
    if (marketParams && marketParams[state.market]) return marketParams[state.market];
    return state.market === 'US'
      ? { label: 'S&P 500', rf: .045, er_m: .095, sigma_m: .15 }
      : { label: 'KOSPI200', rf: .035, er_m: .095, sigma_m: .17 };
  }

  function tangency(market, A) {
    var y = (market.er_m - market.rf) / (A * market.sigma_m * market.sigma_m);
    var sigma = Math.abs(y) * market.sigma_m;
    var er = market.rf + y * (market.er_m - market.rf);
    return { y: y, sigma: sigma, er: er, utility: er - .5 * A * sigma * sigma, sharpe: (market.er_m - market.rf) / market.sigma_m, mrs: A * sigma };
  }

  function cmlChart(market, A) {
    var point = tangency(market, A);
    var maxSigma = Math.max(market.sigma_m * 1.65, point.sigma * 1.25, .01);
    var width = 760, left = 52, right = 20, top = 24, plotHeight = 300, plotWidth = width - left - right;
    var maxY = Math.max(market.rf + point.sharpe * maxSigma, point.er) * 1.28;
    function X(value) { return left + value / maxSigma * plotWidth; }
    function Y(value) { return top + (1 - value / maxY) * plotHeight; }
    function path(fn) {
      var result = '';
      for (var i = 0; i < 80; i++) {
        var sigma = maxSigma * i / 79, value = fn(sigma);
        if (value > maxY) continue;
        result += (result ? 'L' : 'M') + X(sigma).toFixed(1) + ' ' + Y(value).toFixed(1) + ' ';
      }
      return result;
    }
    var svg = ['<svg viewBox="0 0 760 354" role="img" aria-label="자본시장선과 현재 시나리오의 모형상 접점" style="width:100%;height:auto;display:block">', '<title>자본시장선 시나리오</title><desc>시장 가정과 위험회피계수에 따른 모형상 위험과 기대수익 접점</desc>'];
    for (var grid = 0; grid <= 4; grid++) {
      var yy = top + grid / 4 * plotHeight, value = maxY * (1 - grid / 4);
      svg.push('<line x1="' + left + '" x2="' + (left + plotWidth) + '" y1="' + yy + '" y2="' + yy + '" stroke="var(--line)"/><text x="' + (left - 8) + '" y="' + (yy + 4) + '" text-anchor="end" font-size="11" fill="var(--ink-3)">' + (value * 100).toFixed(1) + '%</text>');
    }
    svg.push('<path d="' + path(function (sigma) { return market.rf + point.sharpe * sigma; }) + '" fill="none" stroke="var(--dv-navy)" stroke-width="2.4"/>');
    svg.push('<path d="' + path(function (sigma) { return point.utility + .5 * A * sigma * sigma; }) + '" fill="none" stroke="var(--dv-plum)" stroke-width="2" stroke-dasharray="5 4"/>');
    svg.push('<circle cx="' + X(market.sigma_m) + '" cy="' + Y(market.er_m) + '" r="6" fill="var(--dv-gold)"/><text x="' + X(market.sigma_m) + '" y="' + (Y(market.er_m) - 10) + '" text-anchor="middle" font-size="12" fill="var(--ink)">M · ' + esc(market.label) + '</text>');
    svg.push('<circle cx="' + X(point.sigma) + '" cy="' + Y(point.er) + '" r="8" fill="var(--dv-clay)" stroke="white" stroke-width="2"/><text x="' + (X(point.sigma) + 12) + '" y="' + (Y(point.er) + 4) + '" font-size="12" font-weight="700" fill="var(--dv-clay)">모형상 참고점</text>');
    svg.push('<text x="' + (left + plotWidth) + '" y="348" text-anchor="end" font-size="11" fill="var(--ink-3)">연 변동성 σ →</text></svg>');
    return svg.join('');
  }

  function drawCml() {
    if (!$('cmlWrap')) return;
    var market = getMarket();
    var A = Number(state.scenario_A || profile.assessed_A);
    var point = tangency(market, A);
    $('cmlWrap').innerHTML = cmlChart(market, A);
    $('modelTiles').innerHTML = [
      ['모형상 위험자산 비중 y*', (point.y * 100).toFixed(0) + '%'],
      ['가정상 안전자산 비중', point.y <= 1 ? (Math.max(1 - point.y, 0) * 100).toFixed(0) + '%' : '차입 구간'],
      ['가정상 기대수익 E(Rp)', (point.er * 100).toFixed(1) + '%'],
      ['가정상 변동성 σp', (point.sigma * 100).toFixed(1) + '%']
    ].map(function (item) { return '<div class="model-tile"><div class="kick">' + item[0] + '</div><div class="model-value">' + item[1] + '</div></div>'; }).join('');
    $('modelCheck').innerHTML = (point.y > 1 ? '<b style="color:var(--warning)">주의: 이 값은 차입을 가정하는 비제약 모형 결과입니다. 실제 배분으로 해석하지 마세요.</b><br>' : '') +
      '접점 검산 MRS ' + point.mrs.toFixed(3) + ' ≈ 샤프비율 ' + point.sharpe.toFixed(3) + ' · 가정 출처: ' + esc(marketSource) + '.';
  }

  function saveResult() {
    if (!profile) return;
    var market = getMarket();
    var assessedPoint = tangency(market, Number(profile.assessed_A));
    var payload = Object.assign({}, profile, {
      schema_version: config.schema_version,
      assessed_at: profile.assessed_at || new Date().toISOString(),
      assessed_A: Number(profile.assessed_A),
      A: Number(profile.assessed_A),
      scenario_A: Number(state.scenario_A || profile.assessed_A),
      y_star: assessedPoint.y,
      market: state.market,
      market_label: market.label
    });
    profile = payload;
    writeJSON(RESULT_KEY, payload);
  }

  function showLoadError(message) {
    $('startBtn').disabled = true;
    $('startBtn').textContent = '테스트를 준비하지 못했습니다';
    $('loadError').hidden = false;
    $('loadError').innerHTML = '<div class="error-box">' + esc(message) + '<br>페이지를 새로고침해 다시 시도해주세요.</div>';
  }

  function init() {
    $('startBtn').addEventListener('click', startOrResume);
    $('freshBtn').addEventListener('click', beginFresh);
    var configRequest = fetch('api/risk-profile').then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || data.error) throw new Error(data.error || '테스트 기준을 불러오지 못했습니다.');
        return data;
      });
    });
    var marketRequest = fetch('api/market').then(function (response) { return response.json(); }).then(function (data) {
      if (data && !data.error && data.KR && data.US) { marketParams = data; marketSource = '시장별 국채금리·지수 변동성'; }
    }).catch(function () { marketSource = '기본 가정치(시장 데이터 연결 실패)'; });

    configRequest.then(function (data) {
      config = data;
      loadSavedState();
      return marketRequest;
    }).then(function () {
      if (profile && !$('result').hidden) { drawCml(); saveResult(); }
    }).catch(function (error) { showLoadError(error.message); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
