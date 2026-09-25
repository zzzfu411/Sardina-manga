import test from 'node:test';
import assert from 'node:assert/strict';
import {bookKey, readingState, readTime, rememberBook, setReadingState, libraryCounts, filterLibrary, continueBook, parseShelfBackup, mergeShelfBackup} from '../web/library-model.js';
import {catalogSnapshot, catalogStateAfterCheck} from '../web/library-updates.js';

const base = {siteId: 'fixture', siteName: '测试源', title: '三月的狮子', detailUrl: 'https://comic.example/book/1', openedAt: 100};
const reading = {...base, chapterUrl: 'https://comic.example/chapter/1', chapterName: '第一话', page: 12, pageOffset: 0.63, totalPages: 40};

test('legacy books classify without changing their stored reading location', () => {
  assert.equal(readingState(base), 'later');
  assert.equal(readingState(reading), 'reading');
  assert.equal(readingState({...reading, readingState: 'invalid'}), 'reading');
  assert.deepEqual(libraryCounts([base, reading, {...reading, readingState: 'finished'}]), {all: 3, reading: 1, later: 1, finished: 1});
  assert.equal(reading.page, 12);
});

test('changing a classification changes neither progress nor recent reading order', () => {
  const marked = setReadingState(reading, 'finished', 900);
  assert.deepEqual({...marked, readingState: undefined, stateChangedAt: undefined}, {...reading, readingState: undefined, stateChangedAt: undefined});
  assert.equal(readTime(marked), 100);
  assert.equal(continueBook([marked]), null);
});

test('stale detail and reader snapshots cannot erase newer progress or chosen status', () => {
  const latest = {...reading, readAt: 500, page: 22, readingState: 'finished', stateChangedAt: 600};
  const result = rememberBook(latest, {...reading, title: '新标题'}, {}, 1000);
  assert.equal(result.page, 22);
  assert.equal(result.readAt, 500);
  assert.equal(result.readingState, 'finished');
  assert.equal(result.title, '新标题');
  assert.equal(result.openedAt, 1000);
});

test('a details view pins the legacy read timestamp and real reading promotes a planned book', () => {
  const detail = rememberBook(reading, base, {}, 500);
  assert.equal(detail.readAt, 100);
  const result = rememberBook(base, base, {chapterUrl: reading.chapterUrl, page: 4, pageOffset: 0.5}, 600);
  assert.equal(result.readingState, 'reading');
  assert.equal(result.readAt, 600);
  assert.equal(result.stateChangedAt, 600);
});

test('continue entry uses actual reading time and ignores completed or unstarted books', () => {
  const newer = {...reading, detailUrl: 'https://comic.example/book/2', readAt: 200};
  const viewed = {...reading, readAt: 100, openedAt: 900};
  assert.equal(bookKey(continueBook([base, viewed, newer, {...reading, readAt: 999, readingState: 'finished'}])), bookKey(newer));
});

test('library search and filters combine with title sorting without mutating the shelf', () => {
  const books = [reading, {...base, title: 'Alpha', author: '羽海野', readingState: 'later'}];
  assert.deepEqual(filterLibrary(books, {query: 'ＡＬＰＨＡ', state: 'later'}).map(b => b.title), ['Alpha']);
  assert.equal(filterLibrary(books, {query: '羽海野'}).length, 1);
  assert.equal(filterLibrary(books, {query: '测试源', state: 'finished'}).length, 0);
  assert.equal(books[0], reading);
});

test('v1 backups retain new optional status fields and old fractional progress', () => {
  const result = parseShelfBackup({version: 1, books: [{...reading, readingState: 'finished', stateChangedAt: 800}]}, ['fixture']);
  assert.equal(result[0].pageOffset, 0.63);
  assert.equal(result[0].totalPages, 40);
  assert.equal(result[0].readingState, 'finished');
  assert.equal(result[0].stateChangedAt, 800);
  assert.equal(result[0].readAt, 100);
  assert.equal(parseShelfBackup({version: 1, books: [base]}, ['other'])[0].siteId, 'fixture');
  assert.throws(() => parseShelfBackup({version: 1, books: [{...base, detailUrl: 'javascript:bad'}]}, ['fixture']), /无效/);
});

