import {fetchReaderImage} from './reader-transport.js';

const abortError = () => new DOMException('图片请求已取消', 'AbortError');

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
      Promise.resolve().then(() => fetchImage(job.url, {signal: job.controller.signal, priority: job.priority === 0 ? 'high' : 'low'}))
        .then(blob => {
          if (!job.users.size || job.controller.signal.aborted) throw abortError();
          if (!(blob instanceof Blob) || !blob.size || blob.size > 12 * 1024 * 1024) throw new Error('图片为空或超过 12 MB 上限');
          remember(job.url, blob);
          for (const user of job.users) user.finish(null, blob);
        }).catch(error => {
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
    if (cache.has(url)) {const blob = cache.get(url); cache.delete(url); cache.set(url, blob); return Promise.resolve(blob);}
    let job = jobs.get(url);
    if (!job || job.controller.signal.aborted) {
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
  return {load, promote(url) {const job = jobs.get(url); if (job) {job.priority = 0; pump();}}, stats: () => ({active, background, bytes, cached: cache.size, queued: [...jobs.values()].filter(job => !job.running).length})};
}
