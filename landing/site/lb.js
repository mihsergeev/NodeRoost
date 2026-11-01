(function () {
  'use strict';

  var ru = document.documentElement.lang === 'ru';
  var themeButton = document.querySelector('[data-theme-toggle]');
  var systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  var themeKey = 'noderoost-site-theme';

  function currentTheme() {
    return document.documentElement.dataset.theme || (systemTheme.matches ? 'dark' : 'light');
  }

  function updateThemeButton() {
    var dark = currentTheme() === 'dark';
    if (themeButton) {
      themeButton.setAttribute('aria-label', ru ? (dark ? 'Включить светлую тему' : 'Включить темную тему') : (dark ? 'Use light theme' : 'Use dark theme'));
    }
    var themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta) themeMeta.content = dark ? '#101714' : '#f4f6f1';
  }

  try {
    var savedTheme = localStorage.getItem(themeKey);
    if (savedTheme === 'dark' || savedTheme === 'light') document.documentElement.dataset.theme = savedTheme;
  } catch (error) { /* Browser privacy settings may disable storage. */ }

  if (themeButton) {
    themeButton.hidden = false;
    themeButton.addEventListener('click', function () {
      var nextTheme = currentTheme() === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem(themeKey, nextTheme); } catch (error) { /* The current page still changes theme. */ }
      updateThemeButton();
    });
    if (systemTheme.addEventListener) systemTheme.addEventListener('change', updateThemeButton);
    updateThemeButton();
  }

  document.querySelectorAll('[data-tour]').forEach(function (tour) {
    var tabs = Array.from(tour.querySelectorAll('[data-tour-tab]'));
    var panels = Array.from(tour.querySelectorAll('[data-tour-panel]'));
    var tabList = tour.querySelector('[data-tour-tabs]');
    if (!tabs.length || !tabList) return;
    tabList.setAttribute('role', 'tablist');

    function selectTab(index, focus) {
      tabs.forEach(function (tab, i) {
        var selected = i === index;
        tab.classList.toggle('is-active', selected);
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        var panel = document.getElementById(tab.getAttribute('aria-controls'));
        if (panel) panel.hidden = !selected;
      });
      if (focus) tabs[index].focus({ preventScroll: true });
    }

    tabs.forEach(function (tab, index) {
      var panelId = tab.getAttribute('href').slice(1);
      tab.id = panelId + '-tab';
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', panelId);
      var panel = document.getElementById(panelId);
      if (panel && panels.indexOf(panel) !== -1) {
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', tab.id);
      }
      tab.addEventListener('click', function (event) {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        selectTab(index, false);
      });
      tab.addEventListener('keydown', function (event) {
        var target;
        if (event.key === 'ArrowRight') target = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') target = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') target = 0;
        else if (event.key === 'End') target = tabs.length - 1;
        else if (event.key === ' ') target = index;
        if (target === undefined) return;
        event.preventDefault();
        selectTab(target, true);
      });
    });
    var initial = tabs.findIndex(function (tab) { return tab.getAttribute('href') === window.location.hash; });
    selectTab(initial >= 0 ? initial : 0, false);
  });

  document.querySelectorAll('[data-copy-target]').forEach(function (button) {
    var target = document.getElementById(button.dataset.copyTarget);
    if (!target) return;
    var idleLabel = button.textContent;
    var resetTimer;
    button.hidden = false;
    button.setAttribute('aria-live', 'polite');
    button.addEventListener('click', async function () {
      var copied = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(target.textContent);
          copied = true;
        } else {
          var input = document.createElement('textarea');
          input.value = target.textContent;
          input.style.position = 'fixed';
          input.style.opacity = '0';
          document.body.appendChild(input);
          input.select();
          copied = document.execCommand('copy');
          input.remove();
          button.focus({ preventScroll: true });
        }
      } catch (error) { copied = false; }
      button.textContent = copied ? (ru ? 'Скопировано' : 'Copied') : (ru ? 'Выделите команду' : 'Select the command');
      window.clearTimeout(resetTimer);
      resetTimer = window.setTimeout(function () { button.textContent = idleLabel; }, 2200);
    });
  });

  if (!window.HTMLDialogElement || !HTMLDialogElement.prototype.showModal) return;
  var dialog = document.createElement('dialog');
  dialog.className = 'lightbox';
  dialog.setAttribute('aria-label', ru ? 'Скриншот интерфейса NodeRoost' : 'NodeRoost interface screenshot');
  var header = document.createElement('div');
  header.className = 'lightbox-header';
  var caption = document.createElement('p');
  caption.className = 'lightbox-caption';
  var closeButton = document.createElement('button');
  closeButton.type = 'button';
  closeButton.className = 'lightbox-close';
  closeButton.textContent = ru ? 'Закрыть · Esc' : 'Close · Esc';
  var image = document.createElement('img');
  var imageError = document.createElement('p');
  imageError.className = 'lightbox-error';
  imageError.hidden = true;
  imageError.textContent = ru ? 'Не удалось загрузить скриншот. Откройте ссылку на изображение в новой вкладке.' : 'The screenshot could not be loaded. Open the image link in a new tab.';
  header.append(caption, closeButton);
  dialog.append(header, image, imageError);
  document.body.appendChild(dialog);
  var opener = null;
  var previousOverflow = '';
  closeButton.addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('click', function (event) { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', function () {
    document.body.style.overflow = previousOverflow;
    image.removeAttribute('src');
    if (opener && opener.isConnected) opener.focus({ preventScroll: true });
  });
  image.addEventListener('error', function () { if (dialog.open) imageError.hidden = false; });
  document.addEventListener('click', function (event) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target.closest && event.target.closest('a[data-lb]');
    if (!link) return;
    event.preventDefault();
    var preview = link.querySelector('img');
    opener = link;
    imageError.hidden = true;
    image.src = link.href;
    image.alt = preview ? preview.alt : '';
    caption.textContent = image.alt;
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.showModal();
    closeButton.focus();
  });
})();
