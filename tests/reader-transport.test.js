import test from 'node:test';
import assert from 'node:assert/strict';
import {fetchReaderImage} from '../web/reader-transport.js';

const URL = '/api/image?url=synthetic';
const quota = 'Komiic 今日图片配额已用尽，请稍后再试或在源站查看';

test('a JSON quota failure is readable and uses exactly one request without retry', async () => {
  let calls = 0;
  const fetchImpl = async () => {calls++; return new Response(JSON.stringify({error: quota}), {status: 502, headers: {'Content-Type': 'application/json; charset=utf-8'}});};
  await assert.rejects(fetchReaderImage(URL, {fetchImpl}), {message: quota});
  assert.equal(calls, 1);
});

test('non-JSON HTTP errors report status without exposing raw upstream HTML', async () => {
  let calls = 0;
  const fetchImpl = async () => {calls++; return new Response('<html>private error body</html>', {status: 403, headers: {'Content-Type': 'text/html'}});};
  await assert.rejects(fetchReaderImage(URL, {fetchImpl}), error => /HTTP 403/.test(error.message) && !/private|html/.test(error.message));
  assert.equal(calls, 1);
});

test('malformed JSON and structured error objects use a useful HTTP fallback', async () => {
  for (const body of ['broken json', JSON.stringify({error: {stack: 'private'}})]) {
    await assert.rejects(fetchReaderImage(URL, {fetchImpl: async () => new Response(body, {status: 502, headers: {'Content-Type': 'application/json'}})}), /HTTP 502/);
  }
});

test('server messages stay bounded plain text with no control characters', async () => {
  const fetchImpl = async () => new Response(JSON.stringify({error: '  配额\u0000已用尽\n' + '请稍后'.repeat(100)}), {status: 429, headers: {'Content-Type': 'application/problem+json'}});
  await assert.rejects(fetchReaderImage(URL, {fetchImpl}), error => error.message.startsWith('配额 已用尽 ') && error.message.length === 240 && !/[\u0000-\u001f]/.test(error.message));
});

test('a valid image becomes one Blob from one request with the caller signal', async () => {
  const bytes = new Uint8Array([137, 80, 78, 71, 1, 2, 3]);
  const controller = new AbortController(); let calls = 0;
  const fetchImpl = async (url, options) => {
    calls++; assert.equal(url, URL); assert.equal(options.signal, controller.signal); assert.equal(options.credentials, 'same-origin');
    return new Response(bytes, {headers: {'Content-Type': 'image/png'}});
  };
  const blob = await fetchReaderImage(URL, {signal: controller.signal, fetchImpl});
  assert.equal(calls, 1); assert.equal(blob.type, 'image/png');
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), bytes);
});

test('an already cancelled image never starts a request', async () => {
  const controller = new AbortController(); controller.abort(); let calls = 0;
  await assert.rejects(fetchReaderImage(URL, {signal: controller.signal, fetchImpl: async () => {calls++;}}), {name: 'AbortError'});
  assert.equal(calls, 0);
});

test('cancellation reaches the pending fetch and does not become an error message or retry', async () => {
  const controller = new AbortController(); let calls = 0;
  const fetchImpl = (url, {signal}) => {
    calls++;
    return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), {once: true}));
  };
  const result = fetchReaderImage(URL, {signal: controller.signal, fetchImpl});
  controller.abort();
  await assert.rejects(result, {name: 'AbortError'});
  assert.equal(calls, 1);
});

test('a delayed Blob after cancellation cannot become an image resource', async () => {
  const controller = new AbortController(); let finish, calls = 0;
  const body = new Promise(resolve => {finish = resolve;});
  const fetchImpl = async () => {calls++; return {ok: true, headers: new Headers({'Content-Type': 'image/png'}), blob: () => body};};
  const result = fetchReaderImage(URL, {signal: controller.signal, fetchImpl});
  await Promise.resolve(); controller.abort(); finish(new Blob(['synthetic'], {type: 'image/png'}));
  await assert.rejects(result, {name: 'AbortError'});
  assert.equal(calls, 1);
});

test('cancellation while reading JSON retains AbortError semantics', async () => {
  const controller = new AbortController(); let finish;
  const body = new Promise(resolve => {finish = resolve;});
  const fetchImpl = async () => ({ok: false, status: 502, headers: new Headers({'Content-Type': 'application/json'}), json: () => body});
  const result = fetchReaderImage(URL, {signal: controller.signal, fetchImpl});
  await Promise.resolve(); controller.abort(); finish({error: quota});
  await assert.rejects(result, {name: 'AbortError'});
});

test('unsupported and empty success responses never reach image decoding', async () => {
  await assert.rejects(fetchReaderImage(URL, {fetchImpl: async () => new Response('<html>login</html>', {headers: {'Content-Type': 'text/html'}})}), /未返回可显示的图片/);
  await assert.rejects(fetchReaderImage(URL, {fetchImpl: async () => new Response('', {headers: {'Content-Type': 'image/png'}})}), /空图片/);
});

test('network and body failures do not leak request URLs or retry', async () => {
  let calls = 0;
  await assert.rejects(fetchReaderImage(URL, {fetchImpl: async () => {calls++; throw new TypeError('private URL');}}), {message: '图片请求失败，请检查网络后重试'});
  assert.equal(calls, 1);
  await assert.rejects(fetchReaderImage(URL, {fetchImpl: async () => ({ok: true, headers: new Headers({'Content-Type': 'image/jpeg'}), blob: async () => {throw new TypeError('private body');}})}), {message: '图片内容接收失败，请重试'});
});
