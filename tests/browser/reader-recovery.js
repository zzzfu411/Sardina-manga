async (page, step = () => {}) => {
  const base = '__BASE_URL__', output = '__OUTPUT_DIR__';
  const assert = (ok, message) => {if (!ok) throw new Error(message);};
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const book = {siteId: 'mangabz', title: '章节恢复测试', detailUrl: 'https://www.mangabz.com/990041bz/', coverUrl: ''};
  const chapter = {name: '第1话', url: 'https://www.mangabz.com/m990041/'};
  const urls = Array.from({length: 8}, (_, i) => `https://image.mangabz.com/recovery-${i}.png`);
  const empty = [0, 2, 3, 7];
  let calls = 0, imageCalls = 0, failure = 'once';
  const json = (route, data) => route.fulfill({contentType: 'application/json', body: JSON.stringify({data})});
  await page.route('**/api/details', route => json(route, {...book, chapters: [chapter], catalogCompleteness: 'complete'}));
  await page.route('**/api/recommendations**', route => json(route, {items: [], origins: [], warnings: [], nextBatch: null}));
  await page.route('**/api/book-metadata', route => json(route, {}));
  await page.route('**/api/chapter-images', route => {
    calls++;
    if (failure === 'always' || failure === 'once' && calls === 1) return route.fulfill({status: 502, contentType: 'text/plain', body: 'error code: 502\n'});
    return json(route, {images: urls});
  });
  await page.route('**/api/image?**', route => {
    const match = decodeURIComponent(route.request().url()).match(/recovery-(\d+)/);
    if (match) imageCalls++;
    const index = Number(match?.[1]);
    const fixture = empty.includes(index) ? 'reader-empty.png' : index === 5 ? 'reader-pixel.png' : 'reader-page.png';
    return route.fulfill({contentType: 'image/png', path: '__ROOT__/tests/fixtures/' + fixture});
  });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'onLine', {configurable: true, get: () => !sessionStorage.getItem('offline-fixture')});
    localStorage.setItem('revyunman.reader.preferences.v2', JSON.stringify({mode: 'continuous', prefetch: 'off'}));
  });
  await page.setViewportSize({width: 393, height: 852});
  await page.goto(base + '/');
  const path = await page.evaluate(({book, chapter}) => {
    const encode = value => btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value)))).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
    return '/read/' + encode([book.siteId, book.detailUrl, {title: book.title}]) + '/' + encode(chapter.url);
  }, {book, chapter});
  const loaded = index => page.waitForFunction(i => document.querySelector(`.ry-reader-page[data-page="${i}"]`)?.dataset.state === 'loaded', index);
  const jump = async number => {await page.locator('#reader-page-input').fill(String(number)); await page.locator('#reader-jump').click();};
  step('manual-retry-text-502-and-skip-leading-placeholder');
  await page.goto(base + path); await page.locator('.ry-chapter-error').waitFor();
  assert(calls === 1, 'chapter failure triggered an automatic retry');
  await page.locator('#reader-retry-chapter').click(); await loaded(1);
  assert(calls === 2, 'manual retry did not issue exactly one request');
  assert(await page.locator('.ry-reader-page').count() === 8, 'source indices were removed');
  step('collapse-consecutive-placeholders');
  await jump(3); await loaded(4);
  const seam = await page.evaluate(() => {
    const p = i => document.querySelector(`.ry-reader-page[data-page="${i}"]`);
    return {heights: [0, 2, 3].map(i => p(i).getBoundingClientRect().height), gap: p(4).offsetTop - p(1).offsetTop - p(1).offsetHeight};
  });
  assert(seam.heights.every(height => height === 0) && Math.abs(seam.gap) <= 1, 'placeholder still leaves a gap: ' + JSON.stringify(seam));
  await page.screenshot({path: output + '/reader-recovery-continuous.png'});
  step('paged-navigation-preserves-real-pixel-and-skips-empty');
  await page.locator('#reader-settings-open').click(); await page.locator('#reader-mode-paged').check(); await page.locator('#reader-settings-close').click();
  await page.locator('#reader-prev-page').click(); await loaded(1);
  assert(await page.locator('#reader-page-input').inputValue() === '2', 'backward turn landed on a placeholder');
  await page.locator('#reader-next-page').click(); await loaded(4);
  await page.locator('#reader-next-page').click(); await loaded(5);
  assert(await page.locator('.ry-reader-page[data-page="5"]').isVisible(), 'opaque 1×1 image was discarded');
  await jump(8); await loaded(6);
  assert(await page.locator('#reader-page-input').inputValue() === '7', 'trailing placeholder did not return to content');
  assert(await page.locator('#reader-next-page').isDisabled(), 'trailing placeholder remains a next page');
  step('read-existing-download-and-restore-old-placeholder-index');
  await page.goto(base + '/');
  await page.evaluate(async ({book, chapter, urls}) => {
    const {createDownloadStore} = await import('/download-store.js');
    const store = createDownloadStore(), record = await store.prepare({book, chapter, chapters: [chapter], urls});
    for (const [index, url] of urls.entries()) {
      const response = await fetch('/api/image?siteId=mangabz&url=' + encodeURIComponent(url));
      await store.putPage(record, index, await response.blob());
    }
    await store.putCatalog(book, [chapter]); await store.close();
    const {createLibraryStore} = await import('/library-store.js');
    await createLibraryStore({storage: localStorage}).save([{...book, chapterUrl: chapter.url, chapterName: chapter.name, page: 3, pageOffset: 0.5, readAt: Date.now(), totalPages: urls.length}]);
    sessionStorage.setItem('offline-fixture', '1');
  }, {book, chapter, urls});
  const before = {calls, imageCalls};
  await page.goto(base + path); await loaded(4);
  assert(await page.locator('#reader-page-input').inputValue() === '5', 'saved placeholder index could not resume');
  assert(calls === before.calls && imageCalls === before.imageCalls, 'existing download fetched remote data: ' + JSON.stringify({before, calls, imageCalls}));
  assert((await page.locator('#reader-load-status').innerText()).includes('本地阅读'), 'download was not used');
  step('persistent-failure-is-readable-and-manual-retry-works');
  await page.goto(base + '/');
  await page.evaluate(async ({book, chapter}) => {
    sessionStorage.removeItem('offline-fixture');
    const {createDownloadStore, downloadKey} = await import('/download-store.js');
    const store = createDownloadStore(); await store.remove(downloadKey(book, chapter)); await store.close();
  }, {book, chapter});
  failure = 'always'; const beforeFailure = calls;
  await page.goto(base + path); await page.locator('.ry-chapter-error').waitFor();
  const errorText = await page.locator('.ry-chapter-error').innerText();
  assert(calls === beforeFailure + 1 && errorText.includes('HTTP 502') && !errorText.includes('expected pattern'), 'permanent failure was not bounded and readable');
  failure = 'none'; await page.locator('#reader-retry-chapter').click(); await loaded(4);
  assert(!errors.length, 'browser errors: ' + errors.join('; '));
  return {singleRequest: true, readableError: errorText, seam, pagedSkip: true, opaquePixelPreserved: true, offlineIndexResume: true, manualRetry: true, errors};
}
