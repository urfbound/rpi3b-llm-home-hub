const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../homehub/templates/rss.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'RSS page has an inline script');

function setup(blocked = false) {
  let click, interval, opened, focused = 0, blurred = 0, submitted = 0, prevented = 0;
  const popup = {closed: false, opener: {}, blur() { blurred++; }};
  const form = {requestSubmit() { submitted++; }};
  const link = {
    href: '/rss/articles/4/open',
    addEventListener(type, callback) { if (type === 'click') click = callback; },
    closest() { return {querySelector() { return form; }}; },
  };
  const document = {
    getElementById() { return {close() {}, showModal() {}}; },
    querySelectorAll(selector) { return selector.includes('/rss/articles/') ? [link] : []; },
    querySelector() { return {addEventListener() {}}; },
  };
  const window = {
    screen: {availWidth: 1000, availHeight: 800, availLeft: 20, availTop: 10},
    open(...args) { opened = args; return blocked ? null : popup; },
    focus() { focused++; },
    setInterval(callback) { interval = callback; return 1; },
    clearInterval() {},
  };
  vm.runInNewContext(script, {document, window});
  return {
    click() { click({preventDefault() { prevented++; }}); },
    closePopup() { popup.closed = true; interval(); },
    state() { return {opened, focused, blurred, submitted, prevented, opener: popup.opener}; },
  };
}

test('RSS opens a near-full-screen separate window behind the shelf', () => {
  const app = setup();
  app.click();
  const state = app.state();
  assert.equal(state.opened[0], '/rss/articles/4/open');
  assert.match(state.opened[2], /popup=yes,width=940,height=736,left=50,top=42/);
  assert.equal(state.focused, 1);
  assert.equal(state.blurred, 1);
  assert.equal(state.opener, null);
  assert.equal(state.prevented, 1);
  app.closePopup();
  assert.equal(app.state().submitted, 1);
});

test('RSS link falls back to normal navigation when a popup is blocked', () => {
  const app = setup(true);
  app.click();
  assert.equal(app.state().prevented, 0);
});
