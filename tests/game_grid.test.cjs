const test = require('node:test');
const assert = require('node:assert/strict');
const { installGridNavigation } = require('../homehub/static/game-grid.js');

function input() {
  const handlers = {};
  return {
    value: '',
    validity: { valid: true },
    handlers,
    addEventListener(name, handler) { handlers[name] = handler; },
    focus() { this.focused = true; },
  };
}

test('typing a grid character advances in document order', () => {
  const cells = [input(), input(), input()];
  installGridNavigation({ querySelectorAll: () => cells });

  cells[0].value = 'a';
  cells[0].handlers.input();

  assert.equal(cells[0].value, 'A');
  assert.equal(cells[1].focused, true);
});

test('backspace in an empty grid cell focuses the preceding editable cell', () => {
  const cells = [input(), input(), input()];
  installGridNavigation({ querySelectorAll: () => cells });
  let prevented = false;

  cells[2].handlers.keydown({
    key: 'Backspace',
    preventDefault() { prevented = true; },
  });

  assert.equal(cells[1].focused, true);
  assert.equal(prevented, true);
});

test('invalid characters do not advance to the next cell', () => {
  const cells = [input(), input()];
  cells[0].validity.valid = false;
  installGridNavigation({ querySelectorAll: () => cells });
  cells[0].value = '0';

  cells[0].handlers.input();

  assert.equal(cells[1].focused, undefined);
});
