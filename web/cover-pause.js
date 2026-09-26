/** Free browser connections used by unfinished covers while a reader is open. */
export function createCoverPause({root = document, exclude = null} = {}) {
  const held = new Map();
  let paused = false, turn = 0;
  const observer = new MutationObserver(() => {if (paused) holdPending();});
  function suppress(event) {
    if (held.has(event.target)) event.stopImmediatePropagation();
  }
  function holdPending() {
    for (const img of root.querySelectorAll('img[src]')) {
      if (exclude?.contains(img) || img.complete) continue;
      const src = img.getAttribute('src');
      if (!src || img.hasAttribute('srcset')) continue;
      try {
        const url = new URL(src, root.baseURI);
        if (url.origin !== location.origin || url.pathname !== '/api/image') continue;
      } catch {continue;}
      held.set(img, src);
      img.removeAttribute('src');
    }
  }
  function pause() {
    paused = true; turn++;
    root.addEventListener('error', suppress, true);
    holdPending();
    observer.observe(root, {subtree: true, childList: true, attributes: true, attributeFilter: ['src']});
  }
  function resume() {
    paused = false; const generation = ++turn;
    // Let queued cancellation events settle before restoring listeners and
    // starting a fresh request. Reopening the reader invalidates this resume.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (paused || generation !== turn) return;
      observer.disconnect();
      const images = [...held]; held.clear(); root.removeEventListener('error', suppress, true);
      for (const [img, src] of images) if (img.isConnected && !img.hasAttribute('src')) img.setAttribute('src', src);
    }));
  }
  return {pause, resume};
}
