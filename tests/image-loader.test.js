import test from 'node:test';
import assert from 'node:assert/strict';
import {createImageLoader} from '../web/image-loader.js';
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
