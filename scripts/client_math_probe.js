/* 대조 검사 전용 — `web/assets/finmath.js`를 Node로 불러 표준입력의 케이스를 실행한다.

   손으로 옮겨 적은 참조 구현이 아니라 **브라우저가 실제로 받는 그 파일**을 require한다.
   그래야 finmath.js를 고치는 순간 파이썬과의 어긋남이 CI에 잡힌다(#84 · ADR-0019).
   브라우저는 이 파일을 읽지 않는다 — 부르는 쪽은 scripts/check_client_math.py 하나다.

   입력: [{fn: "bondMetrics", args: [...]}, …]   (JSON, stdin)
   출력: [{value: …} | {error: "…"}, …]           (JSON, stdout · 입력과 같은 순서) */
'use strict';

var M = require('../web/assets/finmath.js');

var buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', function (d) { buf += d; });
process.stdin.on('end', function () {
  var cases;
  try { cases = JSON.parse(buf); } catch (e) {
    process.stderr.write('입력 JSON을 읽지 못했습니다: ' + e.message + '\n');
    process.exit(2);
  }
  var out = cases.map(function (c) {
    if (typeof M[c.fn] !== 'function') return { error: 'finmath.js에 없는 이름: ' + c.fn };
    try { return { value: M[c.fn].apply(null, c.args) }; }
    catch (e) { return { error: String((e && e.message) || e) }; }
  });
  process.stdout.write(JSON.stringify(out));
});
