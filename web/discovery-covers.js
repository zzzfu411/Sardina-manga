import {externalUrl} from './discovery-model.js';

const keyOf = book => `${book.siteId}::${externalUrl(book.detailUrl)}`;

/** Resolve missing covers independently of the list, with bounded work and cache. */
export function createDiscoveryCoverLoader({api, concurrency = 3}) {
  const cache = new Map(), jobs = new Map(), running = new Set();
  let queue = [], enabled = false;
  function remember(key, value) {
    cache.delete(key); cache.set(key, value);
    if (cache.size > 128) cache.delete(cache.keys().next().value);
  }
  function pump() {
    while (enabled && running.size < concurrency && queue.length) {
      const job = queue.shift();
      const run = {job, controller: new AbortController(), cancelled: false};
      running.add(run);
      const current = () => !run.cancelled && jobs.get(job.key) === job;
      (async () => {
        try {
          const result = await api('/api/discovery/cover', {siteId: job.book.siteId, detailUrl: job.book.detailUrl,
            ...(job.refresh ? {refresh: true} : {})}, run.controller.signal);
          if (!current()) return;
          const url = externalUrl(result?.coverUrl);
          if (!url || result.siteId !== job.book.siteId || externalUrl(result.detailUrl) !== externalUrl(job.book.detailUrl)) {
            throw new Error('封面与当前作品不匹配，请重试');
          }
          remember(job.key, url); jobs.delete(job.key); job.onLoad(url);
        } catch (error) {
          if (!current()) return;
          jobs.delete(job.key); job.onError(error);
        } finally {running.delete(run); pump();}
      })();
    }
  }
  function pause() {
    enabled = false;
    for (const run of running) {
      run.cancelled = true; run.controller.abort(); queue.unshift(run.job);
    }
    running.clear();
  }
  return {
    request(book, {onLoad, onError, refresh = false}) {
      const key = keyOf(book);
      if (!refresh && cache.has(key)) {onLoad(cache.get(key)); return;}
      if (jobs.has(key)) return;
      if (refresh) cache.delete(key);
      const job = {key, book: {...book}, onLoad, onError, refresh};
      jobs.set(key, job); queue.push(job); pump();
    },
    resume() {enabled = true; pump();},
    pause,
    clear() {pause(); queue = []; jobs.clear();},
  };
}
