import test from 'node:test';
import assert from 'node:assert/strict';
import {chapterNavigation, readerPreferencesForBook, normalizeReaderPreferences, readerPageWidth, clampReaderZoom} from '../web/reader-model.js';
import {sourceEntryKey} from '../web/book-identity.js';

const chapter = (number, language) => ({name: `第 ${number} 章`, url: `https://fixture.invalid/${language}/${number}`, language, sequenceId: `language:${language}`});

test('NamiComi language blocks stop at their own final chapter without entering the next translation', () => {
  const chapters = ['en', 'es-419'].flatMap(language => Array.from({length: 27}, (_, index) => chapter(index + 1, language)));
  assert.deepEqual(chapterNavigation(chapters, 26), {previous: 25, next: null, sequenceId: 'language:en', count: 27});
  assert.deepEqual(chapterNavigation(chapters, 27), {previous: null, next: 28, sequenceId: 'language:es-419', count: 27});
  assert.equal(chapters.length, 54);
});

test('interleaved explicit sequences navigate only their own chapters and retain unknown language boundaries', () => {
  const chapters = [chapter(1, 'en'), chapter(1, 'es-419'), chapter(2, 'en'), {...chapter(1, ''), sequenceId: 'language:unknown'}, chapter(2, 'es-419')];
  assert.equal(chapterNavigation(chapters, 0).next, 2);
  assert.equal(chapterNavigation(chapters, 4).previous, 1);
  assert.deepEqual(chapterNavigation(chapters, 3), {previous: null, next: null, sequenceId: 'language:unknown', count: 1});
});

test('legacy volume and extra groups preserve array navigation instead of inventing separate sequences', () => {
  const chapters = [{name: '第1话', group: '正文'}, {name: '第1卷', group: '单行本'}, {name: '番外', group: '番外'}];
  assert.deepEqual(chapterNavigation(chapters, 1), {previous: 0, next: 2, sequenceId: '', count: 3});
  assert.deepEqual(chapterNavigation([], 0), {previous: null, next: null, sequenceId: '', count: 0});
  assert.equal(chapterNavigation(chapters, 2).next, null);
});

test('one book overrides global preferences without leaking mode, fit or zoom to another book', () => {
  const defaults = normalizeReaderPreferences({theme: 'light', width: 960});
  const own = {...defaults, mode: 'paged', direction: 'rtl', fit: 'page', zoom: 2};
  const records = {'source::one': own};
  assert.deepEqual(readerPreferencesForBook(defaults, records, 'source::one'), own);
  assert.deepEqual(readerPreferencesForBook(defaults, records, 'source::two'), defaults);
  assert.deepEqual(readerPreferencesForBook(defaults, {'source::one': null}, 'source::one'), defaults);
  assert.deepEqual(readerPreferencesForBook(defaults, [], 'source::one'), defaults);
});

test('verified old and new source URLs share their book preference key', () => {
  const mid = btoa('m:101').replaceAll('=', '');
  const oldBook = {siteId: 'hipmh', detailUrl: `https://m.hipmh.com/works/${mid}-sample`};
  const newBook = {siteId: 'hipmh', detailUrl: `https://reader.hipmh.top/manga/${mid}`};
  const own = normalizeReaderPreferences({mode: 'paged', direction: 'rtl', zoom: 1.5});
  const records = {[sourceEntryKey(oldBook)]: own};
  assert.equal(sourceEntryKey(oldBook), sourceEntryKey(newBook));
  assert.deepEqual(readerPreferencesForBook({}, records, sourceEntryKey(newBook)), own);
});

test('old v2 paged preferences migrate to whole-page fitting while continuous stays fit-width', () => {
  assert.equal(normalizeReaderPreferences({mode: 'paged'}).fit, 'page');
  assert.equal(normalizeReaderPreferences({mode: 'continuous'}).fit, 'width');
  assert.equal(normalizeReaderPreferences({fit: 'broken', zoom: 'bad'}).zoom, 1);
  assert.equal(normalizeReaderPreferences({fit: 'width', zoom: 2.25}).zoom, 2.25);
});

test('fit-width, whole-page and magnification produce different usable sizes on a 393px phone', () => {
  const viewport = {ratio: 2, viewportWidth: 393, viewportHeight: 540, preferredWidth: 800};
  assert.equal(readerPageWidth({...viewport, fit: 'width'}), 393);
  assert.equal(readerPageWidth({...viewport, fit: 'page'}), 270);
  assert.equal(readerPageWidth({...viewport, fit: 'width', zoom: 2}), 786);
  assert.equal(readerPageWidth({...viewport, fit: 'page', zoom: 2}), 540);
  assert.equal(readerPageWidth({...viewport, ratio: 10, fit: 'page'}), 54);
});

test('zoom stays bounded while preserving deliberate quarter steps', () => {
  for (const value of [null, undefined, NaN, Infinity, -1, 'bad']) assert.equal(clampReaderZoom(value), 1);
  assert.equal(clampReaderZoom(4), 3);
  assert.equal(clampReaderZoom(.5), 1);
  assert.equal(clampReaderZoom(1.25), 1.25);
});
