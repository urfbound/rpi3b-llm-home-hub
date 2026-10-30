const test = require('node:test');
const assert = require('node:assert/strict');
const shell = require('../homehub/static/app-shell.js');

test('saved card positions restore in each group and keep new cards', () => {
  assert.equal(shell.cardLabel('calendar', 'October 2026'), 'Calendar');
  assert.deepEqual(
    shell.restoreCardOrder(['calendar', 'weather', 'rss'], ['rss', 'calendar']),
    ['rss', 'calendar', 'weather'],
  );
  assert.deepEqual(
    shell.moveCard(['calendar', 'weather', 'rss'], 'weather', -1),
    ['weather', 'calendar', 'rss'],
  );
});

test('navigation collapses when tabs would overflow available header width', () => {
  assert.equal(shell.navContentWidth([90, 100, 85], 4), 283);
  assert.equal(shell.shouldCollapseNav(650, 390), false);
  assert.equal(shell.shouldCollapseNav(650, 660), true);
});

test('card preview uses the live page grid column count', () => {
  assert.equal(shell.previewColumns('grid', '540px 540px'), 2);
  assert.equal(shell.previewColumns('grid', '430px 430px 0px'), 2);
  assert.equal(shell.previewColumns('grid', '430px'), 1);
  assert.equal(shell.previewColumns('block', 'none'), 1);
  const source = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '../homehub/static/app-shell.js'), 'utf8');
  assert.match(source, /card-layout-popunder/);
  assert.doesNotMatch(source, /createElement\('details'\)/);
});

test('card preview opens above the trigger when space below is short', () => {
  assert.equal(shell.previewAbove(530, 450, 500, 220), true);
  assert.equal(shell.previewAbove(900, 180, 225, 220), false);
});

test('both card movement controls render as single arrow characters', () => {
  assert.equal(shell.cardArrow(-1), '\u2191');
  assert.equal(shell.cardArrow(1), '\u2193');
  assert.equal([...shell.cardArrow(-1)].length, 1);
  assert.equal([...shell.cardArrow(1)].length, 1);
});
