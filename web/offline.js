let ready = false;
export const offlineReady = () => ready;

async function checkReady() {
  const worker = navigator.serviceWorker.controller;
  if (!worker) return;
  const channel = new MessageChannel();
  channel.port1.onmessage = event => {
    ready = event.data?.ready === true;
    window.dispatchEvent(new CustomEvent('sardina-offline-ready', {detail: {ready}}));
    channel.port1.close();
  };
  worker.postMessage({type: 'offline-status'}, [channel.port2]);
}

if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.addEventListener('controllerchange', checkReady);
  const prepare = () => navigator.serviceWorker.register('/sw.js', {updateViaCache: 'none'})
    .then(() => navigator.serviceWorker.ready).then(checkReady).catch(() => {});
  // Registration and precaching never block navigation or the first page.
  if (document.readyState !== 'loading') void prepare();
  else document.addEventListener('DOMContentLoaded', prepare, {once: true});
}
