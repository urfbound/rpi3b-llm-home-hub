(() => {
  const svg = document.getElementById('wumpus-map');
  if (!svg) return;
  const hideMap = document.getElementById('hide-wumpus-map');
  try {
    hideMap.checked = localStorage.getItem('homehub-wumpus-map-hidden') === '1';
  } catch (_) { /* Storage can be unavailable; the checkbox still works. */ }
  hideMap.addEventListener('change', () => {
    try {
      localStorage.setItem('homehub-wumpus-map-hidden', hideMap.checked ? '1' : '0');
    } catch (_) { /* Keep the current page usable without storage. */ }
  });

  const rooms = [...svg.querySelectorAll('.wumpus-room')].map(element => ({
    element,
    number: Number(element.dataset.room),
    x: Number(element.dataset.x),
    y: Number(element.dataset.y),
    z: Number(element.dataset.z),
  }));
  const corridors = [...svg.querySelectorAll('.wumpus-corridor')];
  let yaw = 0;
  let pitch = 0;
  let drag = null;

  function render() {
    const positions = new Map();
    for (const room of rooms) {
      const x = room.x * Math.cos(yaw) + room.z * Math.sin(yaw);
      const z = room.z * Math.cos(yaw) - room.x * Math.sin(yaw);
      const y = room.y * Math.cos(pitch) - z * Math.sin(pitch);
      const depth = room.y * Math.sin(pitch) + z * Math.cos(pitch);
      const scale = 185 * (1 + depth * .16);
      const point = {x: 270 + x * scale, y: 270 - y * scale, depth};
      positions.set(room.number, point);
      room.element.setAttribute('transform', `translate(${point.x} ${point.y}) scale(${1 + depth * .12})`);
    }
    for (const line of corridors) {
      const a = positions.get(Number(line.dataset.a));
      const b = positions.get(Number(line.dataset.b));
      line.setAttribute('x1', a.x);
      line.setAttribute('y1', a.y);
      line.setAttribute('x2', b.x);
      line.setAttribute('y2', b.y);
      line.style.opacity = .35 + (a.depth + b.depth + 2) * .13;
    }
    rooms.sort((a, b) => positions.get(a.number).depth - positions.get(b.number).depth);
    for (const room of rooms) svg.appendChild(room.element);
  }

  svg.addEventListener('pointerdown', event => {
    event.preventDefault();
    drag = {x: event.clientX, y: event.clientY};
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener('pointermove', event => {
    if (!drag) return;
    yaw += (event.clientX - drag.x) * .008;
    pitch = Math.max(-1.4, Math.min(1.4, pitch + (event.clientY - drag.y) * .008));
    drag = {x: event.clientX, y: event.clientY};
    render();
  });
  svg.addEventListener('pointerup', () => { drag = null; });
  svg.addEventListener('pointercancel', () => { drag = null; });
  svg.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    yaw += event.key === 'ArrowLeft' ? -.12 : event.key === 'ArrowRight' ? .12 : 0;
    pitch = Math.max(-1.4, Math.min(1.4,
      pitch + (event.key === 'ArrowUp' ? -.12 : event.key === 'ArrowDown' ? .12 : 0)));
    render();
  });
  render();
})();
