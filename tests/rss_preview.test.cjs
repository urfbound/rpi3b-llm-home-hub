const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const preview = fs.readFileSync(path.join(__dirname, '../docs/design/rss-preview.html'), 'utf8');
const script = preview.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'RSS preview has an inline script');

function setup() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        value: '', textContent: '', checked: false, hidden: false, open: false,
        showModal() { this.open = true; },
        close() { this.open = false; },
      });
    }
    return elements.get(id);
  }
  element('account').value = 'personal';
  const context = vm.createContext({ document: { getElementById: element }, URL, alert() {} });
  vm.runInContext(script.replace(/renderFeeds\(\);\s*$/, ''), context);
  vm.runInContext('renderFeeds = () => {}; renderManage = () => {};', context);
  return { element, run: expression => vm.runInContext(expression, context) };
}

test('personal feeds reject house-wide and own duplicate URLs', () => {
  const app = setup();
  const submit = () => app.run('addFeed({preventDefault(){}})');
  const initial = app.run('personalFeeds.length');
  app.element('feed-url').value = 'https://LIBRARY.example.org:443/feed.xml#latest';
  submit();
  assert.equal(app.run('personalFeeds.length'), initial);
  assert.match(app.element('form-feedback').textContent, /house-wide/);

  app.element('feed-url').value = 'https://garden.example.org/feed.xml';
  submit();
  assert.equal(app.run('personalFeeds.length'), initial);
  assert.match(app.element('form-feedback').textContent, /your feeds/);

  app.element('feed-url').value = 'https://library.example.org/books.xml';
  submit();
  assert.equal(app.run('personalFeeds.length'), initial + 1);
});

test('Done reading repeats Keep until prompting is restored', () => {
  const app = setup();
  const first = 'personal|personal|garden.example.org|Planning a small autumn herb garden';
  const second = 'personal|personal|science.example.org|What to spot in the October sky';
  const third = 'personal|personal|science.example.org|Why autumn leaves change colour';
  app.run(`doneReading(${JSON.stringify(first)})`);
  assert.equal(app.element('done-dialog').open, true);
  app.element('skip-done-prompt').checked = true;
  app.run('closeDone(false)');
  assert.equal(app.run('doneChoices.get("personal")'), 'keep');

  app.run(`doneReading(${JSON.stringify(second)})`);
  assert.equal(app.element('done-dialog').open, false);
  assert.equal(app.run(`stateFor(${JSON.stringify(second)}).read`), true);
  assert.equal(app.run(`stateFor(${JSON.stringify(second)}).deleted`), false);

  app.run('setDonePrompt(true)');
  app.run(`doneReading(${JSON.stringify(third)})`);
  assert.equal(app.element('done-dialog').open, true);
});

test('Done reading repeats Delete but still confirms favourites', () => {
  const app = setup();
  const first = 'personal|personal|garden.example.org|Planning a small autumn herb garden';
  const second = 'personal|personal|science.example.org|What to spot in the October sky';
  const favourite = 'personal|personal|garden.example.org|Putting the beds to rest';
  app.run(`doneReading(${JSON.stringify(first)})`);
  app.element('skip-done-prompt').checked = true;
  app.run('closeDone(true)');
  assert.equal(app.run('doneChoices.get("personal")'), 'delete');
  assert.equal(app.run(`stateFor(${JSON.stringify(first)}).deleted`), true);

  app.run(`doneReading(${JSON.stringify(second)})`);
  assert.equal(app.element('done-dialog').open, false);
  assert.equal(app.run(`stateFor(${JSON.stringify(second)}).deleted`), true);

  app.run(`doneReading(${JSON.stringify(favourite)})`);
  assert.equal(app.element('delete-dialog').open, true);
  assert.equal(app.run(`stateFor(${JSON.stringify(favourite)}).deleted`), false);
  app.run('closeDelete(false)');
  assert.equal(app.run(`stateFor(${JSON.stringify(favourite)}).deleted`), false);
});

test('opening an article marks it read without a deletion prompt', () => {
  const app = setup();
  const key = 'personal|personal|garden.example.org|Planning a small autumn herb garden';
  let prevented = false;
  app.element('link-event').preventDefault = () => { prevented = true; };
  app.run(`visitArticle(document.getElementById('link-event'), ${JSON.stringify(key)})`);
  assert.equal(prevented, true);
  assert.equal(app.run(`stateFor(${JSON.stringify(key)}).read`), true);
  assert.equal(app.element('done-dialog').open, false);
});
