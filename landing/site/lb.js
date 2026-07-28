/* Клик по скриншоту открывает его в полном разрешении.
   Без скрипта ссылка просто откроет картинку — поведение сохраняется. */
(function () {
  var box = null;
  var hint = document.documentElement.lang === 'ru' ? 'закрыть' : 'to close';

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

  document.addEventListener('click', function (e) {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var link = e.target.closest && e.target.closest('a.frame[data-lb]');
    if (!link) return;
    e.preventDefault();

    var shot = link.querySelector('img');
    var full = document.createElement('img');
    full.src = link.getAttribute('href');
    full.alt = shot ? shot.alt : '';

    var caption = document.createElement('p');
    caption.textContent = (shot ? shot.alt + ' · ' : '') + 'Esc — ' + hint;

    box = document.createElement('div');
    box.className = 'lb';
    box.append(full, caption);
    box.addEventListener('click', close);

    document.body.append(box);
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKey);
  });
})();
