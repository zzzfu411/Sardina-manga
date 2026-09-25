import test from 'node:test';
import assert from 'node:assert/strict';
import {imageIdentity, sameImageManifest} from '../web/download-store.js';

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
