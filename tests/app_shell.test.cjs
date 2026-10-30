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
