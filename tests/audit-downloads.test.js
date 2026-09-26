import test from 'node:test';
import assert from 'node:assert/strict';
import {imageIdentity, sameImageManifest, localChapterCatalog} from '../web/download-store.js';
import {chapterNavigation} from '../web/reader-model.js';

test('temporary MangaBZ keys can change but page, source, chapter and transforms remain part of identity', () => {
  const old = 'https://image.mangabz.com/1/91/11380/1.jpg?cid=11380&key=old';
  assert.equal(imageIdentity('mangabz', old), imageIdentity('mangabz', old.replace('key=old', 'key=new')));
  for (const different of [old.replace('/1.jpg', '/2.jpg'), old.replace('cid=11380','cid=20'), old+'&width=100']) assert.notEqual(imageIdentity('mangabz', old), imageIdentity('mangabz', different));
  assert.equal(imageIdentity('unknown',old),old);
  const foreign = old.replace('image.mangabz.com','image.mangabz.com.evil.test');
  assert.equal(imageIdentity('mangabz',foreign),foreign);
  assert.equal(sameImageManifest('mangabz',[old,old.replace('1.jpg','2.jpg')],[old.replace('1.jpg','2.jpg'),old]),false);
  assert.equal(sameImageManifest('mangabz',[old],[old,old]),false);
});

test('a downloaded chapter removed from the remote catalog remains reachable without guessing a position', () => {
  const saved = {url: 'old-1', name: '第1话', language: 'zh', sequenceId: 'zh:main'};
  const online = [{url: 'new-2', name: '第2话', sequenceId: 'zh:main'}, {url: 'en-1', name: 'Chapter 1', sequenceId: 'en:main'}];
  const result = localChapterCatalog(online, saved);
  assert.equal(online.length, 2);
  assert.equal(saved.localOnly, undefined);
  assert.equal(result.at(-1).url, saved.url);
  assert.equal(result.at(-1).localOnly, true);
  assert.equal(result.at(-1).sequenceId, saved.sequenceId);
  assert.deepEqual(chapterNavigation(result, 2), {previous: null, next: null, sequenceId: 'zh:main', count: 1});
  assert.equal(chapterNavigation(result, 0).next, null, 'online sequence cannot enter an orphan appended for local reachability');
  assert.equal(chapterNavigation(result, 1).previous, null, 'language sequences stay separate');
});

test('known online ordering is retained and local chapters are not duplicated or silently reclassified', () => {
  const saved = {url: 'one', name: '旧章名', sequenceId: 'old'};
  const online = [{url: 'one', name: '新章名', sequenceId: 'new'}, {url: 'two', name: '第2话', sequenceId: 'new'}];
  assert.deepEqual(localChapterCatalog(online, saved), online);
  assert.equal(chapterNavigation(localChapterCatalog(online, saved), 0).next, 1);
  const local = localChapterCatalog([], saved);
  assert.equal(local.length, 1); assert.equal(local[0].localOnly, true);
  assert.deepEqual(localChapterCatalog(local, saved), local);
  const legacy = localChapterCatalog([{url: 'new-2'}], {url: 'old-1'});
  assert.equal(chapterNavigation(legacy, 0).next, null);
  assert.equal(chapterNavigation(legacy, 1).previous, null);
});