test('backup merge keeps progress and classification clocks independent', () => {
  const current = {...reading, readAt: 900, openedAt: 900, readingState: 'reading', stateChangedAt: 100};
  const incoming = {...reading, readAt: 100, page: 1, openedAt: 1000, readingState: 'finished', stateChangedAt: 1000};
  const merged = mergeShelfBackup([current], [incoming])[0];
  assert.equal(merged.page, 12);
  assert.equal(merged.pageOffset, 0.63);
  assert.equal(merged.readAt, 900);
  assert.equal(merged.readingState, 'finished');
  assert.equal(merged.stateChangedAt, 1000);
});

test('a first successful reading cannot be demoted by an older planned-book backup', () => {
  const first = rememberBook(undefined, base, {chapterUrl: reading.chapterUrl, page: 1}, 1000);
  const older = {...base, openedAt: 400, readingState: 'later', stateChangedAt: 500};
  assert.equal(first.stateChangedAt, 1000);
  const merged = mergeShelfBackup([first], [older])[0];
  assert.equal(merged.readingState, 'reading');
  assert.equal(merged.page, 1);
});

test('a newer legacy backup progresses the chapter without mixing old page metadata', () => {
  const incoming = {...base, openedAt: 800, chapterUrl: 'https://comic.example/chapter/2', page: 2};
  const merged = mergeShelfBackup([reading], [incoming])[0];
  assert.equal(merged.chapterUrl, incoming.chapterUrl);
  assert.equal(merged.page, 2);
  assert.equal(merged.pageOffset, undefined);
  assert.equal(merged.totalPages, undefined);
  assert.equal(merged.readingState, 'reading');
});

const catalog = (id, now) => catalogStateAfterCheck(null, catalogSnapshot({chapters: [{url: `https://comic.example/chapter/${id}`}]}), now);

test('stale reader snapshots cannot overwrite newer catalogue metadata or a cleared notice at the same time', () => {
  const pending = {...catalog(2, 900), change: 'new', changedAt: 900};
  const latest = {...reading, catalogState: {...pending, change: ''}};
  const saved = rememberBook(latest, {...reading, catalogState: pending}, {chapterUrl: reading.chapterUrl, page: 21}, 1000);
  assert.equal(saved.page, 21);
  assert.equal(saved.catalogState.change, '');
  const older = rememberBook(latest, {...reading, catalogState: catalog(1, 100)}, {}, 1100);
  assert.deepEqual(older.catalogState, latest.catalogState);
});

test('v1 backups retain valid optional catalogue metadata and discard malformed optional records', () => {
  const valid = catalog(2, 900);
  const imported = parseShelfBackup({version: 1, books: [{...reading, catalogState: {...valid, urls: ['not-retained']}}]}, ['fixture'])[0];
  assert.deepEqual(imported.catalogState, valid);
  assert.equal(imported.pageOffset, 0.63);
  const malformed = parseShelfBackup({version: 1, books: [{...reading, catalogState: {...valid, chapterCount: -1}}]}, ['fixture'])[0];
  assert.equal(malformed.catalogState, undefined);
  assert.equal(malformed.page, reading.page);
});

test('catalogue import clock is independent of progress, details and classification clocks', () => {
  const current = {...reading, readAt: 1000, openedAt: 1100, readingState: 'reading', stateChangedAt: 900, catalogState: catalog(1, 100)};
  const incoming = {...reading, page: 1, readAt: 100, openedAt: 100, readingState: 'finished', stateChangedAt: 100, catalogState: catalog(2, 1200)};
  const merged = mergeShelfBackup([current], [incoming])[0];
  assert.equal(merged.page, reading.page);
  assert.equal(merged.readAt, 1000);
  assert.equal(merged.readingState, 'reading');
  assert.deepEqual(merged.catalogState, incoming.catalogState);
  const sameTimePending = {...incoming, catalogState: {...incoming.catalogState, change: 'new', changedAt: 1200}};
  assert.equal(mergeShelfBackup([merged], [sameTimePending])[0].catalogState.change, '');
});
