import test from 'node:test';
import assert from 'node:assert/strict';
import {createLibraryStore, LEGACY_SHELF_KEY, SHELF_PREFIX} from '../web/library-store.js';
import {mergeShelfBackup, serializeShelfBackup, readShelfBackup, markChapterRead, isChapterRead, rememberBook, bookKey} from '../web/library-model.js';
import {catalogSnapshot, catalogStateAfterCheck, acknowledgeCatalog} from '../web/library-updates.js';
import {dueUpdateBooks, AUTO_UPDATE_INTERVAL} from '../web/library-auto-updates.js';
import {sourceEntryKey, sameWork} from '../web/book-identity.js';
import {buildSearchModel} from '../web/search-model.js';

const book = (id, extra = {}) => ({siteId: 'manhuagui', title: `漫画${id}`, detailUrl: `https://www.manhuagui.com/comic/${id}/`, openedAt: id, ...extra});
class Storage {
  data = new Map(); fail = false;
  get length() {return this.data.size;}
  key(index) {return [...this.data.keys()][index] ?? null;}
  getItem(key) {return this.data.get(key) ?? null;}
  setItem(key, value) {if (this.fail) throw new Error('QuotaExceededError'); this.data.set(key, String(value));}
}
const chapter = id => ({url: `https://www.manhuagui.com/comic/1/${id}.html`, name: `第${id}话`});

test('a valid >2 MB backup and 301 books survive a full serialized restore', () => {
  const books = Array.from({length: 300}, (_, i) => book(i + 1, {description: '漫'.repeat(3000)}));
  const merged = mergeShelfBackup(books, [book(301)]);
  assert.equal(merged.length, 301);
  const exported = serializeShelfBackup(merged);
  assert.ok(new TextEncoder().encode(exported).length > 2_000_000);
  const restored = mergeShelfBackup([], readShelfBackup(exported));
  assert.equal(restored.length, 301);
  assert.equal(restored.find(b => b.title === '漫画1').description, books[0].description);
  assert.equal(serializeShelfBackup(restored), exported);
});

test('verified Hip aliases share identity and preserve the latest progress; unrelated IDs do not merge', () => {
  const legacy = {siteId: 'hipmh', title: '一拳超人', detailUrl: 'https://m.hipmh.com/works/bTo0Mjk2-yi-quan-chao-ren-4288#mid=bTo0Mjk2', chapterUrl: 'https://reader.hipmh.top/chapter/c1', page: 17, readAt: 500, openedAt: 500};
  const current = {...legacy, detailUrl: 'https://reader.hipmh.top/manga/bTo0Mjk2', page: 1, readAt: 100, openedAt: 600};
  assert.equal(sourceEntryKey(legacy), sourceEntryKey(current));
  assert.equal(mergeShelfBackup([legacy], [current]).length, 1);
  assert.equal(mergeShelfBackup([legacy], [current])[0].page, 17);
  assert.notEqual(sourceEntryKey(current), sourceEntryKey({...current, detailUrl: 'https://reader.hipmh.top/manga/bTo0Mjk3'}));
  assert.notEqual(sourceEntryKey(current), sourceEntryKey({...current, detailUrl: 'https://evil.test/manga/bTo0Mjk2'}));
  assert.equal(sameWork(book(1, {title: '逆光', author: '甲'}), book(2, {title: '逆光', author: '乙'})), false);
  assert.equal(sameWork(book(1, {title: '逆光'}), book(2, {title: '逆光', author: '甲'})), false);
});

test('chapter read state is explicit, independently merged and restored with undo', () => {
  const progress = rememberBook(undefined, book(1), {chapterUrl: chapter(1).url, page: 9, totalPages: 10}, 100);
  assert.equal(isChapterRead(progress, chapter(1)), false);
  const marked = markChapterRead(progress, chapter(1), true, 200);
  const undone = markChapterRead(marked, chapter(1), false, 300);
  const other = markChapterRead(progress, chapter(2), true, 250);
  const restored = readShelfBackup(serializeShelfBackup(mergeShelfBackup([undone], [marked, other])))[0];
  assert.equal(isChapterRead(restored, chapter(1)), false);
  assert.equal(isChapterRead(restored, chapter(2)), true);
  assert.equal(restored.page, 9);
});

test('migration leaves v1 untouched and merges independent edits from stale tabs', async () => {
  const storage = new Storage(), original = JSON.stringify([book(1), book(2)]);
  storage.setItem(LEGACY_SHELF_KEY, original);
  const a = createLibraryStore({storage, now: () => 100}), b = createLibraryStore({storage, now: () => 200});
  const left = a.books, right = b.books;
  left[0] = {...left[0], chapterUrl: chapter(2).url, page: 11, readAt: 100};
  right[1] = {...right[1], readingState: 'finished', stateChangedAt: 200};
  await a.save(left); await b.save(right);
  const result = await a.sync();
  assert.equal(result.find(row => row.title === left[0].title).page, 11);
  assert.equal(result.find(row => row.title === right[1].title).readingState, 'finished');
  assert.equal(storage.getItem(LEGACY_SHELF_KEY), original);
});

