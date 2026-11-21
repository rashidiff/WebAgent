const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(text = 'Loading') {
  const events = new Map();
  let listener;
  let reads = 0;
  const body = { get innerText() { reads++; return text; } };
  const context = vm.createContext({
    console: { log() {} }, setTimeout, clearTimeout, Date,
    document: { body, title: 'Test', querySelectorAll: () => [] },
    window: {
      location: { href: 'https://example.test' },
      addEventListener: (name, fn) => events.set(name, fn),
      removeEventListener: (name) => events.delete(name),
    },
    chrome: { runtime: { onMessage: { addListener(fn) { listener = fn; } } } },
  });
  vm.runInContext(fs.readFileSync(require.resolve('../content.js'), 'utf8'), context);
  return {
    text(value) { text = value; },
    navigate() { events.get('pagehide')?.(); },
    get reads() { return reads; },
    get listeners() { return events.size; },
    send(request) {
      return new Promise(resolve => {
        assert.equal(listener(request, {}, resolve), true, 'async response channel stays open');
      });
    },
  };
}

test('text already present matches case-insensitively', async () => {
  const p = page('Task COMPLETED');
  assert.equal((await p.send({ type: 'execute_action', action: 'wait_for_text', value: 'completed' })).status, 'success');
  assert.equal(p.listeners, 0);
});

test('delayed text waits and replies once after completion', async () => {
  const p = page();
  let settled = false;
  const result = p.send({ type: 'execute_action', action: 'wait_for_text', value: 'Completed', timeout_ms: 2000 }).then(r => { settled = true; return r; });
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(settled, false);
  p.text('Completed');
  assert.equal((await result).status, 'success');
  assert.ok(p.reads <= 3, 'polling is not a busy loop');
  assert.equal(p.listeners, 0);
});

test('timeout is bounded and useful', async () => {
  const p = page();
  const start = Date.now();
  const result = await p.send({ type: 'execute_action', action: 'wait_for_text', value: 'Completed', timeout_ms: 1100 });
  assert.equal(result.status, 'error');
  assert.match(result.error, /timed out after 100 ms.*Completed/);
  assert.ok(Date.now() - start < 1000);
  assert.ok(p.reads <= 4);
  assert.equal(p.listeners, 0);
});

for (const reason of ['navigation', 'cancellation']) {
  test(`${reason} rejects pending wait and cleans up`, async () => {
    const p = page();
    const result = p.send({ type: 'execute_action', action: 'wait_for_text', value: 'Completed' });
    if (reason === 'navigation') p.navigate();
    else await p.send({ type: 'cancel_wait' });
    assert.match((await result).error, /cancelled/);
    assert.equal(p.listeners, 0);
    const reads = p.reads;
    await new Promise(resolve => setTimeout(resolve, 120));
    assert.equal(p.reads, reads, 'cancelled wait stops polling');
  });
}

test('empty expected text fails immediately', async () => {
  const p = page();
  assert.match((await p.send({ type: 'execute_action', action: 'wait_for_text', value: '' })).error, /non-empty/);
});
