window.DQRMAN = window.DQRMAN || {};

(function initRouter(ns) {
  const tabs = [
    { id: 'map', label: 'Live Map' },
    { id: 'setup', label: 'Sim Setup' },
    { id: 'attacks', label: 'Attack Panel' },
    { id: 'events', label: 'Event Log' },
    { id: 'node', label: 'Node Detail' },
  ];

  function activate(tabId) {
    const screens = document.querySelectorAll('.screen');
    screens.forEach((screen) => {
      screen.classList.toggle('active', screen.dataset.screen === tabId);
    });

    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });

    ns.state.setTab(tabId);
  }

  function mountTabBar(container) {
    container.innerHTML = tabs
      .map((tab) => `<button class="tab-btn" data-tab="${tab.id}">${tab.label}</button>`)
      .join('');

    container.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => activate(btn.dataset.tab));
    });

    activate('map');
  }

  ns.router = { activate, mountTabBar };
})(window.DQRMAN);
