import {fetchReaderImage} from './reader-transport.js';

const abortError = () => new DOMException('图片请求已取消', 'AbortError');

// Priority changes transport scheduling, never the identity of image bytes.
// Keep arbitrary/custom transport URLs intact; only the local proxy accepts it.
function proxyUrl(raw, purpose) {
  try {
    const url = new URL(raw, globalThis.location?.href || 'http://localhost/');
    if (url.pathname !== '/api/image') return raw;
    url.searchParams.delete('purpose');
    if (purpose) url.searchParams.set('purpose', purpose);
    return raw.startsWith('/') && !raw.startsWith('//') ? url.pathname + url.search + url.hash : url.href;
  } catch {return raw;}
}
export const imageRequestPurpose = priority => priority >= 2 ? 'download' : priority === 1 ? 'prefetch' : 'reader';

/** Shared reader/download budget. Cancelling one consumer cannot cancel another. */
export function createImageLoader({fetchImage = fetchReaderImage, limit = 4, backgroundLimit = 2, maxBytes = 24 * 1024 * 1024, timeoutMs = 30000} = {}) {
  const jobs = new Map(), cache = new Map();
  let active = 0, background = 0, bytes = 0, sequence = 0;
  function remember(url, blob) {
    if (cache.has(url)) {bytes -= cache.get(url).size; cache.delete(url);}
    if (blob.size > maxBytes) return;
    cache.set(url, blob); bytes += blob.size;
    while (bytes > maxBytes) {const key = cache.keys().next().value; bytes -= cache.get(key).size; cache.delete(key);}
  }
  function pump() {
    while (active < limit) {
      const job = [...jobs.values()].filter(item => !item.running && item.users.size && (item.priority < 2 || background < backgroundLimit))
        .sort((a, b) => a.priority - b.priority || a.order - b.order)[0];
      if (!job) break;
      job.running = true; active++;
      const isBackground = job.priority >= 2;
      if (isBackground) background++;
      let timedOut = false;
      const timer = setTimeout(() => {timedOut = true; job.controller.abort();}, timeoutMs);
      Promise.resolve().then(() => fetchImage(proxyUrl(job.url, imageRequestPurpose(job.priority)), {signal: job.controller.signal, priority: job.priority === 0 ? 'high' : 'low'}))
        .then(blob => {
          if (!job.users.size || job.controller.signal.aborted) throw abortError();
          if (!(blob instanceof Blob) || !blob.size || blob.size > 12 * 1024 * 1024) throw new Error('图片为空或超过 12 MB 上限');
          remember(job.url, blob);
          job.settled = true;
          for (const user of job.users) user.finish(null, blob);
        }).catch(error => {
          job.settled = true;
          for (const user of job.users) user.finish(timedOut ? new Error('图片加载超时，请重试') : error);
        }).finally(() => {
          clearTimeout(timer);
          if (jobs.get(job.url) === job) jobs.delete(job.url);
          active--; if (isBackground) background--;
          pump();
        });
    }
  }
  function load(url, {signal, priority = 0} = {}) {
    if (signal?.aborted) return Promise.reject(abortError());
    url = proxyUrl(url);
    if (cache.has(url)) {const blob = cache.get(url); cache.delete(url); cache.set(url, blob); return Promise.resolve(blob);}
    let job = jobs.get(url);
    if (!job || job.settled || job.controller.signal.aborted) {
      job = {url, priority, order: sequence++, controller: new AbortController(), users: new Set(), running: false};
      jobs.set(url, job);
    }
    job.priority = Math.min(job.priority, priority);
    return new Promise((resolve, reject) => {
      const user = {finish(error, blob) {signal?.removeEventListener('abort', cancel); job.users.delete(user); error ? reject(error) : resolve(blob);}};
      function cancel() {
        user.finish(abortError());
        if (!job.users.size) {
          job.controller.abort();
          if (jobs.get(url) === job) jobs.delete(url);
          pump();
        }
      }
      job.users.add(user); signal?.addEventListener('abort', cancel, {once: true});
      pump();
    });
  }
  function invalidate(url, rejectedBlob) {
    const key = proxyUrl(url), blob = cache.get(key);
    // A delayed decode failure must not discard newer bytes fetched by another
    // consumer in the meantime. Other cached pages remain usable.
    if (!blob || rejectedBlob && rejectedBlob !== blob) return false;
    bytes -= blob.size; cache.delete(key); return true;
  }
  return {load, invalidate, promote(url) {const job = jobs.get(proxyUrl(url)); if (job) {job.priority = 0; pump();}}, stats: () => ({active, background, bytes, cached: cache.size, queued: [...jobs.values()].filter(job => !job.running).length})};
}
