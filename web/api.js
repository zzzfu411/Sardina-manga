export async function api(path, body, signal) {
  const controller = new AbortController(); let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, {once: true});
  const timeout = setTimeout(() => {timedOut = true; controller.abort();}, 45000);
  try {
    controller.signal.throwIfAborted();
    const response = await fetch(path, {method: body ? 'POST' : 'GET', headers: body ? {'Content-Type': 'application/json'} : {}, body: body ? JSON.stringify(body) : undefined, signal: controller.signal});
    let payload;
    try {payload = await response.json();}
    catch (error) {if (error.name !== 'SyntaxError') throw error;}
    controller.signal.throwIfAborted();
    if (response.ok) {
      if (!payload || !Object.hasOwn(payload, 'data')) throw new Error('服务返回的数据不完整，请稍后重试');
      return payload.data;
    }
    const message = typeof payload?.error === 'string' ? payload.error : '';
    if (/^HTTP Error 403:/.test(message)) throw new Error('漫画源暂时拒绝访问，请稍后重试或返回详情换源');
    if (/^HTTP Error 429:/.test(message) || response.status === 429) throw new Error('请求过于频繁，请稍后重试');
    if ([502, 503, 504].includes(response.status) && (!message || /^HTTP Error (502|503|504):|timed out/i.test(message))) {
      throw new Error(`漫画源或服务暂时不可用（HTTP ${response.status}），请重试或返回详情换源`);
    }
    throw new Error(message || `请求失败（HTTP ${response.status}），请稍后重试`);
  } catch (error) {
    if (timedOut) throw new Error('连接超时，请重试或切换漫画源');
    if (error.name !== 'AbortError' && navigator.onLine === false) throw new Error('当前离线，请阅读已下载章节');
    if (error instanceof TypeError) throw new Error('网络请求失败，请检查连接后重试');
    throw error;
  } finally {clearTimeout(timeout); signal?.removeEventListener('abort', abort);}
}
