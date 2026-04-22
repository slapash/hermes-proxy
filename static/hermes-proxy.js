// HermesProxy — global event bus for plugins
// Type: any, because plugins are untrusted third-party code.
window.HermesProxy = {
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
