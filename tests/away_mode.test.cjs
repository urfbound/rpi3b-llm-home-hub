const test = require('node:test');
const assert = require('node:assert/strict');
const away = require('../homehub/static/away-mode.js');

test('offline state is separated per user and list', () => {
  assert.notEqual(away.storageKey(1, 5), away.storageKey(2, 5));
  assert.notEqual(away.storageKey(1, 5), away.storageKey(1, 6));
});

test('clearing offline data removes only the selected account and active selection', () => {
  const values = new Map([
    ['homehub-away-last-user', '1'],
    ['homehub-away-last-list', '5'],
    ['homehub-away-v1:1:5', JSON.stringify({ items: { 9: { text: 'Milk' } } })],
    ['homehub-away-v1:1:6', JSON.stringify({ items: { 10: { text: 'Bread' } } })],
    ['homehub-away-v1:2:5', JSON.stringify({ items: { 11: { text: 'Tea' } } })],
  ]);
  const storage = {
    get length() { return values.size; },
    key(index) { return [...values.keys()][index] ?? null; },
    getItem(key) { return values.get(key) ?? null; },
    removeItem(key) { values.delete(key); },
  };

  away.clearUserData(1, storage);

  assert.equal(values.has('homehub-away-v1:1:5'), false);
  assert.equal(values.has('homehub-away-v1:1:6'), false);
  assert.equal(values.has('homehub-away-v1:2:5'), true);
  assert.equal(values.has('homehub-away-last-user'), false);
  assert.equal(values.has('homehub-away-last-list'), false);
});

test('initial snapshot request includes the selected list id', () => {
  assert.equal(away.snapshotUrl(5), '/shopping/sync?list_id=5');
});

test('checkbox changes queue desired state and cancel when returned to baseline', () => {
  const state = away.newState(1, { id: 5, title: 'Groceries' }, [
    { id: 9, text: 'Milk', done: false, updated_at: 'v1' },
  ]);

  away.queueCheckbox(state, 9, true);
  assert.deepEqual(state.pending['9'], {
    done: true, base_done: false, base_updated_at: 'v1',
  });
  away.queueCheckbox(state, 9, false);
  assert.equal(state.pending['9'], undefined);
});

test('sync conflicts keep the phone choice until the user resolves it', () => {
  const state = away.newState(1, { id: 5, title: 'Groceries' }, [
    { id: 9, text: 'Milk', done: false, updated_at: 'v1' },
  ]);
  away.queueCheckbox(state, 9, true);
  away.applySyncResults(state, {
    results: [{ id: 9, status: 'conflict', server_done: false, requested_done: true }],
    items: [{ id: 9, text: 'Milk', done: false, updated_at: 'v2' }],
  });

  assert.equal(state.pending['9'].status, 'conflict');
  assert.equal(state.pending['9'].requested_done, true);
  away.keepPhoneChoice(state, 9);
  assert.equal(state.pending['9'].status, undefined);
  assert.equal(state.pending['9'].base_updated_at, 'v2');
});

test('deleted items keep their pending state until explicitly discarded', () => {
  const state = away.newState(1, { id: 5, title: 'Groceries' }, [
    { id: 9, text: 'Milk', done: false, updated_at: 'v1' },
  ]);
  away.queueCheckbox(state, 9, true);
  away.applySyncResults(state, {
    results: [{ id: 9, status: 'deleted' }], items: [],
  });

  assert.equal(state.pending['9'].status, 'deleted');
  assert.equal(state.items['9'].text, 'Milk');
  away.resolveWithServer(state, 9);
  assert.equal(state.pending['9'], undefined);
});
