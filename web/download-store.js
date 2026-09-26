import {sourceEntryKey} from './book-identity.js';

export const DOWNLOAD_LIMIT = 1024 * 1024 * 1024;
export const downloadKey = (book, chapter) => JSON.stringify([sourceEntryKey(book), chapter.url]);
const request = value => new Promise((resolve, reject) => {value.onsuccess = () => resolve(value.result); value.onerror = () => reject(value.error);});
const storageError = error => error?.name === 'QuotaExceededError' ? new Error('下载空间不足，请删除不需要的章节后继续') : error;
const catalogChapters = rows => (rows || []).filter(row => !row.localOnly).map(({url, name, language, sequenceId}) => ({url, name, language, sequenceId}));
const compactBook = book => Object.fromEntries(['siteId', 'siteName', 'detailUrl', 'title', 'coverUrl', 'author', 'description', 'tags', 'language', 'edition'].filter(key => book[key] !== undefined).map(key => [key, book[key]]));
const summary = row => ({id: row.id, book: row.book, chapter: row.chapter, total: row.urls.length, count: row.count, bytes: row.bytes + (row.backups || []).reduce((n, version) => n + version.bytes, 0), complete: row.complete, updatedAt: row.updatedAt, retainedVersions: row.backups?.length || 0});
const version = row => ({generation: row.generation, urls: row.urls, count: row.count, bytes: row.bytes, complete: row.complete});

// Ignore only the verified MangaBZ temporary ticket fields; every other URL
// remains exact. A chapter's order and page count must also match before reuse.
export function imageIdentity(site, raw) {
  try {
    const url = new URL(raw);
    if (site === 'mangabz' && /(^|\.)mangabz\.com$/.test(url.hostname)) {
      for (const key of ['key']) url.searchParams.delete(key);
      url.hash = ''; return url.href;
    }
  } catch {}
  return raw;
}
export const sameImageManifest = (site, left, right) => left.length === right.length && left.every((url, i) => imageIdentity(site, url) === imageIdentity(site, right[i]));

/** Saved bytes remain reachable when the remote source removes a chapter.
 * Its old position among current entries is unknown; do not guess a new order.
 */
export function localChapterCatalog(chapters, chapter) {
  const rows = Array.isArray(chapters) ? chapters.map(row => ({...row})) : [];
  if (chapter?.url && !rows.some(row => row.url === chapter.url)) rows.push({...chapter, localOnly: true});
  return rows;
}

/** v2 shares book catalogs, lists small summaries and commits blobs with counts.
 * Changed content keeps previous generations until the replacement completes.
 */
