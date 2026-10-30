function restoreCardOrder(ids, saved) {
  const known = saved.filter(id => ids.includes(id));
  return [...new Set(known), ...ids.filter(id => !known.includes(id))];
}

function moveCard(ids, id, step) {
  const from = ids.indexOf(id), to = from + step;
  if (from < 0 || to < 0 || to >= ids.length) return ids;
  const next = [...ids];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

function shouldCollapseNav(available, nav) {
  return nav > available;
}

function navContentWidth(widths, gap) {
  return widths.reduce((sum, width) => sum + width, 0) + Math.max(0, widths.length - 1) * gap;
}

function cardLabel(id, heading) {
  return id === 'calendar' ? 'Calendar' : heading;
}

function previewColumns(display, gridTemplateColumns) {
  if (display !== 'grid' || !gridTemplateColumns || gridTemplateColumns === 'none') return 1;
  return Math.max(1, gridTemplateColumns.trim().split(/\s+/)
    .filter(track => parseFloat(track) !== 0).length);
}

function previewAbove(viewportHeight, triggerTop, triggerBottom, panelHeight) {
  const below = viewportHeight - triggerBottom;
  return below < panelHeight && triggerTop > below;
}

function cardArrow(step) {
  return step < 0 ? '\u2191' : '\u2193';
}

if (typeof module !== 'undefined') module.exports = {restoreCardOrder, moveCard, shouldCollapseNav, navContentWidth, cardLabel, previewColumns, previewAbove, cardArrow};

if (typeof document !== 'undefined') (() => {
  const menu = document.querySelector('#site-menu');
  const toggle = document.querySelector('.menu-toggle');
  const header = document.querySelector('header');
  const crumbTab = document.querySelector('#breadcrumb-tab');
  const crumbPage = document.querySelector('#breadcrumb-page');
  const main = document.querySelector('main[data-user-id]');
  if (!main || !main.dataset.userId) return;

  const returnKey = `homehub-return-section:${main.dataset.userId}`;
  try {
    const saved = JSON.parse(sessionStorage.getItem(returnKey) || 'null');
    sessionStorage.removeItem(returnKey);
    if (saved && Date.now() - saved.savedAt < 15000 && !location.hash) {
      const target = document.getElementById(saved.section);
      if (target) {
        history.replaceState(null, '', `${location.pathname}${location.search}#${encodeURIComponent(saved.section)}`);
        target.scrollIntoView();
      }
    }
  } catch (_) { sessionStorage.removeItem(returnKey); }
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post') return;
    const section = form.closest('section[id],article[id],[data-return-section]');
    if (section?.id) sessionStorage.setItem(returnKey, JSON.stringify({section: section.id, savedAt: Date.now()}));
  }, true);

  const activeTab = menu?.querySelector('[aria-current="page"]');
  const title = main.querySelector('h1');
  if (activeTab && crumbTab) {
    crumbTab.textContent = activeTab.textContent.trim();
    crumbTab.href = activeTab.href;
  }
  if (title && crumbPage && activeTab?.getAttribute('href') !== '/' &&
      title.textContent.trim() !== crumbTab?.textContent.trim()) {
    crumbPage.textContent = ` › ${title.textContent.trim()}`;
  }

  const key = `homehub-card-layout:${main.dataset.userId}:${location.pathname}`;
  let layout = {};
  try { layout = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) { layout = {}; }
  const cards = [...main.querySelectorAll('.panel')].filter(card =>
    !card.parentElement.closest('.panel') && card.closest('main') === main &&
    !card.querySelector(':scope > h1')
  );
  const cardId = card => card.id || card.dataset.cardKey;
  const parents = [...new Set(cards.map(card => card.parentElement))];
  const groupId = parent => parent.id || [...parent.classList].sort().join('.') || parent.tagName;
  const placeCards = (parent, siblings, ordered) => {
    const slots = siblings.map(card => {
      const slot = document.createComment('card position');
      parent.insertBefore(slot, card);
      return slot;
    });
    ordered.forEach((id, index) => {
      slots[index].replaceWith(siblings.find(card => cardId(card) === id));
    });
  };
  parents.forEach(parent => {
    const siblings = cards.filter(card => card.parentElement === parent);
    siblings.forEach(card => { card.dataset.cardKey = String(cards.indexOf(card)); });
    const ordered = restoreCardOrder(siblings.map(cardId), (layout.__orders || {})[groupId(parent)] || []);
    placeCards(parent, siblings, ordered);
  });
  cards.forEach(card => {
    card.classList.add('layout-card');
    const standalone = getComputedStyle(card.parentElement).display !== 'grid';
    if (standalone) card.classList.add('card-standalone');
    const width = layout[cardId(card)] || (standalone ? 'wide' : 'standard');
    card.classList.toggle('card-wide', width === 'wide');
  });
  cards.sort((a, b) =>
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1
  );

  if (cards.length) {
    const controls = document.createElement('div');
    controls.className = 'card-layout-controls';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'card-layout-trigger';
    trigger.textContent = 'Arrange cards';
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-controls', 'card-layout-popunder');
    const popunder = document.createElement('section');
    popunder.className = 'card-layout-popunder';
    popunder.id = 'card-layout-popunder';
    popunder.setAttribute('aria-label', 'Arrange cards');
    popunder.hidden = true;
    const positionPreview = () => {
      if (popunder.hidden) return;
      const rect = trigger.getBoundingClientRect();
      const above = previewAbove(window.innerHeight, rect.top, rect.bottom, popunder.scrollHeight);
      popunder.classList.toggle('above', above);
      const space = above ? rect.top : window.innerHeight - rect.bottom;
      popunder.style.maxHeight = `${Math.max(80, Math.min(680, space - 12))}px`;
    };
    const heading = document.createElement('div');
    heading.className = 'card-layout-popunder-heading';
    heading.innerHTML = '<strong>Page layout</strong><span>Tap a card to change its width. Use the arrows to move it.</span>';
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'card-layout-close';
    close.textContent = 'Close';
    heading.append(close);
    popunder.append(heading);
    controls.append(trigger, popunder);
    const rowByCard = new Map();
    const gridByParent = new Map();
    const updatePreview = () => {
      parents.forEach(parent => {
        const style = getComputedStyle(parent);
        const columns = previewColumns(style.display, style.gridTemplateColumns);
        gridByParent.get(parent).style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
      });
    };
    const updateArrows = () => {
      cards.forEach(card => {
        const siblings = [...card.parentElement.children].filter(item => cards.includes(item));
        const row = rowByCard.get(card);
        row.children[1].disabled = siblings[0] === card;
        row.children[2].disabled = siblings.at(-1) === card;
      });
    };
    parents.forEach(parent => {
      const preview = document.createElement('div');
      preview.className = 'card-layout-preview-grid';
      if (getComputedStyle(parent).display !== 'grid') preview.classList.add('card-layout-preview-stack');
      gridByParent.set(parent, preview);
      popunder.append(preview);
    });
    cards.forEach(card => {
      const label = cardLabel(card.id,
        card.querySelector('h2, h1')?.textContent.trim() || `Card ${card.dataset.cardKey}`);
      const row = document.createElement('div');
      row.className = 'card-layout-preview-card';
      row.dataset.cardKey = cardId(card);
      rowByCard.set(card, row);
      const width = document.createElement('button');
      width.type = 'button';
      const updateWidth = () => {
        const wide = card.classList.contains('card-wide');
        width.textContent = `${label}: ${wide ? 'Full width' : 'Standard width'}`;
        width.setAttribute('aria-pressed', String(wide));
        row.classList.toggle('card-wide', wide);
      };
      updateWidth();
      width.addEventListener('click', () => {
        const wide = card.classList.toggle('card-wide');
        layout[cardId(card)] = wide ? 'wide' : 'standard';
        localStorage.setItem(key, JSON.stringify(layout));
        updateWidth();
        updatePreview();
        positionPreview();
      });
      row.append(width);
      for (const [step, direction] of [[-1, 'up'], [1, 'down']]) {
        const move = document.createElement('button');
        move.type = 'button';
        move.className = 'card-order-arrow';
        move.textContent = cardArrow(step);
        move.setAttribute('aria-label', `Move ${label} ${direction}`);
        move.addEventListener('click', () => {
          const parent = card.parentElement;
          const siblings = [...parent.children].filter(item => cards.includes(item));
          const next = moveCard(siblings.map(cardId), cardId(card), step);
          placeCards(parent, siblings, next);
          placeCards(gridByParent.get(parent), siblings.map(item => rowByCard.get(item)), next);
          layout.__orders ||= {};
          layout.__orders[groupId(parent)] = next;
          localStorage.setItem(key, JSON.stringify(layout));
          updateArrows();
          updatePreview();
          positionPreview();
          move.focus();
        });
        row.append(move);
      }
      gridByParent.get(card.parentElement).append(row);
    });
    updateArrows();
    updatePreview();
    const hidePreview = () => {
      popunder.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
    };
    trigger.addEventListener('click', () => {
      popunder.hidden = !popunder.hidden;
      trigger.setAttribute('aria-expanded', String(!popunder.hidden));
      if (!popunder.hidden) { updatePreview(); positionPreview(); }
    });
    close.addEventListener('click', () => { hidePreview(); trigger.focus(); });
    document.addEventListener('click', event => {
      if (!controls.contains(event.target)) hidePreview();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !popunder.hidden) { hidePreview(); trigger.focus(); }
    });
    window.addEventListener('resize', () => { updatePreview(); positionPreview(); });
    window.addEventListener('scroll', positionPreview, {passive: true});
    main.append(controls);
  }

  if (!menu || !toggle || !header) return;
  const closeMenu = () => { menu.hidden = true; toggle.setAttribute('aria-expanded', 'false'); };
  const navWidth = navContentWidth(
    [...menu.querySelectorAll(':scope > a')].map(link => link.getBoundingClientRect().width),
    parseFloat(getComputedStyle(menu).columnGap) || 0
  );
  const updateNavigation = () => {
    const style = getComputedStyle(header);
    const available = header.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    const collapsed = shouldCollapseNav(available, navWidth);
    header.classList.toggle('nav-collapsed', collapsed);
    menu.hidden = collapsed;
    toggle.setAttribute('aria-expanded', 'false');
  };
  updateNavigation();
  window.addEventListener('resize', updateNavigation);
  toggle.addEventListener('click', () => {
    const open = menu.hidden;
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  });
  menu.addEventListener('click', event => {
    if (event.target.closest('a') && header.classList.contains('nav-collapsed')) closeMenu();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && header.classList.contains('nav-collapsed')) closeMenu();
  });
})();
