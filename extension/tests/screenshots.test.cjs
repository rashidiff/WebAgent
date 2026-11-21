const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const image = 'data:image/jpeg;base64,' + Buffer.from('synthetic image').toString('base64');

function panel({ retry = false, failure = false } = {}) {
  const sent = [];
  let captures = 0;
  let injections = 0;
  let attempts = 0;
  const node = { addEventListener() {}, classList: { add() {}, remove() {} }, appendChild() {}, focus() {} };
  class Socket {
    static OPEN = 1;
    readyState = 1;
    send(value) { sent.push(JSON.parse(value)); }
  }
  const chrome = {
    runtime: { lastError: null },
    tabs: {
      query: (query, cb) => cb([{ id: 1, windowId: 2 }]),
      captureVisibleTab: (id, options, cb) => { captures++; cb(image); },
      sendMessage(id, data, cb) {
        attempts++;
        if (retry && attempts === 1) {
          chrome.runtime.lastError = { message: 'Receiving end does not exist' };
          cb();
          chrome.runtime.lastError = null;
        } else cb(failure ? { status: 'error', error: 'synthetic failure' } : { status: 'success', dom_tree: [] });
      },
    },
    scripting: { executeScript(options, cb) { injections++; cb(); } },
  };
  const context = vm.createContext({ chrome, WebSocket: Socket, TextEncoder, console: { log() {}, warn() {}, error() {} }, setTimeout, clearTimeout,
    document: { getElementById: () => node, createElement: () => node } });
  const source = fs.readFileSync(require.resolve('../sidepanel.js'), 'utf8').split('// Event Listeners')[0];
  vm.runInContext(source, context);
  vm.runInContext('appendTimeline = () => {}; connectWS();', context);
  return { sent, context, get captures() { return captures; }, get injections() { return injections; },
    async execute(capture = true) {
      await vm.runInContext(`socket.onmessage({data: JSON.stringify({type:'agent_execute', action:'click', run_id:'r', action_id:'a', capability_token:'c', capture_screenshots:${capture}})})`, context);
      return sent.at(-1);
    },
  };
}

if (process.argv.includes('--payload')) {
  panel().execute().then(result => process.stdout.write(JSON.stringify(result)));
} else {
  for (const retry of [false, true]) for (const failure of [false, true]) {
    test(`images forwarded through WebSocket: retry=${retry}, failure=${failure}`, async () => {
      const p = panel({ retry, failure });
      const result = await p.execute();
      assert.equal(result.status, failure ? 'error' : 'success');
      assert.equal(result.screenshot_before, image);
      assert.equal(result.screenshot_after, image);
      assert.equal(result.run_id, 'r');
      assert.equal(p.injections, retry ? 1 : 0);
    });
  }
  test('capture disabled neither captures nor transmits', async () => {
    const p = panel();
    const result = await p.execute(false);
    assert.equal(p.captures, 0);
    assert.equal(result.screenshot_before, undefined);
    assert.equal(result.screenshot_after, undefined);
  });
  test('total UTF-8 limit drops images gracefully and bounds non-image payloads', () => {
    const p = panel();
    p.context.payload = { status: 'success', dom_tree: [], screenshot_before: 'data:image/jpeg;base64,' + 'A'.repeat(4000), screenshot_after: image };
    const serialize = () => vm.runInContext('serializeActionResult({run_id:"r", action_id:"a", capability_token:"c", capture_screenshots:true, max_result_bytes:4096}, payload)', p.context);
    let raw = serialize();
    assert.ok(Buffer.byteLength(raw) <= 4096);
    assert.equal(JSON.parse(raw).status, 'success');
    assert.equal(JSON.parse(raw).screenshot_before, undefined);
    assert.match(JSON.parse(raw).screenshot_warning, /total WebSocket/);
    p.context.payload.dom_tree = [{ text: 'متن'.repeat(5000) }];
    raw = serialize();
    assert.ok(Buffer.byteLength(raw) <= 4096);
    assert.equal(JSON.parse(raw).status, 'error');
  });
  test('malformed and schema-oversized images are omitted', () => {
    const p = panel();
    p.context.payload = { status: 'success', screenshot_before: 'javascript:bad', screenshot_after: 'data:image/jpeg;base64,' + 'A'.repeat(2000000) };
    const result = JSON.parse(vm.runInContext('serializeActionResult({capture_screenshots:true}, payload)', p.context));
    assert.equal(result.screenshot_before, undefined);
    assert.equal(result.screenshot_after, undefined);
    assert.match(result.screenshot_warning, /schema size/);
  });
}
