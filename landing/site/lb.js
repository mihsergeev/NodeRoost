/* Клик по скриншоту открывает его в полном разрешении, клик по схеме —
   увеличивает её. Без скрипта ссылка на скриншот просто откроет картинку. */
(function () {
  var box = null;
  var ru = document.documentElement.lang === 'ru';
  var hint = 'Esc — ' + (ru ? 'закрыть' : 'to close');

  function close() {
    if (!box) return;
    box.remove();
    box = null;
    document.body.style.overflow = '';
    document.removeEventListener('keydown', onKey);
  }

  function onKey(e) {
    if (e.key === 'Escape') close();
  }

  function open(node, text) {
    var caption = document.createElement('p');
    caption.textContent = text ? text + ' · ' + hint : hint;

    box = document.createElement('div');
    box.className = 'lb';
    box.append(node, caption);
    box.addEventListener('click', close);

    document.body.append(box);
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKey);
  }

  document.addEventListener('click', function (e) {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (!e.target.closest) return;

    var link = e.target.closest('a.frame[data-lb]');
    if (link) {
      e.preventDefault();
      var shot = link.querySelector('img');
      var full = document.createElement('img');
      full.src = link.getAttribute('href');
      full.alt = shot ? shot.alt : '';
      open(full, shot ? shot.alt : '');
      return;
    }

    var host = e.target.closest('[data-lb-svg]');
    if (host) {
      var svg = host.querySelector('svg');
      if (svg) open(svg.cloneNode(true), host.getAttribute('data-caption') || '');
    }
  });
})();
