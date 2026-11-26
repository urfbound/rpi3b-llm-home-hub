const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
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

test('card movement controls identify direction without a font-dependent glyph', () => {
  assert.equal(shell.cardArrow(-1), 'up');
  assert.equal(shell.cardArrow(1), 'down');
});

test('app navigation arrows use drawn icons instead of Unicode arrow glyphs', () => {
  const templates = path.join(__dirname, '../homehub/templates');
  const files = fs.readdirSync(templates).filter(file => file.endsWith('.html'));
  const sources = [...files.map(file => fs.readFileSync(path.join(templates, file), 'utf8')),
    fs.readFileSync(path.join(__dirname, '../homehub/games.py'), 'utf8'),
    fs.readFileSync(path.join(__dirname, '../homehub/static/app-shell.js'), 'utf8')];
  const arrowGlyph = /[\u2190-\u21FF\u2B05-\u2B07\u27A1\u203A]/u;

  for (const source of sources) assert.doesNotMatch(source, arrowGlyph);
});

test('recipe search and filters keep only matching visible cards', () => {
  assert.equal(shell.recipeMatches('Lemon pasta', 'shared', 2, 1, 'LEMON', 'all'), true);
  assert.equal(shell.recipeMatches('Lemon pasta', 'shared', 2, 1, 'soup', 'all'), false);
  assert.equal(shell.recipeMatches('Lemon pasta', 'private', 2, 1, '', 'shared'), false);
  assert.equal(shell.recipeMatches('Lemon pasta', 'private', 2, 2, '', 'mine'), true);
  assert.equal(shell.recipeMatches('Lemon pasta', 'private', 2, 1, '', 'mine'), false);
});
