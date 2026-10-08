async (page, step = () => {}) => {
  const base = '__BASE_URL__', output = '__OUTPUT_DIR__';
  const assert = (ok, message) => {if (!ok) throw new Error(message);};
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const book = {siteId: 'mangabz', siteName: '漫画巴士', title: '手机阅读交互测试', detailUrl: 'https://www.mangabz.com/990028bz/', coverUrl: ''};
  const chapters = [1, 2].map(i => ({name: `第${i}话`, url: `https://www.mangabz.com/m990028${i}/`}));
  const json = (route, data) => route.fulfill({contentType: 'application/json', body: JSON.stringify({data})});
  let failFirst = true;
  await page.route('**/api/details', route => json(route, {...book, chapters, catalogCompleteness: 'complete'}));
  await page.route('**/api/chapter-images', route => json(route, {images: Array.from({length: 12}, (_, i) => `https://image.mangabz.com/touch-${i}.png`)}));
  await page.route('**/api/recommendations**', route => json(route, {items: [], origins: [], warnings: [], nextBatch: null}));
  await page.route('**/api/book-metadata', route => json(route, {}));
  await page.route('**/api/image?**', route => {
    const url = new URL(route.request().url()).searchParams.get('url') || '';
    if (failFirst && url.includes('touch-0.png')) return route.fulfill({status: 502, contentType: 'application/json', body: JSON.stringify({error: 'fixture image unavailable'})});
    return route.fulfill({contentType: 'image/png', path: '__ROOT__/tests/fixtures/reader-page.png'});
  });
  const cdp = await page.context().newCDPSession(page);
  const viewport = async (width, height) => {
    await page.setViewportSize({width, height});
    await cdp.send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: true});
  };
  await viewport(393, 852);
  await cdp.send('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 5});
  const touch = (type, points = []) => cdp.send('Input.dispatchTouchEvent', {type, touchPoints: points.map(([id, x, y]) => ({id, x, y, radiusX: 1, radiusY: 1, force: 1}))});
  const tap = async (x = 196, y = 350, duration = 40) => {
    await touch('touchStart', [[1, x, y]]); await page.waitForTimeout(duration); await touch('touchEnd');
  };
  const settle = () => page.waitForTimeout(360);
  const hidden = () => page.locator('#reader-dialog').evaluate(root => root.classList.contains('ry-reader-focused'));
  const geometry = () => page.locator('#reader-scroll').evaluate(scroll => {
    const figure = [...scroll.querySelectorAll('.ry-reader-page')].find(el => !el.hidden && el.getBoundingClientRect().bottom > 200);
    const rect = figure.getBoundingClientRect();
    return {top: scroll.scrollTop, left: scroll.scrollLeft, height: scroll.clientHeight, page: figure.dataset.page, figureTop: rect.top, width: rect.width, pageHeight: rect.height};
  });
  const samePosition = (before, after) => {
    assert(before.page === after.page, 'toggling chrome changed the visible page');
    for (const key of ['top', 'left', 'height', 'figureTop', 'width', 'pageHeight']) assert(Math.abs(before[key] - after[key]) <= 1, `toggling chrome changed ${key}: ${before[key]} -> ${after[key]}`);
  };
  try {
    await page.goto(base + '/');
    const path = await page.evaluate(async ({book, chapters}) => {
      const {sourceEntryKey} = await import('/book-identity.js');
      const key = sourceEntryKey(book);
      localStorage.removeItem('revyunman.library.v2.book.' + encodeURIComponent(key));
      const prefs = JSON.parse(localStorage.getItem('revyunman.reader.books.v1') || '{}'); delete prefs[key];
      localStorage.setItem('revyunman.reader.books.v1', JSON.stringify(prefs));
      localStorage.setItem('revyunman.reader.preferences.v2', JSON.stringify({mode: 'continuous', fit: 'width', focused: false, zoom: 1, prefetch: 'auto'}));
      const encode = value => btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value)))).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
      return '/read/' + encode([book.siteId, book.detailUrl, {title: book.title}]) + '/' + encode(chapters[0].url);
    }, {book, chapters});
    step('retry-button-is-not-a-canvas-tap');
    await page.goto(base + path);
    await page.waitForSelector('.ry-reader-page[data-page="0"][data-state="error"]');
    const retry = await page.locator('.ry-reader-page[data-page="0"] .ry-page-retry').boundingBox();
    assert(retry.y >= 74, 'retry is covered by the header'); failFirst = false;
    await tap(retry.x + retry.width / 2, retry.y + retry.height / 2);
    await page.waitForSelector('.ry-reader-page[data-page="0"][data-state="loaded"]'); await settle();
    assert(!await hidden(), 'retry tap hid the toolbar');
    await page.evaluate(() => {
      window.readerPointerTypes = [];
      document.querySelector('#reader-scroll').addEventListener('pointerdown', event => window.readerPointerTypes.push(event.pointerType));
    });

    step('single-tap-hides-and-shows-with-stable-canvas');
    const before = await geometry(); await tap(); await settle();
    assert(await hidden(), 'touch tap did not hide chrome');
    assert(!await page.locator('.ry-reader-header').isVisible() && !await page.locator('.ry-reader-toolbar').isVisible(), 'chrome remains visible');
    assert(await page.locator('#exit-focus').evaluate(el => el.getBoundingClientRect().width <= 1 && getComputedStyle(el).clipPath !== 'none'), 'persistent floating bubble remains');
    samePosition(before, await geometry());
    await page.screenshot({path: output + '/reader-touch-hidden-393.png'});
    await tap(); await settle(); assert(!await hidden(), 'second single tap did not reveal chrome'); samePosition(before, await geometry());

    step('native-swipe-scroll-does-not-toggle-chrome');
    await touch('touchStart', [[1, 196, 600]]);
    for (let y = 580; y >= 240; y -= 20) {await touch('touchMove', [[1, 196, y]]); await page.waitForTimeout(16);}
    await page.waitForTimeout(180); await touch('touchEnd'); await page.waitForTimeout(500);
    assert(!await hidden(), 'swiping hid chrome');
    assert((await geometry()).top > before.top + 100, 'native swipe did not scroll');
    const afterScroll = await geometry(); await tap(); await settle(); assert(await hidden(), 'tap after scroll stopped responding'); samePosition(afterScroll, await geometry());

    step('long-press-and-multitouch-do-not-toggle');
    await tap(196, 350, 550); await settle(); assert(await hidden(), 'long press toggled chrome');
    await touch('touchStart', [[1, 170, 350]]);
    await touch('touchStart', [[1, 170, 350], [2, 225, 350]]);
    await touch('touchEnd', [[1, 170, 350]]); await touch('touchEnd'); await settle();
    assert(await hidden(), 'multi-finger gesture toggled chrome');
    await touch('touchStart', [[1, 196, 350]]); await touch('touchCancel'); await settle();
    assert(await hidden(), 'cancelled pointer toggled chrome');
    assert(await page.locator('#reader-scroll').evaluate(el => /pinch-zoom|manipulation|auto/.test(getComputedStyle(el).touchAction)), 'native pinch was disabled');
    await touch('touchStart', [[1, 160, 350], [2, 230, 350]]);
    for (let distance = 0; distance <= 60; distance += 10) {
      await touch('touchMove', [[1, 160 - distance, 350], [2, 230 + distance, 350]]); await page.waitForTimeout(32);
    }
    await touch('touchEnd'); await settle();
    assert(await page.evaluate(() => visualViewport.scale > 1.1), 'native pinch did not zoom the browser');
    assert(await hidden(), 'native pinch toggled chrome');
    await cdp.send('Emulation.setPageScaleFactor', {pageScaleFactor: 1}); await settle();

    step('double-tap-zooms-without-changing-chrome');
    await tap(); await page.waitForTimeout(80); await tap(); await settle();
    assert(await page.locator('#reader-dialog').getAttribute('data-zoomed') === 'true', 'double touch did not zoom');
    assert(await hidden(), 'double touch changed chrome visibility');
    await page.keyboard.press('0'); await settle();
    assert(await page.locator('#reader-dialog').getAttribute('data-zoomed') === 'false', 'zoom reset failed');
    await page.mouse.dblclick(196, 350); await settle();
    assert(await page.locator('#reader-dialog').getAttribute('data-zoomed') === 'true' && await hidden(), 'mouse double-click conflicts with chrome toggle');
    await page.mouse.move(196, 400); await page.mouse.down(); await page.mouse.move(185, 330, {steps: 6}); await page.mouse.up(); await settle();
    assert(await hidden(), 'mouse pan toggled chrome'); await page.keyboard.press('0');

    step('keyboard-and-panel-escape-remain-reachable');
    await page.keyboard.press('c'); assert(await page.locator('#reader-catalog').isVisible(), 'catalog shortcut failed');
    await page.keyboard.press('Escape'); assert(await hidden(), 'closing a panel changed chrome state');
    assert(await page.evaluate(() => document.activeElement.id === 'reader-scroll'), 'panel did not restore reading focus');
    await page.keyboard.press('Shift+Tab');
    assert(await page.evaluate(() => document.activeElement.id === 'exit-focus'), 'keyboard escape button is unreachable');
    assert((await page.locator('#exit-focus').boundingBox()).width > 40, 'keyboard escape button stays clipped on focus');
    await page.keyboard.press('Enter'); assert(!await hidden(), 'keyboard button failed to show controls');
    await page.locator('#reader-scroll').focus(); await page.keyboard.press('Enter'); assert(await hidden(), 'Enter did not hide chrome');
    await page.keyboard.press('Escape'); assert(!await hidden(), 'Escape did not reveal controls');

    step('panel-opening-cancels-a-pending-tap');
    await tap(); await page.locator('#reader-settings-open').click(); await settle();
    assert(!await hidden() && await page.locator('#reader-settings').isVisible(), 'pending canvas tap affected the settings panel');
    await page.locator('#reader-mode-paged').check(); await page.locator('#reader-fit-page').check();
    await page.locator('#reader-settings-close').click();
    const paged = await geometry();
    await tap(); await settle(); assert(await hidden(), 'paged tap did not hide chrome');
    assert(!await page.locator('.ry-reader-toolbar').isVisible(), 'paged mode left its bottom toolbar visible'); samePosition(paged, await geometry());
    await tap(); await settle(); assert(!await hidden(), 'paged controls could not be restored'); samePosition(paged, await geometry());
    const previousPage = Number(await page.locator('#reader-page-input').inputValue());
    await page.locator('#reader-next-page').click();
    assert(Number(await page.locator('#reader-page-input').inputValue()) === previousPage + 1 && !await hidden(), 'page button was intercepted');

    step('small-screen-and-landscape-controls');
    const sizes = [];
    for (const [width, height] of [[320, 852], [852, 393], [393, 852]]) {
      await viewport(width, height);
      await page.waitForFunction(() => document.querySelector('#reader-scroll').clientHeight === innerHeight);
      const overflow = await page.locator('#reader-dialog').evaluate(el => el.scrollWidth - el.clientWidth);
      assert(overflow <= 1, 'reader overflows at ' + width);
      assert(await page.locator('#reader-settings-open').isVisible() && await page.locator('#reader-next-page').isVisible(), 'controls unreachable at ' + width);
      sizes.push({width, height, overflow});
    }
    await page.screenshot({path: output + '/reader-touch-controls-393.png'});
    assert(await page.evaluate(() => window.readerPointerTypes.includes('touch')), 'fixture did not exercise native touch pointers');

    step('chapter-change-cancels-pending-tap');
    await tap(); await page.locator('#next-chapter').click(); await page.waitForSelector('.ry-reader-page[data-page="0"][data-state="loaded"]'); await settle();
    assert(!await hidden(), 'previous chapter tap changed the new chapter');
    await tap(); await settle(); assert(await hidden(), 'new chapter tap stopped responding');
    await page.reload(); await page.waitForSelector('.ry-reader-page[data-state="loaded"]');
    assert(await hidden(), 'per-book hidden chrome preference was lost on reopen');
    await tap(); await settle(); assert(!await hidden(), 'reopened reader could not reveal chrome');
    assert(!errors.length, 'browser errors: ' + errors.join('; '));
    return {singleTap: true, stableCanvas: true, swipe: true, multitouch: true, doubleTap: true, keyboardEscape: true, paged: true, retry: true, lifecycle: true, sizes, errors};
  } finally {
    await cdp.send('Emulation.setTouchEmulationEnabled', {enabled: false});
    await cdp.send('Emulation.clearDeviceMetricsOverride'); await cdp.detach();
  }
}
