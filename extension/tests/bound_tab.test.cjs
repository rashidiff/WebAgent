const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const crypto = require('node:crypto').webcrypto;

function panel() {
  const nodes = new Map();
  function node() {
    const handlers = new Map();
    return { value: '', classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      addEventListener(name, fn) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(fn); },
      removeEventListener(name, fn) { handlers.get(name)?.delete(fn); },
      click() { for (const fn of [...(handlers.get('click') || [])]) fn(); },
      focus() {}, setAttribute() {}, appendChild() {} };
  }
  const element = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const event = () => {
    const listeners = new Set();
    return { addListener: fn => listeners.add(fn), removeListener: fn => listeners.delete(fn), emit: (...args) => { for (const fn of [...listeners]) fn(...args); } };
  };
  const tabs = new Map([[1, { id: 1, windowId: 10, title: 'Tab A', url: 'https://a.test/' }], [2, { id: 2, windowId: 10, title: 'Tab B', url: 'https://b.test/' }]]);
  const pages = new Map([[1, { document_id: 'doc-a', url: 'https://a.test/' }], [2, { document_id: 'doc-b', url: 'https://b.test/' }]]);
  const actions = [], sent = [], captures = [], messages = [];
  let active = 1;
  let inaccessible = false;
  const chrome = { runtime: { lastError: null }, storage: { local: { get() {} }, session: { get() {} } }, scripting: { executeScript(_, cb) { cb(); } }, tabs: {
    onRemoved: event(), onUpdated: event(),
    query(_, cb) { cb([tabs.get(active)]); },
    get(id, cb) { cb(tabs.get(id)); },
    captureVisibleTab(windowId, _, cb) { captures.push(windowId); cb('data:image/jpeg;base64,AAAA'); },
    sendMessage(id, request, cb) {
      if (inaccessible) { chrome.runtime.lastError = { message: 'Cannot access target page' }; cb(); chrome.runtime.lastError = null; return; }
      if (request.type === 'execute_action' || request.type === 'get_page_text') actions.push([id, request.action || 'get_text']);
      cb({ status: 'success', dom_tree: [], page_text: { url: tabs.get(id)?.url }, page_state: pages.get(id) });
    },
    update(id, props, cb) {
      actions.push([id, 'navigate']);
      tabs.get(id).url = props.url;
      pages.set(id, { document_id: 'agent-navigation', url: props.url });
      chrome.tabs.onUpdated.emit(id, { status: 'loading', url: props.url });
      cb();
      chrome.tabs.onUpdated.emit(id, { status: 'complete' });
    },
  } };
  class Socket { static OPEN = 1; readyState = 1; send(value) { sent.push(JSON.parse(value)); } }
  const context = vm.createContext({ chrome, crypto, Uint8Array, TextEncoder, WebSocket: Socket,
    console: { log() {}, error() {}, warn() {} }, setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 5)), clearTimeout,
    document: { getElementById: element, createElement: node, body: node() } });
  vm.runInContext(fs.readFileSync(require.resolve('../sidepanel.js'), 'utf8'), context);
  context.messages = messages;
  vm.runInContext('appendMessage = (role, text) => messages.push(text); appendTimeline = () => {}; connectWS();', context);
  return { context, sent, actions, captures, element, tabs, pages, messages, chrome,
    switchTab() { active = 2; },
    inaccessible() { inaccessible = true; },
    async start() { element('prompt-input').value = 'test task'; await vm.runInContext('startSession()', context); },
    async packet(type = 'agent_execute', action = 'scroll', id = 'action-1') {
      context.packet = { type, action, value: action === 'navigate' ? 'https://destination.test/' : 'down', run_id: 'run-1', action_id: id, capability_token: 'cap' };
      await vm.runInContext('socket.onmessage({data: JSON.stringify(packet)})', context);
    },
  };
}

test('tab switch keeps scrolling, keyboard and text actions on starting tab', async () => {
  const p = panel(); await p.start(); p.switchTab();
  for (const action of ['scroll', 'key', 'get_text']) await p.packet('agent_execute', action);
  assert.deepEqual(p.actions, [[1, 'scroll'], [1, 'key'], [1, 'get_text']]);
  assert.match(p.element('target-tab').textContent, /Tab A.*window 10, tab 1/);
});

