import test from 'node:test';
import assert from 'node:assert/strict';
import {createDownloadManager, downloadSelection} from '../web/download-model.js';
import {downloadKey} from '../web/download-store.js';

const book = {siteId: 'mangabz', detailUrl: 'https://www.mangabz.com/91bz/', title: '测试漫画'};
const chapters = [{url: 'https://www.mangabz.com/m1/', name: '第一章'}, {url: 'https://www.mangabz.com/m2/', name: '第二章'}];
const context = {book, chapters, chapter: chapters[0]};
const urls = Array.from({length: 6}, (_, i) => `https://image.mangabz.com/${i}.jpg`);
const waitFor = async test => {for (let i = 0; i < 1000; i++) {if (test()) return; await new Promise(resolve => setTimeout(resolve, 1));} throw new Error('timed out');};
function memoryStore() {
  const rows = new Map(), pages = new Map();
  return {rows, pages,
    async enqueue(value) {const id = downloadKey(value.book, value.chapter); if (!rows.has(id)) rows.set(id, {...value, id, urls: [], generation: 'queued', count: 0, bytes: 0, complete: false}); return rows.get(id);},
    async getChapter(book, chapter) {return rows.get(downloadKey(book, chapter));},
    async prepare(value) {const id = downloadKey(value.book, value.chapter); if (rows.get(id)?.urls.length) return rows.get(id); const row = {...value, id, generation: '1', count: 0, bytes: 0, complete: false}; rows.set(id, row); return row;},
    async getPage(row, index) {return pages.get(`${row.id}:${index}`);},
    async putPage(row, index, blob) {const previous = rows.get(row.id); assert.ok(previous); const key = `${row.id}:${index}`, old = pages.get(key); pages.set(key, blob); const saved = {...previous, count: previous.count + (old ? 0 : 1), bytes: previous.bytes + blob.size - (old?.size || 0)}; saved.complete = saved.count === saved.urls.length; rows.set(row.id, saved); return saved;},
    async remove(id) {rows.delete(id); for (const key of pages.keys()) if (key.startsWith(id + ':')) pages.delete(key);},
  };
}
function manager(store, overrides = {}) {return createDownloadManager({store, api: async () => ({images: urls}), imageUrl: url => '/image?url=' + url, loadImage: async () => new Blob(['valid']), validateImage: async () => {}, ...overrides});}

test('downloads two pages at most, counts persisted pages, and skips already complete chapters', async () => {
  const store = memoryStore(); let active = 0, maximum = 0, calls = 0;
  const controller = manager(store, {loadImage: async () => {calls++; active++; maximum = Math.max(active, maximum); await new Promise(resolve => setTimeout(resolve, 2)); active--; return new Blob(['ok']);}});
  controller.add(context); controller.add(context);
  await waitFor(() => controller.jobs()[0].status === 'complete');
  assert.equal(calls, 6); assert.equal(maximum, 2); assert.equal(store.rows.values().next().value.count, 6);
  const restored = manager(store, {api: () => {throw new Error('should stay offline');}, loadImage: () => {throw new Error('already downloaded');}});
  restored.add(context); await waitFor(() => restored.jobs()[0].status === 'complete');
});

test('a bad page cannot mark a chapter complete; retry resumes saved pages', async () => {
  const store = memoryStore(); let fail = true, validationCalls = 0, networkCalls = 0;
  const controller = manager(store, {loadImage: async () => {networkCalls++; return new Blob(['ok']);}, validateImage: async () => {validationCalls++; if (fail && validationCalls === 3) throw new Error('bad image');}});
  controller.add(context); await waitFor(() => controller.jobs()[0].status === 'error');
  const saved = store.rows.values().next().value.count; assert.ok(saved > 0 && saved < 6); assert.equal(store.rows.values().next().value.complete, false);
  fail = false; const previousCalls = networkCalls; controller.add(context); await waitFor(() => controller.jobs()[0].status === 'complete');
  assert.equal(networkCalls - previousCalls, 6 - saved);
});

test('pause aborts pending transfers, retains committed pages, and delete cannot be resurrected', async () => {
  const store = memoryStore(); let calls = 0;
  const controller = manager(store, {loadImage: async (url, {signal}) => {
    if (++calls <= 2) return new Blob(['ok']);
    return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError'))));
  }});
  controller.add(context); await waitFor(() => calls >= 4);
  await controller.pause(downloadKey(book, chapters[0]));
  assert.equal(controller.jobs()[0].status, 'paused'); assert.equal(store.rows.values().next().value.count, 2);
  await controller.remove(downloadKey(book, chapters[0])); assert.equal(store.rows.size, 0); assert.equal(store.pages.size, 0); assert.equal(controller.jobs().length, 0);
});

test('storage failures remain explicit and do not erase saved pages or report success', async () => {
  const store = memoryStore(), put = store.putPage; let writes = 0;
  store.putPage = async (...args) => {if (++writes > 1) throw new Error('下载空间不足'); return put(...args);};
  const controller = manager(store); controller.add(context); await waitFor(() => controller.jobs()[0].status === 'error');
  assert.match(controller.jobs()[0].error, /空间不足/); assert.equal(store.rows.values().next().value.count, 1);
});

test('multi-chapter selection follows explicit language sequences and stops at the end', () => {
  const rows = [{url: 'a', sequenceId: 'zh'}, {url: 'b', sequenceId: 'en'}, {url: 'c', sequenceId: 'zh'}, {url: 'd', sequenceId: 'en'}];
  assert.deepEqual(downloadSelection(rows, 0, 5).map(row => row.url), ['a', 'c']);
  assert.deepEqual(downloadSelection(rows, 1, 3).map(row => row.url), ['b', 'd']);
  assert.deepEqual(downloadSelection(rows, 20, 3), []);
});

test('queued chapters persist before starting and can resume after the manager is recreated', async () => {
  const store = memoryStore();
  const controller = manager(store, {api: (path, body, signal) => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError'))))});
  controller.add(context); controller.add({...context, chapter: chapters[1]});
  await waitFor(() => store.rows.size === 2 && controller.jobs()[0].status === 'loading');
  controller.pauseAll(); await waitFor(() => !controller.jobs()[0].running);
  assert.equal(store.rows.get(downloadKey(book, chapters[1])).urls.length, 0);
  const reopened = manager(store); reopened.add({...context, chapter: chapters[1]});
  await waitFor(() => reopened.jobs()[0].status === 'complete');
  assert.equal(store.rows.get(downloadKey(book, chapters[1])).count, 6);
});
