import test from 'node:test';
import assert from 'node:assert/strict';
import {createImageLoader, imageRequestPurpose} from '../web/image-loader.js';
const tick = () => new Promise(resolve => setImmediate(resolve));
const blob = () => new Blob(['abc'], {type: 'image/png'});

test('one shared request survives cancellation of just its reader consumer', async () => {
  let finish, calls = 0, signal;
  const loader = createImageLoader({fetchImage: (url, options) => {calls++; signal = options.signal; return new Promise(resolve => {finish = resolve;});}});
  const controller = new AbortController();
  const reading = loader.load('same', {signal: controller.signal}), download = loader.load('same', {priority: 2});
  await tick(); controller.abort(); await assert.rejects(reading, {name: 'AbortError'});
  assert.equal(signal.aborted, false); finish(blob()); await download; await tick();
  await loader.load('same'); assert.equal(calls, 1); assert.equal(loader.stats().active, 0);
});

test('visible pages precede queued download work and background cannot occupy all slots', async () => {
  const starts = [], finishes = new Map();
  const loader = createImageLoader({limit: 2, backgroundLimit: 1, fetchImage: url => {starts.push(url); return new Promise(resolve => finishes.set(url, resolve));}});
  const first = loader.load('download-1', {priority: 2}), second = loader.load('download-2', {priority: 2});
  const prefetch = loader.load('prefetch', {priority: 1}), visible = loader.load('visible');
  await tick(); assert.deepEqual(starts, ['download-1', 'prefetch']);
  finishes.get('prefetch')(blob()); await prefetch; await tick(); assert.equal(starts.at(-1), 'visible');
  finishes.get('visible')(blob()); finishes.get('download-1')(blob()); await Promise.all([visible, first]); await tick();
  assert.equal(starts.at(-1), 'download-2'); finishes.get('download-2')(blob()); await second; await tick();
  assert.equal(loader.stats().active, 0);
});

test('abandoned queued requests never hit the network and last consumer cancels active work', async () => {
  const starts = [];
  const loader = createImageLoader({limit: 1, fetchImage: (url, {signal}) => new Promise((resolve, reject) => {
    starts.push(url); signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')));
  })});
  const one = new AbortController(), two = new AbortController();
  const first = loader.load('first', {signal: one.signal}), queued = loader.load('queued', {signal: two.signal});
  await tick(); two.abort(); await assert.rejects(queued, {name: 'AbortError'}); one.abort(); await assert.rejects(first, {name: 'AbortError'}); await tick();
  assert.deepEqual(starts, ['first']); assert.equal(loader.stats().active, 0); assert.equal(loader.stats().bytes, 0);
});

test('byte-limited LRU cache evicts old blobs and never caches failed or oversized images', async () => {
  const calls = [];
  const loader = createImageLoader({maxBytes: 5, fetchImage: async url => {calls.push(url); if (url === 'bad') throw new Error('bad'); return blob();}});
  await loader.load('one'); await loader.load('two'); await loader.load('two'); await loader.load('one');
  assert.deepEqual(calls, ['one', 'two', 'one']); assert.equal(loader.stats().bytes, 3);
  await assert.rejects(loader.load('bad')); await tick(); await assert.rejects(loader.load('bad')); assert.equal(calls.filter(url => url === 'bad').length, 2);
  const oversized = createImageLoader({fetchImage: async () => new Blob([new Uint8Array(12 * 1024 * 1024 + 1)])});
  await assert.rejects(oversized.load('large'), /12 MB/); assert.equal(oversized.stats().cached, 0);
});

test('request timeout releases its slot and permits subsequent reading', async () => {
  const loader = createImageLoader({timeoutMs: 20, limit: 1, fetchImage: (url, {signal}) => url === 'ok' ? Promise.resolve(blob()) : new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError'))))});
  await assert.rejects(loader.load('slow'), /超时/); assert.equal((await loader.load('ok')).size, 3);
});

test('a decoder can discard just the rejected blob and retry the same address immediately', async () => {
  const calls = [], bad = new Blob(['not-an-image']), good = blob();
  const loader = createImageLoader({fetchImage: async url => {calls.push(url); return url === 'broken' && calls.filter(value => value === url).length === 1 ? bad : good;}});
  await loader.load('already-read');
  assert.equal(await loader.load('broken'), bad);
  assert.equal(loader.invalidate('broken', bad), true);
  assert.equal(await loader.load('broken'), good);
  assert.equal(loader.invalidate('broken', bad), false, 'late failure cannot discard replacement bytes');
  assert.equal(await loader.load('already-read'), good);
  assert.deepEqual(calls, ['already-read', 'broken', 'broken']);
  assert.equal(loader.stats().bytes, good.size * 2);
});

test('proxy purposes reflect foreground, prefetch and download without splitting their cache identity', async () => {
  const urls = [], requests = [];
  const loader = createImageLoader({fetchImage: async (url, options) => {urls.push(new URL(url, 'http://localhost')); requests.push(options.priority); return blob();}});
  const base = '/api/image?site=mangabz&url=https%3A%2F%2Fimage.mangabz.com%2Fone.png';
  await loader.load(base, {priority: 2});
  await loader.load(base + '&purpose=reader');
  assert.equal(urls.length, 1);
  assert.equal(urls[0].searchParams.get('purpose'), 'download');
  assert.equal(urls[0].searchParams.get('url'), 'https://image.mangabz.com/one.png');
  await loader.load(base.replace('one.png', 'two.png'), {priority: 1});
  await loader.load(base.replace('one.png', 'three.png'));
  assert.deepEqual(urls.map(url => url.searchParams.get('purpose')), ['download', 'prefetch', 'reader']);
  assert.deepEqual(requests, ['low', 'low', 'high']);
  assert.equal(imageRequestPurpose(0), 'reader');
  assert.equal(imageRequestPurpose(1), 'prefetch');
  assert.equal(imageRequestPurpose(2), 'download');
});

test('a queued prefetch promoted to visible is dispatched to the server as reader work', async () => {
  let finish;
  const seen = [];
  const loader = createImageLoader({limit: 1, fetchImage: async url => {
    if (url === 'hold') return new Promise(resolve => {finish = resolve;});
    seen.push(new URL(url, 'http://localhost').searchParams.get('purpose')); return blob();
  }});
  const first = loader.load('hold');
  const url = '/api/image?site=mangabz&url=https%3A%2F%2Fimage.mangabz.com%2Fone.png';
  const second = loader.load(url, {priority: 1});
  loader.promote(url); await tick(); finish(blob()); await Promise.all([first, second]);
  assert.deepEqual(seen, ['reader']);
});