export function createDownloadStore({indexedDB = globalThis.indexedDB, name = 'sardina.downloads.v1', limit = DOWNLOAD_LIMIT, channel = globalThis.BroadcastChannel} = {}) {
  let opening;
  const listeners = new Set(), bus = channel ? new channel(name + '.changes') : null;
  const changed = () => {for (const callback of listeners) callback();};
  if (bus) {bus.onmessage = changed; bus.unref?.();}
  function database() {
    if (!indexedDB) return Promise.reject(new Error('此浏览器不支持章节下载，请使用正常浏览模式'));
    if (!opening) opening = new Promise((resolve, reject) => {
      let settled = false;
      const call = indexedDB.open(name, 2);
      const timer = setTimeout(() => {settled = true; reject(new Error('下载空间暂时无法打开，请关闭其他 Sardina 标签页后重试'));}, 5000);
      call.onupgradeneeded = event => {
        const db = call.result, tx = call.transaction;
        if (event.oldVersion < 1) {db.createObjectStore('chapters', {keyPath: 'id'}); db.createObjectStore('pages', {keyPath: ['chapterId', 'index']}); db.createObjectStore('meta');}
        const catalogs = db.createObjectStore('catalogs', {keyPath: 'id'});
        const summaries = db.createObjectStore('summaries', {keyPath: 'id'});
        const pages = db.createObjectStore('pageVersions', {keyPath: ['chapterId', 'generation', 'index']});
        const chapters = tx.objectStore('chapters');
        chapters.openCursor().onsuccess = event => {
          const cursor = event.target.result; if (!cursor) return;
          const row = cursor.value, id = sourceEntryKey(row.book);
          const savedCatalog = {id, book: compactBook(row.book), chapters: catalogChapters(row.chapters), updatedAt: row.updatedAt};
          const query = catalogs.get(id);
          query.onsuccess = () => {
            if (!query.result || query.result.updatedAt < row.updatedAt) catalogs.put(savedCatalog);
          };
          row.catalogId = id; row.book = compactBook(row.book); delete row.chapters;
          row.generation ||= crypto.randomUUID(); row.token = row.generation; row.backups = [];
          cursor.update(row); summaries.put(summary(row)); cursor.continue();
        };
        tx.objectStore('pages').openCursor().onsuccess = event => {
          const cursor = event.target.result; if (!cursor) return;
          const old = cursor.value, query = chapters.get(old.chapterId);
          query.onsuccess = () => {if (query.result) pages.put({...old, generation: query.result.generation}); cursor.delete(); cursor.continue();};
        };
      };
      call.onsuccess = () => {clearTimeout(timer); if (settled) {call.result.close(); return;} const db = call.result; db.onversionchange = () => {db.close(); opening = null;}; resolve(db);};
      call.onerror = () => {clearTimeout(timer); reject(storageError(call.error));};
    }).catch(error => {opening = null; throw error;});
    return opening;
  }
  async function run(names, mode, work) {
    const db = await database(), tx = db.transaction(names, mode);
    const done = new Promise((resolve, reject) => {tx.oncomplete = resolve; tx.onabort = () => reject(tx.error || new Error('下载保存中断，请继续下载')); tx.onerror = () => {};}); done.catch(() => {});
    try {const result = await work(tx); await done; if (mode === 'readwrite') {changed(); bus?.postMessage('change');} return result;}
    catch (error) {try {tx.abort();} catch {} await done.catch(() => {}); throw storageError(error);}
  }
  const pageRange = (id, generation) => generation ? IDBKeyRange.bound([id, generation, 0], [id, generation, Number.MAX_SAFE_INTEGER]) : IDBKeyRange.bound([id], [id, []]);
  function write(tx, row) {tx.objectStore('chapters').put(row); tx.objectStore('summaries').put(summary(row)); return row;}
  async function catalog(tx, book, chapters) {
    const id = sourceEntryKey(book), rows = tx.objectStore('catalogs');
    if (Array.isArray(chapters)) rows.put({id, book: compactBook(book), chapters: catalogChapters(chapters), updatedAt: Date.now()});
    return id;
  }
  async function hydrate(tx, row) {
    if (!row) return null;
    const data = await request(tx.objectStore('catalogs').get(row.catalogId));
    return {...row, chapters: localChapterCatalog(data?.chapters, row.chapter)};
  }
  function hasCatalog(tx, id) {
    return new Promise((resolve, reject) => {
      const cursor = tx.objectStore('chapters').openCursor();
      cursor.onerror = () => reject(cursor.error);
      cursor.onsuccess = () => {const row = cursor.result; if (!row) resolve(false); else if (row.value.catalogId === id) resolve(true); else row.continue();};
    });
  }
  async function enqueue({book, chapters, chapter}) {
    return run(['chapters', 'summaries', 'catalogs'], 'readwrite', async tx => {
      const id = downloadKey(book, chapter), old = await request(tx.objectStore('chapters').get(id));
      if (old) return hydrate(tx, old);
      const catalogId = await catalog(tx, book, chapters);
      return hydrate(tx, write(tx, {id, catalogId, book: compactBook(book), chapter: {...chapter}, urls: [], token: crypto.randomUUID(), generation: crypto.randomUUID(), backups: [], count: 0, bytes: 0, complete: false, updatedAt: Date.now()}));
    });
  }
  async function prepare({book, chapters, chapter, urls, expectedGeneration}) {
    if (!urls.length || urls.length > 2000 || urls.some(url => typeof url !== 'string' || !/^https?:\/\//i.test(url))) throw new Error('章节图片列表无效或超过 2000 页下载上限');
    return run(['chapters', 'summaries', 'catalogs'], 'readwrite', async tx => {
      const id = downloadKey(book, chapter), old = await request(tx.objectStore('chapters').get(id));
      if (expectedGeneration && old?.generation !== expectedGeneration) throw new Error('此下载已删除或变更，请重新选择章节');
      if (old?.complete) return hydrate(tx, old);
      if (old && sameImageManifest(book.siteId, old.urls, urls)) {
        old.urls = [...urls]; old.updatedAt = Date.now(); return hydrate(tx, write(tx, old));
      }
      const backups = [...(old?.backups || [])]; if (old?.count) backups.push(version(old));
      if (backups.length > 3) throw new Error('此源多次变更图片，旧缓存已保留，请先恢复旧缓存或删除本章后重下');
      const catalogId = await catalog(tx, book, chapters);
      return hydrate(tx, write(tx, {id, catalogId, book: compactBook(book), chapter: {...chapter}, urls: [...urls], token: old?.token || crypto.randomUUID(), generation: crypto.randomUUID(), backups, count: 0, bytes: 0, complete: false, updatedAt: Date.now()}));
    });
  }
  async function putPage(record, index, blob) {
    if (!(blob instanceof Blob) || !blob.size || blob.size > 12 * 1024 * 1024) throw new Error('无法保存这张图片');
    return run(['chapters', 'summaries', 'pageVersions', 'meta'], 'readwrite', async tx => {
      const rows = tx.objectStore('chapters'), pages = tx.objectStore('pageVersions'), meta = tx.objectStore('meta');
      const row = await request(rows.get(record.id));
      if (!row || row.generation !== record.generation) throw new Error('此下载已删除或变更，请重新选择章节');
      if (!Number.isSafeInteger(index) || index < 0 || index >= row.urls.length) throw new Error('下载页码无效');
      const old = await request(pages.get([row.id, row.generation, index]));
      const delta = blob.size - (old?.blob?.size || 0); let usage = (Number(await request(meta.get('bytes'))) || 0) + delta;
      if (usage > limit) throw new Error('下载已达到 1 GB 上限，请删除不需要的章节后继续');
      row.count += old ? 0 : 1; row.bytes += delta; row.complete = row.count === row.urls.length; row.updatedAt = Date.now();
      pages.put({chapterId: row.id, generation: row.generation, index, blob});
      if (row.complete) {for (const backup of row.backups || []) {pages.delete(pageRange(row.id, backup.generation)); usage -= backup.bytes;} row.backups = [];}
      meta.put(Math.max(0, usage), 'bytes'); return write(tx, row);
    });
  }
  async function getPage(record, index) {
    return run(['chapters', 'pageVersions'], 'readonly', async tx => {
      const current = await request(tx.objectStore('chapters').get(record.id));
      if (!current || ![current.generation, ...(current.backups || []).map(row => row.generation)].includes(record.generation)) return null;
      return (await request(tx.objectStore('pageVersions').get([record.id, record.generation, index])))?.blob || null;
    });
  }
  return {
    enqueue, prepare, putPage, getPage,
    subscribe(callback) {listeners.add(callback); return () => listeners.delete(callback);},
    async close() {bus?.close(); listeners.clear(); (await opening)?.close(); opening = null;},
    putCatalog: (book, chapters) => run(['chapters', 'catalogs'], 'readwrite', async tx => {if (await hasCatalog(tx, sourceEntryKey(book))) return catalog(tx, book, chapters);}),
    getChapter: (book, chapter) => run(['chapters', 'catalogs'], 'readonly', async tx => hydrate(tx, await request(tx.objectStore('chapters').get(downloadKey(book, chapter))))),
    list: () => run(['summaries'], 'readonly', tx => request(tx.objectStore('summaries').getAll())),
    async restorePrevious(id) {
      return run(['chapters', 'summaries'], 'readwrite', async tx => {
        const row = await request(tx.objectStore('chapters').get(id));
        if (!row?.backups?.length) throw new Error('没有保留的旧缓存');
        const previous = row.backups.pop(); if (row.count) row.backups.push(version(row));
        Object.assign(row, previous, {updatedAt: Date.now()}); return write(tx, row);
      });
    },
    async remove(id) {
      return run(['chapters', 'summaries', 'pageVersions', 'meta', 'catalogs'], 'readwrite', async tx => {
        const rows = tx.objectStore('chapters'), meta = tx.objectStore('meta'), old = await request(rows.get(id));
        const usage = Number(await request(meta.get('bytes'))) || 0;
        tx.objectStore('pageVersions').delete(pageRange(id)); rows.delete(id); tx.objectStore('summaries').delete(id);
        meta.put(Math.max(0, usage - (old ? summary(old).bytes : 0)), 'bytes');
        if (old && !await hasCatalog(tx, old.catalogId)) tx.objectStore('catalogs').delete(old.catalogId);
      });
    },
  };
}
