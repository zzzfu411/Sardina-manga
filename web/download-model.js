import {downloadKey} from './download-store.js';
import {chapterNavigation} from './reader-model.js';

export function downloadSelection(chapters, start, count) {
  const selected = [];
  let index = start;
  while (Number.isInteger(index) && chapters[index] && selected.length < Math.min(5, Math.max(1, count))) {
    selected.push(chapters[index]); index = chapterNavigation(chapters, index).next;
  }
  return selected;
}

export async function validateDownloadedImage(blob) {
  if (typeof createImageBitmap === 'function') {
    try {const bitmap = await createImageBitmap(blob); const valid = bitmap.width > 0 && bitmap.height > 0; bitmap.close(); if (valid) return;} catch {}
  }
  const url = URL.createObjectURL(blob);
  try {
    await new Promise((resolve, reject) => {
      const image = new Image(); image.onload = () => image.naturalWidth ? resolve() : reject(new Error('图片无法解码'));
      image.onerror = () => reject(new Error('图片内容无法解码，请重试')); image.src = url;
    });
  } finally {URL.revokeObjectURL(url);}
}

/** Explicit downloads survive chapter changes. Only one chapter and two pages run at once. */
export function createDownloadManager({store, api, imageUrl, loadImage, validateImage = validateDownloadedImage, onChange = () => {}}) {
  const jobs = new Map();
  let running = null;
  const notify = () => onChange();
  async function run(job) {
    job.running = true; job.status = 'loading'; job.error = ''; job.controller = new AbortController();
    const signal = job.controller.signal;
    const check = () => {if (signal.aborted) throw new DOMException('下载已暂停', 'AbortError');};
    notify();
    try {
      let record = await store.getChapter(job.book, job.chapter); check();
      if (!record?.urls.length || (!record.complete && job.refresh)) {
        const data = await api('/api/chapter-images', {siteId: job.book.siteId, chapterUrl: job.chapter.url, ...(job.refresh ? {refresh: true} : {})}, signal); check();
        record = await store.prepare({...job, urls: data?.images || []}); check();
      }
      job.record = record; job.status = 'downloading'; notify();
      if (!record.complete) {
        let next = 0, failure = null;
        async function worker() {
          try {
            while (next < record.urls.length) {
              check(); const index = next++;
              if (await store.getPage(record, index)) continue;
              check();
              const suffix = job.refresh ? `&retry=download-${Date.now()}` : '';
              const blob = await loadImage(imageUrl(record.urls[index], job.book.siteId) + suffix, {signal, priority: 2}); check();
              await validateImage(blob); check();
              const saved = await store.putPage(record, index, blob);
              if (!job.record || saved.count >= job.record.count) job.record = saved;
              notify();
            }
          } catch (error) {
            if (error?.name !== 'AbortError' && !failure) failure = error;
            job.controller.abort();
          }
        }
        await Promise.all([worker(), worker()]);
        if (failure) throw failure;
        check();
      }
      job.status = 'complete';
    } catch (error) {
      job.status = job.record?.complete ? 'complete' : error?.name === 'AbortError' ? 'paused' : 'error';
      if (job.status === 'error') job.error = error?.message || '下载失败，请重试';
    } finally {
      job.running = false; job.controller = null; running = null; notify(); pump();
    }
  }
  function pump() {
    if (running) return;
    const job = [...jobs.values()].find(item => item.status === 'queued');
    if (job) {running = job; job.promise = run(job);}
  }
  function add(context) {
    const id = downloadKey(context.book, context.chapter), old = jobs.get(id);
    if (old?.running || ['saving', 'queued', 'complete'].includes(old?.status)) return;
    if ([...jobs.values()].filter(job => job.running || ['saving', 'queued'].includes(job.status)).length >= 20) throw new Error('请等待当前下载完成，最多同时排队 20 章');
    const job = old || {...context, id};
    job.refresh = old?.status === 'error'; job.status = 'saving'; job.error = ''; jobs.set(id, job); notify();
    job.enqueued = Promise.resolve().then(() => store.enqueue?.(job)).then(record => {
      if (record) job.record = record;
      if (job.status === 'saving') job.status = 'queued';
      notify(); pump();
    }).catch(error => {job.status = 'error'; job.error = error.message || '下载任务保存失败'; notify();});
  }
  async function pause(id) {
    const job = jobs.get(id);
    if (!job || job.status === 'complete') return;
    job.status = 'paused'; job.controller?.abort(); notify();
    await job.enqueued;
    await job.promise;
  }
  return {
    add, pause, jobs: () => [...jobs.values()],
    pauseAll() {for (const job of jobs.values()) if (job.status !== 'complete') {job.status = 'paused'; job.controller?.abort();} notify();},
    async remove(id) {await pause(id); await store.remove(id); jobs.delete(id); notify();},
  };
}
