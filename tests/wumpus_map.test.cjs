const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('the occupied room stays centered when the cave map is rotated', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '../homehub/static/wumpus-map.js'), 'utf8');
  const listeners = {};
  const makeRoom = (number, point, current = false) => {
    const attributes = {};
    return {
      dataset: {room: String(number), x: String(point[0]), y: String(point[1]), z: String(point[2])},
      classList: {contains: name => current && name === 'current'},
      setAttribute: (name, value) => { attributes[name] = value; },
      getAttribute: name => attributes[name],
    };
  };
  const rooms = [
    makeRoom(4, [0.2, -0.5, 0.3]),
    makeRoom(9, [-0.6, 0.1, 0.7], true),
    makeRoom(12, [0.4, 0.8, -0.2]),
  ];
  const corridors = [{
    dataset: {a: '4', b: '9'},
    style: {},
    setAttribute() {},
  }];
  const svg = {
    querySelectorAll: selector => selector === '.wumpus-room' ? rooms : corridors,
    addEventListener: (name, listener) => { listeners[name] = listener; },
    appendChild() {},
  };
  const hideMap = {addEventListener() {}};
  const context = {
    document: {getElementById: id => id === 'wumpus-map' ? svg : hideMap},
    localStorage: {getItem: () => '0', setItem() {}},
  };
  const currentRoom = rooms[1];
  const assertCentered = () => {
    const match = currentRoom.getAttribute('transform').match(/^translate\(([-\d.]+) ([-\d.]+)\)/);
    assert.ok(match);
    assert.equal(Number(match[1]), 270);
    assert.equal(Number(match[2]), 270);
  };

  vm.runInNewContext(source, context);
  assertCentered();
  listeners.keydown({key: 'ArrowRight', preventDefault() {}});
  assertCentered();
});
