const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function staticRequest(network) {
  let onFetch;
  let saved;
  const cached = {source: 'old cache'};
  const context = {
    URL,
    self: {
      location: {origin: 'https://homehub.test'},
      addEventListener(name, callback) { if (name === 'fetch') onFetch = callback; },
    },
    caches: {
      match: async () => cached,
      open: async () => ({put: async (_request, response) => { saved = response; }}),
    },
    fetch: network,
  };
  const source = fs.readFileSync(path.join(__dirname, '../homehub/static/service-worker.js'), 'utf8');
  vm.runInNewContext(source, context);
  let reply;
  onFetch({
    request: {url: 'https://homehub.test/static/app-shell.js', method: 'GET', mode: 'cors'},
    respondWith(response) { reply = response; },
  });
  return {reply, cached, saved: () => saved};
}

test('static assets refresh online and use cache offline', async () => {
  const fresh = {ok: true, clone: () => ({source: 'new cache'})};
  const online = staticRequest(async () => fresh);
  assert.equal(await online.reply, fresh);
  assert.equal(online.saved().source, 'new cache');

  const offline = staticRequest(async () => { throw Error('offline'); });
  assert.equal(await offline.reply, offline.cached);
});
