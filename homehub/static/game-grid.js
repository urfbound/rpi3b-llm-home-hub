(() => {
  function installGridNavigation(form) {
    if (!form) return;
    const cells = Array.from(form.querySelectorAll('input.puzzle-cell:not([disabled])'));
    cells.forEach((cell, index) => {
      cell.addEventListener('input', () => {
        cell.value = cell.value.toUpperCase();
        if (cell.value.length === 1 && cell.validity.valid && cells[index + 1]) {
          cells[index + 1].focus();
        }
      });
      cell.addEventListener('keydown', event => {
        if (event.key === 'Backspace' && !cell.value && cells[index - 1]) {
          event.preventDefault();
          cells[index - 1].focus();
        }
      });
    });
  }

  const api = { installGridNavigation };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.HomeHubGrid = api;

  if (typeof document !== 'undefined') {
    document.querySelectorAll('form[data-grid-navigation]').forEach(installGridNavigation);
  }
})();
