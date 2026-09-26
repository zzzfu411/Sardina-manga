async (page, step = () => {}) => {
  step('load-isolated-page');
  await page.route('**/api/image?**', route => route.abort('failed'));
  await page.goto('__BASE_URL__/');
  step('injected-image-failure');
  await page.evaluate(() => new Promise(resolve => {
    const img = new Image(); img.alt = 'Injected diagnostic failure';
    img.onload = img.onerror = resolve;
    img.src = '/api/image?url=https%3A%2F%2Fimage.mangabz.com%2Fdiagnostic.png&siteId=mangabz&purpose=reader';
    document.body.append(img);
  }));
  throw new Error('Intentional image failure: diagnostic evidence self-check');
}
