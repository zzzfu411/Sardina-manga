/** One cancellable request per page. The caller owns image decoding and Blob URLs. */
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif']);

function checkAbort(signal) {
  if (signal?.aborted) throw new DOMException('图片请求已取消', 'AbortError');
}

function safeMessage(value) {
  return typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 240) : '';
}

async function responseError(response, signal) {
  let message = '';
  const type = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
  if (type === 'application/json' || type.endsWith('+json')) {
    try {
      const payload = await response.json();
      message = safeMessage(payload?.error);
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      // A proxy error page or malformed JSON must not become raw page content.
    }
  } else {
    try {await response.body?.cancel();} catch { /* No error-body text is needed. */ }
  }
  checkAbort(signal);
  return new Error(message || `图片加载失败（HTTP ${response.status}），请重试或切换漫画源`);
}

export async function fetchReaderImage(url, {signal, priority = 'auto', fetchImpl = globalThis.fetch} = {}) {
  checkAbort(signal);
  let response;
  try {
    response = await fetchImpl(url, {signal, priority, credentials: 'same-origin', headers: {Accept: 'image/*, application/json'}});
  } catch (error) {
    checkAbort(signal);
    if (error?.name === 'AbortError') throw error;
    throw new Error('图片请求失败，请检查网络后重试');
  }
  checkAbort(signal);
  if (!response.ok) throw await responseError(response, signal);
  const type = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
  if (!IMAGE_TYPES.has(type)) {
    try {await response.body?.cancel();} catch { /* Release an unsupported response. */ }
    checkAbort(signal);
    throw new Error('漫画源未返回可显示的图片，请重试或切换漫画源');
  }
  let blob;
  try {
    blob = await response.blob();
  } catch (error) {
    checkAbort(signal);
    if (error?.name === 'AbortError') throw error;
    throw new Error('图片内容接收失败，请重试');
  }
  // Also guard adapters/mocks that settle successfully after cancellation.
  checkAbort(signal);
  if (!blob.size) throw new Error('漫画源返回了空图片，请重试或切换漫画源');
  return blob;
}
