const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('the occupied room and its neighbors stay together on initial render and rotation', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '../homehub/static/wumpus-map.js'), 'utf8');
  const listeners = {};
  const paintOrder = [];
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
    makeRoom(18, [-0.382, 1, 0]),
    makeRoom(19, [0.382, 1, 0], true),
    makeRoom(20, [0.618, 0.618, 0.618]),
  ];
  const corridors = [
    {dataset: {a: '18', b: '19'}, style: {}, setAttribute() {}},
    {dataset: {a: '19', b: '20'}, style: {}, setAttribute() {}},
  ];
  const svg = {
    querySelectorAll: selector => selector === '.wumpus-room' ? rooms : corridors,
    addEventListener: (name, listener) => { listeners[name] = listener; },
    appendChild: element => { paintOrder.push(element); },
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
    for (const neighbor of [rooms[0], rooms[2]]) {
      const position = neighbor.getAttribute('transform').match(/^translate\(([-\d.]+) ([-\d.]+)\)/);
      assert.ok(position);
      assert.ok(Math.hypot(Number(position[1]) - 270, Number(position[2]) - 270) < 180,
        `room ${neighbor.dataset.room} should stay near the occupied room`);
    }
  };

  vm.runInNewContext(source, context);
  assertCentered();
  assert.equal(paintOrder[paintOrder.length - 1], currentRoom);
  listeners.keydown({key: 'ArrowRight', preventDefault() {}});
  assertCentered();
  assert.equal(paintOrder[paintOrder.length - 1], currentRoom);
});
