// session-favorites.js — star and pin favorite sessions
(function () {
  'use strict';
  if (window.__SessionFavoritesInited) return;
  window.__SessionFavoritesInited = true;

  const PLUGIN_NAME = 'session-favorites';
  const STORAGE_KEY = 'hermes-favorites';

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }

  function getFavorites() {
    return safe(() => {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    }, {});
  }

  function setFavorites(map) {
    safe(() => { localStorage.setItem(STORAGE_KEY, JSON.stringify(map)); });
  }

  function createStar(faved) {
    const btn = document.createElement('button');
    btn.className = 'fav-star';
    btn.textContent = faved ? '★' : '☆';
    btn.style.cssText = 'background:none;border:none;cursor:pointer;font-size:16px;color:var(--accent);padding:0 4px;opacity:' + (faved ? '1' : '0.6') + '}';
    return btn;
  }

  function addStars(container) {
    const favs = getFavorites();
    container.querySelectorAll('.session-item').forEach(item => {
      if (item.querySelector('.fav-star')) return;
      const sid = item.dataset.id;
      if (!sid) return;
      const faved = !!favs[sid];
      const star = createStar(faved);
      star.addEventListener('click', e => {
        e.stopPropagation();
        const map = getFavorites();
        if (map[sid]) delete map[sid]; else map[sid] = true;
        setFavorites(map);
        sortFavorites(container);
      });
      const titleEl = item.querySelector('.session-title');
      if (titleEl) {
        titleEl.insertBefore(star, titleEl.firstChild);
      } else {
        item.appendChild(star);
      }
    });
  }

  function sortFavorites(container) {
    const favs = getFavorites();
    const items = Array.from(container.querySelectorAll('.session-item'));
    items.sort((a, b) => {
      const fa = !!favs[a.dataset.id];
      const fb = !!favs[b.dataset.id];
      if (fa === fb) return items.indexOf(a) - items.indexOf(b); // stable-ish
      return fb - fa; // favorites first
    });
    items.forEach(el => container.appendChild(el));
    // Re-apply stars after reorder since app.js may replace elements
    addStars(container);
  }

  function init() {
    if (window.HermesProxy) {
      HermesProxy.on('sessionListRendered', container => {
        safe(() => {
          addStars(container);
          sortFavorites(container);
        });
      });
    }
    // Also apply immediately on plugin load if sidebar already has items
    const list = document.getElementById('session-list');
    if (list) {
      safe(() => {
        addStars(list);
        sortFavorites(list);
      });
    }
    console.log(`[${PLUGIN_NAME}] loaded`);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
