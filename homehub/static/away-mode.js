(function (global) {
  'use strict';

  const PREFIX = 'homehub-away-v1';
  const ACTIVE_USER_KEY = 'homehub-away-last-user';
  const ACTIVE_LIST_KEY = 'homehub-away-last-list';

  function storageKey(userId, listId) {
    return `${PREFIX}:${userId}:${listId}`;
  }

  function snapshotUrl(listId) {
    return `/shopping/sync?list_id=${encodeURIComponent(listId)}`;
  }

  function newState(userId, list, items) {
    const byId = {};
    for (const item of items) byId[String(item.id)] = { ...item };
    return { user_id: String(userId), list_id: String(list.id), title: list.title,
      items: byId, pending: {} };
  }

  function queueCheckbox(state, itemId, done) {
    const key = String(itemId);
    const item = state.items[key];
    if (!item || typeof done !== 'boolean') return state;
    const previous = state.pending[key];
    if (previous) {
      previous.done = done;
      if (done === previous.base_done && previous.status !== 'deleted') {
        delete state.pending[key];
      }
    } else if (done !== Boolean(item.done)) {
      state.pending[key] = { done, base_done: Boolean(item.done),
        base_updated_at: item.updated_at || null };
    }
    item.done = done;
    return state;
  }

  function applySyncResults(state, data) {
    const previousItems = state.items;
    const fresh = {};
    for (const item of data.items || []) fresh[String(item.id)] = { ...item };
    state.items = fresh;
    for (const result of data.results || []) {
      const key = String(result.id);
      const pending = state.pending[key];
      if (!pending) continue;
      if (result.status === 'updated' || result.status === 'unchanged') {
        delete state.pending[key];
      } else if (result.status === 'conflict') {
        pending.status = 'conflict';
        pending.server_done = Boolean(result.server_done);
        pending.requested_done = Boolean(result.requested_done);
      } else if (result.status === 'deleted') {
        pending.status = 'deleted';
        if (previousItems[key]) state.items[key] = previousItems[key];
        if (state.items[key]) state.items[key].done = pending.done;
      }
    }
    for (const [key, pending] of Object.entries(state.pending)) {
      if (!state.items[key] && previousItems[key]) state.items[key] = previousItems[key];
      if (state.items[key] && pending.status !== 'conflict') state.items[key].done = pending.done;
    }
    return state;
  }

  function resolveWithServer(state, itemId) {
    delete state.pending[String(itemId)];
    return state;
  }

  function keepPhoneChoice(state, itemId) {
    const key = String(itemId);
    const pending = state.pending[key];
    const item = state.items[key];
    if (!pending || !item || pending.status === 'deleted') return state;
    pending.base_done = Boolean(item.done);
    pending.base_updated_at = item.updated_at || null;
    delete pending.status;
    delete pending.server_done;
    delete pending.requested_done;
    item.done = pending.done;
    return state;
  }

  const API = { storageKey, snapshotUrl, newState, queueCheckbox, applySyncResults,
    resolveWithServer, keepPhoneChoice };
  global.HomeHubAway = API;
  if (typeof module !== 'undefined' && module.exports) module.exports = API;

  if (!global.document) return;

  function readStored(key) {
    try {
      const raw = global.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (_error) {
      return null;
    }
  }

  function writeStored(key, value) {
    try {
      global.localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function itemFromElement(element) {
    return { id: Number(element.dataset.awayId), text: element.dataset.awayText,
      done: element.dataset.awayDone === 'true',
      updated_at: element.dataset.awayUpdatedAt || null };
  }

  function button(text, action, label) {
    const control = global.document.createElement('button');
    control.type = 'button';
    control.textContent = text;
    if (label) control.setAttribute('aria-label', label);
    control.addEventListener('click', action);
    return control;
  }

  function renderItem(element, item, pending, onResolve) {
    if (!item) return;
    const form = element.querySelector('[data-away-toggle]');
    const toggle = form && form.querySelector('button');
    const text = form && form.querySelector('span');
    if (toggle) {
      toggle.textContent = item.done ? '✓' : '◯';
      toggle.setAttribute('aria-label', item.done ? 'Mark incomplete' : 'Mark complete');
      toggle.disabled = Boolean(pending && pending.status === 'deleted');
    }
    if (text) {
      text.textContent = item.text;
      text.classList.toggle('done', Boolean(item.done));
    }
    const oldMessage = element.querySelector('[data-away-item-message]');
    if (oldMessage) oldMessage.remove();
    if (!pending) return;
    const message = global.document.createElement('div');
    message.className = 'away-item-message';
    message.dataset.awayItemMessage = '';
    if (pending.status === 'conflict') {
      const description = global.document.createElement('span');
      description.textContent = `Changed on the hub: ${pending.server_done ? 'complete' : 'not complete'}. Phone choice: ${pending.done ? 'complete' : 'not complete'}.`;
      message.append(description,
        button('Use hub', () => onResolve('server', item.id), 'Use the hub state'),
        button('Keep phone', () => onResolve('phone', item.id), 'Keep the phone state'));
    } else if (pending.status === 'deleted') {
      const description = global.document.createElement('span');
      description.textContent = 'This item was deleted on the hub.';
      message.append(description, button('Discard change', () => onResolve('discard', item.id)));
    } else {
      const description = global.document.createElement('span');
      description.textContent = 'Waiting to sync';
      message.append(description);
    }
    element.append(message);
  }

  function bindOfflinePage() {
    const page = global.document.querySelector('[data-away-offline]');
    if (!page) return false;
    const userId = global.localStorage.getItem(ACTIVE_USER_KEY);
    const listId = global.localStorage.getItem(ACTIVE_LIST_KEY);
    const key = userId && listId ? storageKey(userId, listId) : null;
    const state = key && readStored(key);
    const message = page.querySelector('[data-offline-message]');
    const list = page.querySelector('[data-offline-items]');
    if (!state || !list) {
      message.textContent = 'No shopping list has been saved on this phone yet. Open a list while connected to prepare it for away mode.';
      return true;
    }
    page.querySelector('h1').textContent = state.title;
    list.replaceChildren();
    message.textContent = Object.keys(state.pending).length
      ? `${Object.keys(state.pending).length} change(s) are waiting to sync after you reconnect and sign in.`
      : 'Saved on this phone. Checkbox changes will sync after you reconnect and sign in.';
    for (const item of Object.values(state.items)) {
      const li = global.document.createElement('li');
      const control = button(item.done ? '✓' : '◯', () => {
        API.queueCheckbox(state, item.id, !item.done);
        writeStored(key, state);
        bindOfflinePage();
      }, `${item.done ? 'Uncheck' : 'Check'} ${item.text}`);
      control.disabled = Boolean(state.pending[String(item.id)]
        && state.pending[String(item.id)].status === 'deleted');
      const span = global.document.createElement('span');
      span.textContent = item.text;
      span.classList.toggle('done', Boolean(item.done));
      li.append(control, span);
      if (state.pending[String(item.id)]) {
        const pending = global.document.createElement('small');
        pending.className = 'away-pending-label';
        pending.textContent = state.pending[String(item.id)].status === 'deleted'
          ? 'Deleted on the hub; review after reconnect' : 'Waiting to sync';
        li.append(pending);
      }
      list.append(li);
    }
    return true;
  }

  function startListPage() {
    const root = global.document.querySelector('#shopping[data-away-list-id]');
    const userMeta = global.document.querySelector('meta[name="homehub-user-id"]');
    if (!root || !userMeta) return;
    const userId = userMeta.content;
    const list = { id: root.dataset.awayListId, title: root.dataset.awayListTitle };
    const itemElements = [...root.querySelectorAll('[data-away-item]')];
    const serverItems = itemElements.map(itemFromElement);
    const key = storageKey(userId, list.id);
    const saved = readStored(key);
    const state = saved || newState(userId, list, serverItems);
    const previousItems = state.items || {};
    state.user_id = String(userId);
    state.list_id = String(list.id);
    state.title = list.title;
    state.items = Object.fromEntries(serverItems.map(item => [String(item.id), item]));
    for (const [id, pending] of Object.entries(state.pending || {})) {
      if (!state.items[id] && previousItems[id]) {
        state.items[id] = previousItems[id];
        pending.status = 'deleted';
      } else if (state.items[id] && pending.status !== 'conflict') {
        state.items[id].done = pending.done;
      }
    }
    global.localStorage.setItem(ACTIVE_USER_KEY, String(userId));
    global.localStorage.setItem(ACTIVE_LIST_KEY, String(list.id));
    writeStored(key, state);

    const message = root.querySelector('[data-away-message]');
    const loginLink = root.querySelector('[data-away-login]');
    const syncButton = root.querySelector('[data-away-sync]');
    let busy = false;

    function render(statusText) {
      const pendingCount = Object.keys(state.pending).length;
      root.querySelectorAll('[data-away-orphan]').forEach(note => note.remove());
      if (message) message.textContent = statusText || (pendingCount
        ? `${pendingCount} change(s) saved on this phone; checking for the hub…`
        : 'This list is saved on this phone for away mode.');
      if (loginLink && !statusText?.startsWith('Sign in to sync')) loginLink.hidden = true;
      if (syncButton) syncButton.hidden = pendingCount === 0;
      for (const element of itemElements) {
        const itemId = element.dataset.awayId;
        const item = state.items[itemId];
        const pending = state.pending[itemId];
        renderItem(element, item, pending, (choice, id) => {
          if (choice === 'server' || choice === 'discard') API.resolveWithServer(state, id);
          else API.keepPhoneChoice(state, id);
          writeStored(key, state);
          render();
          if (choice === 'phone') sync();
        });
      }
      for (const [id, pending] of Object.entries(state.pending)) {
        if (itemElements.some(element => element.dataset.awayId === id)) continue;
        const item = state.items[id];
        const note = global.document.createElement('p');
        note.className = 'away-item-message';
        note.dataset.awayOrphan = '';
        if (pending.status === 'deleted') {
          note.append(global.document.createTextNode(`${item ? item.text : 'A saved item'} was deleted on the hub. `),
            button('Discard change', () => {
              API.resolveWithServer(state, id);
              writeStored(key, state);
              render();
            }));
        } else {
          note.textContent = `${item ? item.text : 'A saved item'} is awaiting sync.`;
        }
        if (syncButton) syncButton.before(note);
      }
      writeStored(key, state);
    }

    async function sync() {
      if (busy) return;
      busy = true;
      const changes = Object.entries(state.pending)
        .filter(([, pending]) => pending.status !== 'deleted')
        .map(([id, pending]) => ({ id: Number(id), done: pending.done,
          base_done: pending.base_done, base_updated_at: pending.base_updated_at }));
      try {
        const response = await global.fetch(changes.length ? '/shopping/sync' : API.snapshotUrl(list.id), {
          method: changes.length ? 'POST' : 'GET',
          credentials: 'same-origin',
          headers: changes.length ? {
            'Content-Type': 'application/json',
            'X-CSRF-Token': global.document.querySelector('meta[name="homehub-csrf"]').content,
          } : {},
          body: changes.length ? JSON.stringify({ list_id: Number(list.id), changes }) : undefined,
        });
        if (response.redirected) {
          render('Sign in to sync. Your pending changes are saved on this phone.');
          if (loginLink) loginLink.hidden = false;
        } else if (response.status === 403 || response.status === 404) {
          render('This account no longer has access to the list. Pending changes remain on this phone.');
        } else if (!response.ok) {
          render('The hub could not sync this list. Pending changes remain saved on this phone.');
        } else {
          if (loginLink) loginLink.hidden = true;
          const data = await response.json();
          API.applySyncResults(state, data);
          render(Object.keys(state.pending).length
            ? 'Review the changes below before resolving them.'
            : 'List synced with the hub.');
        }
      } catch (_error) {
        render(`Hub unavailable. ${Object.keys(state.pending).length} change(s) are saved on this phone.`);
      } finally {
        busy = false;
      }
    }

    for (const element of itemElements) {
      const form = element.querySelector('[data-away-toggle]');
      if (!form) continue;
      form.addEventListener('submit', event => {
        event.preventDefault();
        const id = element.dataset.awayId;
        const current = state.items[id];
        API.queueCheckbox(state, id, !current.done);
        writeStored(key, state);
        render();
        sync();
      });
    }
    if (syncButton) syncButton.addEventListener('click', sync);
    render();
    sync();
    global.addEventListener('online', sync);
    global.addEventListener('focus', sync);
    global.setInterval(sync, 60000);
  }

  if ('serviceWorker' in global.navigator) {
    global.addEventListener('load', () => global.navigator.serviceWorker.register('/service-worker.js').catch(() => {}));
  }
  if (!bindOfflinePage()) startListPage();
})(typeof window === 'undefined' ? globalThis : window);
