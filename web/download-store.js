import {sourceEntryKey} from './book-identity.js';

export const DOWNLOAD_LIMIT = 1024 * 1024 * 1024;
export const downloadKey = (book, chapter) => JSON.stringify([sourceEntryKey(book), chapter.url]);
const request = value => new Promise((resolve, reject) => {value.onsuccess = () => resolve(value.result); value.onerror = () => reject(value.error);});
const storageError = error => error?.name === 'QuotaExceededError' ? new Error('下载空间不足，请删除不需要的章节后继续') : error;

/** Metadata and page counts commit with the Blob, never before it. */
export function createDownloadStore({indexedDB = globalThis.indexedDB, name = 'sardina.downloads.v1', limit = DOWNLOAD_LIMIT} = {}) {
  let opening;
  function database() {
    if (!indexedDB) return Promise.reject(new Error('此浏览器不支持章节下载，请使用正常浏览模式'));
    if (!opening) opening = new Promise((resolve, reject) => {
      let settled = false;
      const call = indexedDB.open(name, 1);
      const timer = setTimeout(() => {settled = true; reject(new Error('下载空间暂时无法打开，请关闭其他 Sardina 标签页后重试'));}, 5000);
      call.onupgradeneeded = () => {
        const db = call.result;
        db.createObjectStore('chapters', {keyPath: 'id'});
        db.createObjectStore('pages', {keyPath: ['chapterId', 'index']});
        db.createObjectStore('meta');
      };
      call.onsuccess = () => {
        clearTimeout(timer);
        if (settled) {call.result.close(); return;}
        const db = call.result;
        db.onversionchange = () => {db.close(); opening = null;};
        resolve(db);
      };
      call.onerror = () => {clearTimeout(timer); reject(storageError(call.error));};
    }).catch(error => {opening = null; throw error;});
    return opening;
  }
  async function run(names, mode, work) {
    const db = await database(), tx = db.transaction(names, mode);
    const done = new Promise((resolve, reject) => {tx.oncomplete = resolve; tx.onabort = () => reject(tx.error || new Error('下载保存中断，请继续下载')); tx.onerror = () => {};});
    done.catch(() => {});
    try {const result = await work(tx); await done; return result;}
    catch (error) {try {tx.abort();} catch {} await done.catch(() => {}); throw storageError(error);}
  }
  const pageRange = id => IDBKeyRange.bound([id, 0], [id, Number.MAX_SAFE_INTEGER]);
  async function enqueue({book, chapters, chapter}) {
    return run(['chapters'], 'readwrite', async tx => {
      const rows = tx.objectStore('chapters'), id = downloadKey(book, chapter);
      const old = await request(rows.get(id));
      if (old) return old;
      const row = {id, book: {...book}, chapters: chapters.map(item => ({url: item.url, name: item.name, language: item.language, sequenceId: item.sequenceId})), chapter: {...chapter}, urls: [], generation: crypto.randomUUID(), count: 0, bytes: 0, complete: false, updatedAt: Date.now()};
      rows.put(row); return row;
    });
  }
  async function prepare({book, chapters, chapter, urls}) {
    if (!urls.length || urls.length > 2000 || urls.some(url => typeof url !== 'string' || !/^https?:\/\//i.test(url))) throw new Error('章节图片列表无效或超过 2000 页下载上限');
    const id = downloadKey(book, chapter);
    return run(['chapters', 'pages', 'meta'], 'readwrite', async tx => {
      const rows = tx.objectStore('chapters'), meta = tx.objectStore('meta');
      const old = await request(rows.get(id));
      if (old && old.urls.length === urls.length && old.urls.every((url, index) => url === urls[index])) return old;
      if (old?.complete) return old;
      const usage = Number(await request(meta.get('bytes'))) || 0;
      if (old) tx.objectStore('pages').delete(pageRange(id));
      const row = {id, book: {...book}, chapters: chapters.map(item => ({url: item.url, name: item.name, language: item.language, sequenceId: item.sequenceId})), chapter: {...chapter}, urls: [...urls], generation: crypto.randomUUID(), count: 0, bytes: 0, complete: false, updatedAt: Date.now()};
      rows.put(row); meta.put(Math.max(0, usage - (old?.bytes || 0)), 'bytes'); return row;
    });
  }
  async function putPage(record, index, blob) {
    if (!(blob instanceof Blob) || !blob.size || blob.size > 12 * 1024 * 1024) throw new Error('无法保存这张图片');
    return run(['chapters', 'pages', 'meta'], 'readwrite', async tx => {
      const rows = tx.objectStore('chapters'), pages = tx.objectStore('pages'), meta = tx.objectStore('meta');
      const row = await request(rows.get(record.id));
      if (!row || row.generation !== record.generation) throw new Error('此下载已删除或变更，请重新选择章节');
      if (!Number.isSafeInteger(index) || index < 0 || index >= row.urls.length) throw new Error('下载页码无效');
      const old = await request(pages.get([row.id, index]));
      const delta = blob.size - (old?.blob?.size || 0), usage = (Number(await request(meta.get('bytes'))) || 0) + delta;
      if (usage > limit) throw new Error('下载已达到 1 GB 上限，请删除不需要的章节后继续');
      row.count += old ? 0 : 1; row.bytes += delta; row.complete = row.count === row.urls.length; row.updatedAt = Date.now();
      pages.put({chapterId: row.id, index, blob}); rows.put(row); meta.put(usage, 'bytes'); return row;
    });
  }
  async function getPage(record, index) {
    return run(['chapters', 'pages'], 'readonly', async tx => {
      const current = await request(tx.objectStore('chapters').get(record.id));
      if (current?.generation !== record.generation) return null;
      return (await request(tx.objectStore('pages').get([record.id, index])))?.blob || null;
    });
  }
  return {
    enqueue, prepare, putPage, getPage,
    getChapter: (book, chapter) => run(['chapters'], 'readonly', tx => request(tx.objectStore('chapters').get(downloadKey(book, chapter)))),
    list: () => run(['chapters'], 'readonly', tx => request(tx.objectStore('chapters').getAll())),
    async remove(id) {
      return run(['chapters', 'pages', 'meta'], 'readwrite', async tx => {
        const rows = tx.objectStore('chapters'), meta = tx.objectStore('meta'), old = await request(rows.get(id));
        const usage = Number(await request(meta.get('bytes'))) || 0;
        tx.objectStore('pages').delete(pageRange(id)); rows.delete(id); meta.put(Math.max(0, usage - (old?.bytes || 0)), 'bytes');
      });
    },
  };
}