test('a stale tab cannot resurrect a removed book while saving another book', async () => {
  const storage = new Storage(); storage.setItem(LEGACY_SHELF_KEY, JSON.stringify([book(1), book(2)]));
  const a = createLibraryStore({storage, now: () => 200}), b = createLibraryStore({storage, now: () => 100});
  await a.save(a.books.filter(row => row.title !== '漫画1'));
  const stale = b.books.map(row => row.title === '漫画2' ? {...row, openedAt: 100, page: 4, chapterUrl: chapter(2).url, readAt: 100} : row);
  const result = await b.save(stale);
  assert.deepEqual(result.map(row => row.title), ['漫画2']);
  assert.ok([...storage.data.keys()].some(key => key.startsWith(SHELF_PREFIX)));
});

test('corrupt legacy data remains recoverable and failed writes are observable', async () => {
  const storage = new Storage(); storage.setItem(LEGACY_SHELF_KEY, '{broken');
  const store = createLibraryStore({storage});
  assert.equal(store.issues.length, 1);
  storage.fail = true;
  await assert.rejects(store.save([book(1)]), /Quota/);
  assert.equal(storage.getItem(LEGACY_SHELF_KEY), '{broken');
  assert.equal(JSON.parse(store.recovery()).records[LEGACY_SHELF_KEY], '{broken');
  assert.equal((await store.sync()).length, 1);
  storage.fail = false;
  assert.equal((await store.save(store.books)).length, 1);
  assert.equal(createLibraryStore({storage}).books.length, 1);
});

test('near-quota legacy storage needs no full duplicate and can remove books after a failed write', async () => {
  const storage = new Storage();
  storage.setItem(LEGACY_SHELF_KEY, JSON.stringify([book(1, {description: 'a'.repeat(2100)}), book(2, {description: 'b'.repeat(2100)})]));
  const original = storage.getItem(LEGACY_SHELF_KEY);
  storage.setItem = function(key, value) {
    const proposed = new Map(this.data); proposed.set(key, value);
    if ([...proposed].reduce((total, [k, v]) => total + k.length + v.length, 0) > 7500) throw new Error('QuotaExceededError');
    this.data = proposed;
  };
  const store = createLibraryStore({storage});
  await store.save(store.books);
  assert.equal([...storage.data.keys()].filter(key => key.startsWith(SHELF_PREFIX)).length, 0);
  await assert.rejects(store.save(store.books.map(row => ({...row, description: 'x'.repeat(5000), openedAt: 900}))), /Quota/);
  assert.equal((await store.save([])).length, 0);
  assert.equal(createLibraryStore({storage}).books.length, 0);
  assert.equal(storage.getItem(LEGACY_SHELF_KEY), original);
});

test('a synchronous exit flush preserves the last progress while the Web Lock callback is deferred', async () => {
  const storage = new Storage(); storage.setItem(LEGACY_SHELF_KEY, JSON.stringify([book(1, {chapterUrl: chapter(1).url, page: 4, readAt: 100})]));
  let release;
  const locks = {request: (_name, action) => new Promise(resolve => {release = () => resolve(action());})};
  const store = createLibraryStore({storage, locks});
  const save = store.save(store.books.map(row => ({...row, page: 19, readAt: 200})));
  store.flush();
  assert.equal(createLibraryStore({storage, locks: null}).books[0].page, 19);
  await new Promise(resolve => setImmediate(resolve)); release(); await save;
});

test('complete locked directories support updates; partial or unknown directories do not', () => {
  const details = {chapters: [chapter(1), {...chapter(2), locked: true}], unavailableReason: '部分章节受限', catalogCompleteness: 'complete'};
  assert.equal(catalogSnapshot(details).chapterCount, 2);
  for (const status of ['partial', 'unknown']) assert.throws(() => catalogSnapshot({...details, catalogCompleteness: status}), /目录/);
});

test('acknowledgement clock persists through a record merge with the pending notice', () => {
  const first = catalogStateAfterCheck(null, catalogSnapshot({chapters: [chapter(1)]}), 100);
  const changed = catalogStateAfterCheck(first, catalogSnapshot({chapters: [chapter(1), chapter(2)]}), 200);
  const cleared = acknowledgeCatalog(changed, {chapters: [chapter(1), chapter(2)]}, {now: 300});
  assert.equal(mergeShelfBackup([book(1, {catalogState: changed})], [book(1, {catalogState: cleared})])[0].catalogState.change, '');
});

test('source-verified English alternate title is a primary search hit without changing work identity', () => {
  const result = buildSearchModel([{siteId: 'terra', results: [{title: '罗德岛源石记事——莱茵生命', alternateTitles: ['rhine lab'], matchedTitle: 'rhine lab', detailUrl: 'https://terra-historicus.hypergryph.com/comic/1'}]}], 'rhine lab');
  assert.equal(result.works.length, 1);
  assert.equal(result.works[0].title, '罗德岛源石记事——莱茵生命');
});

test('auto-update checks only stale started ongoing books and backs off failed attempts', () => {
  const now = AUTO_UPDATE_INTERVAL * 2;
  const started = book(1, {readingState: 'reading', chapterUrl: chapter(1).url});
  assert.equal(dueUpdateBooks([started], {now}).length, 1);
  assert.equal(dueUpdateBooks([started], {now, attempts: {[`${started.siteId}::${started.detailUrl}`]: now - 1}}).length, 0);
  assert.equal(dueUpdateBooks([book(2), {...started, status: '已完结'}, {...started, readingState: 'finished'}], {now}).length, 0);
  assert.equal(dueUpdateBooks([started], {now, availableSites: ['other']}).length, 0);
});
