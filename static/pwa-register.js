(function () {
  if (!('serviceWorker' in navigator)) return;

  async function registerPwa() {
    try {
      await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    } catch (error) {
      console.warn('Delta PWA registration failed:', error);
    }
  }

  if (document.readyState === 'complete') {
    registerPwa();
  } else {
    window.addEventListener('load', registerPwa, { once: true });
  }
})();