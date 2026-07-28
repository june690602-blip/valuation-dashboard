/* 의견 보내기 위젯 — 모든 페이지 공통.
 *
 * 로그인·회원가입 없이 오른쪽 아래 작은 버튼 하나로 의견을 보낼 수 있게 한다.
 * 보낸 내용은 서버(/api/feedback)가 받아 로그·파일에 남기고, FEEDBACK_ENDPOINT가
 * 설정돼 있으면 운영자 메일로 전달한다. 메일 주소는 클라이언트에 노출하지 않는다
 * (공개 HTML에 주소를 박으면 스팸 수집 대상이 되므로 서버에서만 중계).
 *
 * 페이지에 <script src="assets/feedback.js" defer></script> 한 줄만 넣으면 된다.
 */
(function () {
  'use strict';
  if (window.__feedbackWidget) return;   // 중복 삽입 방지
  window.__feedbackWidget = true;

  var MAX = 2000;
  var css = [
    '#fbBtn{position:fixed;right:18px;bottom:18px;z-index:45;display:inline-flex;align-items:center;gap:6px;',
    'padding:8px 14px;border:1px solid var(--line-strong,#C8C2B4);border-radius:999px;background:var(--paper,#FBF9F5);',
    'color:var(--ink-2,#514C45);font-family:var(--font-sans,system-ui);font-size:11.5px;letter-spacing:.01em;',
    'cursor:pointer;box-shadow:var(--shadow-sm,0 1px 2px rgba(20,19,15,.04));transition:background 120ms,color 120ms}',
    '#fbBtn:hover{background:var(--paper-3,#EDE8DE);color:var(--ink,#16130F)}',
    '#fbWrap{position:fixed;inset:0;z-index:90;display:none;align-items:center;justify-content:center;padding:20px;',
    'background:rgba(20,19,15,.28)}',
    '#fbWrap.on{display:flex}',
    '#fbCard{width:100%;max-width:410px;background:var(--paper,#FBF9F5);border:1px solid var(--line,#E2DDD2);',
    'border-radius:var(--radius-md,4px);box-shadow:var(--shadow-lg,0 18px 48px rgba(20,19,15,.10));padding:22px 22px 18px;',
    'font-family:var(--font-sans,system-ui)}',
    '#fbCard h2{margin:0;font-family:var(--font-display,system-ui);font-size:17px;font-weight:700;color:var(--ink,#16130F);letter-spacing:-.01em}',
    '#fbCard .fb-lead{margin:7px 0 15px;font-size:12px;line-height:1.65;color:var(--ink-2,#514C45)}',
    '#fbCard label{display:block;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;',
    'color:var(--ink-3,#8A847B);margin-bottom:5px}',
    '#fbText,#fbFrom{width:100%;box-sizing:border-box;border:1px solid var(--line-strong,#C8C2B4);border-radius:var(--radius-sm,2px);',
    'background:#fff;color:var(--ink,#16130F);font-family:inherit;font-size:13px;line-height:1.6;padding:9px 10px}',
    '#fbText{min-height:104px;resize:vertical}',
    '#fbText:focus,#fbFrom:focus{outline:2px solid var(--ink,#16130F);outline-offset:-1px}',
    '#fbCount{float:right;font-family:var(--font-mono,monospace);font-size:10.5px;color:var(--ink-4,#B3ADA3);text-transform:none;letter-spacing:0}',
    '.fb-row{margin-bottom:13px}',
    '.fb-hp{position:absolute;left:-9999px;width:1px;height:1px;opacity:0}',
    '.fb-actions{display:flex;justify-content:flex-end;gap:8px;align-items:center;margin-top:4px}',
    '.fb-note{font-size:10.5px;line-height:1.6;color:var(--ink-3,#8A847B);margin:12px 0 0}',
    '.fb-msg{font-size:12px;line-height:1.6;margin-right:auto;color:var(--ink-2,#514C45)}',
    '.fb-msg.err{color:var(--danger,#A23A2A)}',
    '.fb-b{border-radius:var(--radius-sm,2px);font-family:inherit;font-size:12.5px;padding:8px 15px;cursor:pointer}',
    '.fb-b1{background:var(--cta,#16130F);color:#fff;border:1px solid var(--cta,#16130F)}',
    '.fb-b1:hover{background:var(--cta-hover,#302A22)}',
    '.fb-b1[disabled]{opacity:.5;cursor:default}',
    '.fb-b2{background:transparent;color:var(--ink-2,#514C45);border:1px solid var(--line-strong,#C8C2B4)}',
    '.fb-b2:hover{background:var(--paper-3,#EDE8DE)}',
    '@media (max-width:600px){#fbBtn{right:12px;bottom:12px}}'
  ].join('');

  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  var btn = document.createElement('button');
  btn.id = 'fbBtn';
  btn.type = 'button';
  btn.setAttribute('aria-haspopup', 'dialog');
  btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-3.8-.8L3 21l1.9-4.9A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z"/></svg>'
    + '<span>의견 보내기</span>';

  var wrap = document.createElement('div');
  wrap.id = 'fbWrap';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  wrap.setAttribute('aria-labelledby', 'fbTitle');
  wrap.innerHTML =
    '<form id="fbCard" novalidate>' +
      '<h2 id="fbTitle">의견 보내기</h2>' +
      '<p class="fb-lead">쓰다가 불편했던 점, 틀려 보이는 숫자, 있으면 좋겠는 기능 — <b>한 줄만 적으셔도 충분합니다.</b> ' +
      '로그인이나 가입 없이 바로 전달됩니다.</p>' +
      '<div class="fb-row"><label for="fbText">의견<span id="fbCount">0 / ' + MAX + '</span></label>' +
        '<textarea id="fbText" maxlength="' + MAX + '" placeholder="예) 밸류에이션 탭의 RIM 설명이 어려웠어요 / 배당주 화면이 있으면 좋겠어요"></textarea></div>' +
      '<div class="fb-row"><label for="fbFrom">답장 받을 이메일 <span style="text-transform:none;letter-spacing:0;font-weight:400">(선택 — 안 적으셔도 됩니다)</span></label>' +
        '<input id="fbFrom" type="email" autocomplete="email" placeholder="you@example.com"></div>' +
      '<div class="fb-hp" aria-hidden="true"><label for="fbCompany">회사</label><input id="fbCompany" tabindex="-1" autocomplete="off"></div>' +
      '<div class="fb-actions"><span class="fb-msg" id="fbMsg"></span>' +
        '<button type="button" class="fb-b fb-b2" id="fbCancel">닫기</button>' +
        '<button type="submit" class="fb-b fb-b1" id="fbSend">보내기</button></div>' +
      '<p class="fb-note">보내주신 내용과 (적으셨다면) 이메일은 서비스 개선 목적으로만 확인하며 다른 곳에 제공하지 않습니다. ' +
      '개인정보·계좌·비밀번호는 적지 말아 주세요.</p>' +
    '</form>';

  function mount() {
    document.body.appendChild(btn);
    document.body.appendChild(wrap);

    var card = wrap.querySelector('#fbCard');
    var text = wrap.querySelector('#fbText');
    var from = wrap.querySelector('#fbFrom');
    var hp = wrap.querySelector('#fbCompany');
    var msg = wrap.querySelector('#fbMsg');
    var send = wrap.querySelector('#fbSend');
    var count = wrap.querySelector('#fbCount');
    var lastFocus = null;

    function say(t, isErr) { msg.textContent = t; msg.className = 'fb-msg' + (isErr ? ' err' : ''); }

    function open() {
      lastFocus = document.activeElement;
      wrap.classList.add('on');
      say('');
      setTimeout(function () { text.focus(); }, 0);
    }
    function close() {
      wrap.classList.remove('on');
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    btn.addEventListener('click', open);
    wrap.querySelector('#fbCancel').addEventListener('click', close);
    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) close(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && wrap.classList.contains('on')) close();
    });
    text.addEventListener('input', function () { count.textContent = text.value.length + ' / ' + MAX; });
    // Ctrl/⌘+Enter 로도 전송
    text.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); card.requestSubmit(); }
    });

    card.addEventListener('submit', function (e) {
      e.preventDefault();
      var body = text.value.trim();
      if (body.length < 2) { say('의견을 한 줄만 적어주세요.', true); text.focus(); return; }
      send.disabled = true;
      say('보내는 중…');
      fetch('api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: body,
          email: from.value.trim(),
          company: hp.value,                       // 허니팟 — 사람은 비워둔다
          page: location.pathname + location.search
        })
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok) { say((res.d && res.d.error) || '전달하지 못했습니다. 잠시 후 다시 시도해 주세요.', true); send.disabled = false; return; }
          say('보내주셔서 고맙습니다. 잘 전달됐어요.');
          text.value = ''; from.value = ''; count.textContent = '0 / ' + MAX;
          setTimeout(function () { close(); send.disabled = false; }, 1200);
        })
        .catch(function () {
          say('네트워크 오류로 전달하지 못했습니다.', true);
          send.disabled = false;
        });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
