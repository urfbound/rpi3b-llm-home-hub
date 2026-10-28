(() => {
  const menu = document.querySelector('#site-menu');
  const toggle = document.querySelector('.menu-toggle');
  const crumbTab = document.querySelector('#breadcrumb-tab');
  const crumbPage = document.querySelector('#breadcrumb-page');
  const main = document.querySelector('main[data-user-id]');
  if (!main) return;

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
  try { layout = JSON.parse(localStorage.getItem(key) || '{}'); } catch (_) { layout = {}; }
  const cards = [...main.querySelectorAll('.panel')].filter(card =>
    !card.parentElement.closest('.panel') && card.closest('main') === main
  );
  const parents = [...new Set(cards.map(card => card.parentElement))];
  const groupId = parent => parent.id || [...parent.classList].sort().join('.') || parent.tagName;
  parents.forEach(parent => {
    const siblings = cards.filter(card => card.parentElement === parent);
    if (siblings.length < 2) return;
    const orderedIds = (layout.__orders || {})[groupId(parent)] || [];
    siblings.sort((a, b) => {
      const aId = a.id || a.dataset.cardIndex;
      const bId = b.id || b.dataset.cardIndex;
      const ai = orderedIds.indexOf(aId), bi = orderedIds.indexOf(bId);
      return (ai < 0 ? Number(a.dataset.cardIndex) + orderedIds.length : ai) -
        (bi < 0 ? Number(b.dataset.cardIndex) + orderedIds.length : bi);
    }).forEach(card => parent.append(card));
  });
  cards.forEach((card, index) => {
    card.dataset.cardIndex = String(index);
    if (layout[card.id || card.dataset.cardIndex] === 'wide') card.classList.add('card-wide');
  });

  if (!menu || !toggle) return;
  const controls = document.createElement('section');
  controls.className = 'menu-card-controls';
  controls.innerHTML = '<h2>Arrange cards</h2><p>Choose how much room each card uses.</p>';
  cards.forEach(card => {
    const heading = card.querySelector('h2, h1');
    const label = heading?.textContent.trim() || `Card ${Number(card.dataset.cardIndex) + 1}`;
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${label}: ${card.classList.contains('card-wide') ? 'Full width' : 'Standard width'}`;
    button.setAttribute('aria-pressed', String(card.classList.contains('card-wide')));
    button.addEventListener('click', () => {
      const wide = card.classList.toggle('card-wide');
      layout[card.id || card.dataset.cardIndex] = wide ? 'wide' : 'standard';
      localStorage.setItem(key, JSON.stringify(layout));
      button.textContent = `${label}: ${wide ? 'Full width' : 'Standard width'}`;
      button.setAttribute('aria-pressed', String(wide));
    });
    controls.append(button);
    const siblings = cards.filter(candidate => candidate.parentElement === card.parentElement);
    if (siblings.length > 1) {
      const moveRow = document.createElement('div');
      moveRow.className = 'card-move-controls';
      ['Up', 'Down'].forEach(direction => {
        const move = document.createElement('button');
        move.type = 'button';
        move.textContent = `${direction}: ${label}`;
        move.addEventListener('click', () => {
          const position = siblings.indexOf(card);
          const target = position + (direction === 'Up' ? -1 : 1);
          if (target < 0 || target >= siblings.length) return;
          [siblings[position], siblings[target]] = [siblings[target], siblings[position]];
          siblings.forEach(item => card.parentElement.append(item));
          layout.__orders ||= {};
          layout.__orders[groupId(card.parentElement)] = siblings.map(item => item.id || item.dataset.cardIndex);
          localStorage.setItem(key, JSON.stringify(layout));
        });
        moveRow.append(move);
      });
      controls.append(moveRow);
    }
  });
  menu.append(controls);

  const closeMenu = () => { menu.hidden = true; toggle.setAttribute('aria-expanded', 'false'); };
  toggle.addEventListener('click', () => {
    const open = menu.hidden;
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  });
  menu.addEventListener('click', event => {
    if (event.target.closest('a')) closeMenu();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenu(); });
})();
