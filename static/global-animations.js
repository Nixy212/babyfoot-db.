(function() {
  'use strict';

  function createFloatingShapes() {
    if (document.querySelector('.floating-shapes-global')) return;

    const container = document.createElement('div');
    container.className = 'floating-shapes-global';

    for (let i = 1; i <= 5; i++) {
      const shape = document.createElement('div');
      shape.className = `floating-shape-global shape-global-${i}`;
      container.appendChild(shape);
    }

    document.body.insertBefore(container, document.body.firstChild);
  }

  function createGlowPulse() {
    if (document.querySelector('.glow-pulse-global')) return;

    const glow = document.createElement('div');
    glow.className = 'glow-pulse-global';
    document.body.insertBefore(glow, document.body.firstChild);
  }

  function init() {
    createFloatingShapes();
    createGlowPulse();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
