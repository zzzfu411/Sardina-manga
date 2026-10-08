async (page, step = () => {}) => {
  const base = '__BASE_URL__', screenshots = '__OUTPUT_DIR__';
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  const errors = [], reads = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  const sites = [{siteId: 'hipmh', siteName: '嬉皮漫画'}, {siteId: 'manhuazhijia', siteName: '漫画之家'},
    {siteId: 'mangacopy', siteName: '拷贝漫画'}, {siteId: 'guazimanhua', siteName: '瓜子漫画'}, {siteId: 'cocoecar', siteName: '可可漫画'}];
  const cases = [
    ['全知读者视角', '全知讀者視角', ['singNsong / Sleepy-C / UMI', '싱숑(SingNSong)', 'Sleep-C / SingSyong']],
    ['妖神记', '妖神記', ['发飙的蜗牛', '踏雪动漫', '']],
    ['装备妖精的日常', '裝備妖精的日常', ['孝至', 'こうじ', '']],
    ['我的装备天下第一', '我的裝備天下第一', ['林七年', '星际互娱', '']],
  ];
  const books = cases.flatMap(([title, traditional, authors], caseIndex) => [
    ...sites.slice(0, 4).map((site, index) => ({...site, title: index === 2 ? traditional : title, author: authors[index] || ''})),
    {...sites[3], title: title + '（简体）', author: ''},
    {...sites[4], title: title + '外传', author: authors[0]},
  ].map((book, index) => ({...book, query: title, detailUrl: `https://fixture.example/${caseIndex}/${index}`,
    coverUrl: `https://fixture.example/cover/${caseIndex}-${index}.png`,
    chapters: [1, 2].map(n => ({name: `${title} 来源${index} 第${n}话`, url: `https://fixture.example/${caseIndex}/${index}/chapter/${n}`}))})));
  const json = (route, data) => route.fulfill({contentType: 'application/json', body: JSON.stringify({data})});
  let releaseDetail;
  await page.route('**/api/sites', route => json(route, sites));
  await page.route('**/api/search', route => {
    const {siteId, keyword} = route.request().postDataJSON();
    return json(route, [{...sites.find(site => site.siteId === siteId), results: books.filter(book => book.siteId === siteId && book.query === keyword)}]);
  });
  await page.route('**/api/details', async route => {
    const {siteId, detailUrl} = route.request().postDataJSON();
    const book = books.find(book => book.siteId === siteId && book.detailUrl === detailUrl);
    assert(book, 'unexpected detail request');
    if (siteId === 'guazimanhua') await new Promise(resolve => { releaseDetail = resolve; });
    return json(route, {...book, author: siteId === 'guazimanhua' ? '源站补充的另一署名' : book.author});
  });
  await page.route('**/api/chapter-images', route => {
    reads.push(route.request().postDataJSON()); return json(route, {images: ['https://fixture.example/page.png']});
  });
  await page.route('**/api/image?**', route => route.fulfill({contentType: 'image/png', path: '__ROOT__/tests/fixtures/reader-page.png'}));
  await page.addInitScript(() => { localStorage.clear(); sessionStorage.clear(); });
  await page.setViewportSize({width: 1280, height: 900});
  for (const [title] of cases) {
    step('single-card-' + title);
    await page.goto(base + '/s/' + encodeURIComponent(title));
    await page.waitForFunction(count => document.querySelector('#search-status')?.textContent.includes(count + ' / ' + count), sites.length);
    const work = page.locator('.search-primary .search-work');
    assert(await work.count() === 1, title + ' has duplicate cards');
    assert(await work.locator('.search-source').count() === 5, 'lost an alternative source entry');
    assert((await work.locator('.search-source-fold summary').innerText()).includes('4 个源'), 'wrong unique-source count');
    assert(!await work.locator('.search-source-fold').evaluate(node => node.open), 'source list should start folded');
    await work.locator('.search-chapter').first().waitFor();
    await work.locator('.search-source-fold summary').click();
    assert(await work.locator('.search-source-credit').count() === 5, 'different credits are not visible in the source picker');
    await work.locator('.search-order').click();
    const alternative = work.locator('.search-source[data-site="guazimanhua"]').last();
    await alternative.click();
    await page.waitForFunction(() => document.querySelector('.search-primary .search-chapter-area')?.getAttribute('aria-busy') === 'true');
    // The mock resolves only after the reader has picked a different entry and
    // set chapter order, reproducing the late-author split in production.
    assert(typeof releaseDetail === 'function', 'detail request did not start');
    releaseDetail(); releaseDetail = null;
    await page.waitForFunction(() => document.querySelector('.search-primary .search-author')?.textContent.includes('源站补充'));
    assert(await work.count() === 1, 'late detail author split the card');
    assert(await alternative.getAttribute('aria-pressed') === 'true', 'late details lost the selected source');
    assert(await work.locator('.search-source-fold').evaluate(node => node.open), 'late details closed the source picker');
    assert((await work.locator('.search-chapter').first().innerText()).includes('来源4 第2话'), 'wrong source or chapter order');
    results.push({title, cards: 1, candidates: 5, sources: 4});
  }
  step('source-filter-and-return');
  const work = page.locator('.search-primary .search-work');
  await page.locator('#source-filter-label').click();
  await page.locator('#source-tabs button').filter({hasText: '漫画之家'}).click();
  assert(await work.count() === 1 && await work.locator('.search-source').count() === 1, 'source filter leaked grouped candidates');
  await page.locator('#source-tabs button').first().click();
  assert(await work.locator('.search-source').count() === 5, 'returning to all sources lost alternatives');
  // Re-select the cached alternative; opening the source picker also verifies
  // that the filter round trip leaves the controls usable.
  if (!await work.locator('.search-source-fold').evaluate(node => node.open)) await work.locator('.search-source-fold summary').click();
  await work.locator('.search-source[data-site="guazimanhua"]').last().click();
  await page.waitForFunction(() => document.querySelector('.search-primary .search-chapter')?.textContent.includes('来源4'));
  step('mobile-source-labels');
  const sizes = [];
  for (const width of [393, 320]) {
    await page.setViewportSize({width, height: 852});
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    assert(overflow <= 1, 'credit labels overflow the mobile viewport'); sizes.push({width, overflow});
  }
  await page.screenshot({path: screenshots + '/multi-title-sources-mobile.png'});
  step('selected-entry-reaches-reader');
  const expected = books.find(book => book.query === '我的装备天下第一' && book.detailUrl.endsWith('/4'));
  const chapterText = await work.locator('.search-chapter').first().innerText();
  const chapter = chapterText.includes('第2话') ? expected.chapters[1] : expected.chapters[0];
  await work.locator('.search-chapter').first().click();
  await page.waitForFunction(() => document.querySelector('.ry-reader-page')?.dataset.state === 'loaded');
  assert(reads[0]?.siteId === expected.siteId && reads[0]?.chapterUrl === chapter.url, 'opened the wrong source chapter');
  assert(!errors.length, 'browser errors: ' + errors.join('; '));
  return {titles: results, lateMetadataStable: true, filter: true, reading: true, sizes, errors};
}
