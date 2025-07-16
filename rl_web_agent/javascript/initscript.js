(() => {
  const ORIG = EventTarget.prototype.addEventListener;
  const HOVER = new Set(['mouseenter', 'mouseover', 'pointerenter']);
  EventTarget.prototype.addEventListener = function (type, listener, opts) {
    if (HOVER.has(type)) {
      try {
        console.log("hover event", type);
        // only real elements (skip window / document)
        if (this && this.setAttribute) {
          console.log("setting attribute", this);
          this.setAttribute('data-maybe-hoverable', 'true');
        }
      } catch (_) { /* ignore edge‑cases */ }
    }
    return ORIG.call(this, type, listener, opts);
  };
  console.log("initscript.js loaded");
})();