test('agent navigation stays bound and accepts the new document', async () => {
  const p = panel(); await p.start(); p.switchTab();
  await p.packet('agent_execute', 'navigate');
  await p.packet('agent_execute', 'scroll', 'next');
  assert.deepEqual(p.actions, [[1, 'navigate'], [1, 'scroll']]);
});

test('closing target immediately terminates and later execution does nothing', async () => {
  const p = panel(); await p.start();
  p.tabs.delete(1); p.chrome.tabs.onRemoved.emit(1);
  await p.packet();
  assert.deepEqual(p.actions, []);
  assert.ok(p.sent.some(value => value.type === 'stop_agent'));
  assert.ok(p.messages.some(value => /Target tab was closed/.test(value)));
  assert.equal(vm.runInContext('isAgentRunning', p.context), false);
});

test('inaccessible target produces a clear terminal state', async () => {
  const p = panel(); await p.start(); p.inaccessible(); await p.packet();
  assert.deepEqual(p.actions, []);
  assert.ok(p.messages.some(value => /Cannot access target page/.test(value)));
  assert.equal(vm.runInContext('isAgentRunning', p.context), false);
});

for (const change of ['url', 'reload', 'revision', 'window']) {
  test(`approval cannot execute after ${change} changes`, async () => {
    const p = panel(); await p.start();
    const pending = p.packet('agent_action');
    await new Promise(resolve => setImmediate(resolve));
    assert.match(p.element('approval-text').textContent, /Tab A.*https:\/\/a.test/);
    p.element('btn-approve').click(); await pending;
    if (change === 'url') p.pages.get(1).url = 'https://changed.test/';
    if (change === 'reload') p.pages.get(1).document_id = 'new-document';
    if (change === 'revision') p.chrome.tabs.onUpdated.emit(1, { status: 'loading' });
    if (change === 'window') p.tabs.get(1).windowId = 20;
    await p.packet();
    assert.deepEqual(p.actions, []);
    assert.equal(vm.runInContext('isAgentRunning', p.context), false);
  });
}

test('approval remains valid across an active-tab switch', async () => {
  const p = panel(); await p.start();
  const pending = p.packet('agent_action');
  await new Promise(resolve => setImmediate(resolve));
  p.switchTab(); p.element('btn-approve').click(); await pending;
  await p.packet();
  assert.deepEqual(p.actions, [[1, 'scroll']]);
});

test('background target does not capture the unrelated visible tab', async () => {
  const p = panel(); await p.start(); p.switchTab();
  assert.equal(await vm.runInContext('captureVisibleScreenshot({id:1, windowId:10})', p.context), null);
  assert.deepEqual(p.captures, []);
});

test('stop dismisses pending approval and late grant cannot execute', async () => {
  const p = panel(); await p.start();
  const pending = p.packet('agent_action');
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext('sendStopSignal()', p.context); await pending;
  await p.packet();
  assert.deepEqual(p.actions, []);
});

test('content script rejects navigation between validation and execution', () => {
  let listener;
  let scrolls = 0;
  const context = vm.createContext({ crypto, Uint8Array, setTimeout, clearTimeout, console: { log() {} },
    window: { location: { href: 'https://a.test/' }, scrollBy() { scrolls++; } },
    document: { title: 'A' }, chrome: { runtime: { onMessage: { addListener(fn) { listener = fn; } } } } });
  vm.runInContext(fs.readFileSync(require.resolve('../content.js'), 'utf8'), context);
  let state;
  listener({ type: 'get_page_state' }, {}, result => { state = result.page_state; });
  vm.runInContext('window.location.href = "https://changed.test/"', context);
  let result;
  listener({ type: 'execute_action', action: 'scroll', value: 'down', expected_page_state: state }, {}, value => { result = value; });
  assert.equal(result.status, 'error');
  assert.match(result.error, /page changed/);
  assert.equal(scrolls, 0);
});
