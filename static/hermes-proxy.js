// HermesProxy — stable plugin API surface for hermes-proxy
// Versioned so plugins can guard compatibility.
window.HermesProxy = {
  version: 1,
  _hooks: {},
  on(event, fn) {
    (this._hooks[event] ||= []).push(fn);
  },
  emit(event, ...args) {
    for (const fn of (this._hooks[event] || [])) {
      try { fn(...args); }
      catch (e) { console.error('Plugin error in', event, ':', e); }
    }
  },
  registerTheme(name, colors) {
    this.emit('themeRegister', name, colors);
  },
  setTheme(name) {
    document.documentElement.setAttribute('data-theme', name);
    this.emit('themeChange', name);
  },
};
