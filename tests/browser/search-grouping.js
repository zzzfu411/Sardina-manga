async (page, step = () => {}) => {
  const base='__BASE_URL__', screenshotDir='__OUTPUT_DIR__';
  const assert=(ok,message)=>{if(!ok)throw new Error(message);};
  const errors=[],reads=[];page.on('pageerror',error=>errors.push(error.message));
  const books=[
    {siteId:'hipmh',siteName:'嬉皮漫画',author:'Monohumbug(Redice Studio) / REDICE STUDIO / Saenal / Team Argo'},
    {siteId:'manhuazhijia',siteName:'漫画之家',author:'Team Argo'},
    {siteId:'mangacopy',siteName:'拷贝漫画',title:'裝備我最強',author:'Saenal / Team Argo'},
    {siteId:'guazimanhua',siteName:'瓜子漫画',author:'',detailAuthor:'Team Argo,Monohumbug&#40Redice Studio&#41,Saenal,Redice Studio,极'},
    {siteId:'dumanwu',siteName:'读漫屋',author:'',detailAuthor:'Team Argo'},
    {siteId:'dumanwu',siteName:'读漫屋',author:'',detailAuthor:'Saenal'},
    {siteId:'cocoecar',siteName:'可可漫画',title:'装备我最强(原版自译)',author:'Team Argo'},
  ].map((book,index)=>({title:'装备我最强',...book,detailUrl:`https://fixture.example/book/${index}`,
    coverUrl:`https://fixture.example/cover/${index}.png`,chapters:[1,2].map(n=>({name:`来源${index} 第${n}话`,url:`https://fixture.example/book/${index}/${n}`}))}));
  const sites=[...new Map(books.map(book=>[book.siteId,{siteId:book.siteId,siteName:book.siteName}])).values()];
  const json=(route,data)=>route.fulfill({contentType:'application/json',body:JSON.stringify({data})});
  await page.route('**/api/sites',route=>json(route,sites));
  await page.route('**/api/search',route=>{
    const {siteId}=route.request().postDataJSON();
    return json(route,[{...sites.find(site=>site.siteId===siteId),results:books.filter(book=>book.siteId===siteId)}]);
  });
  await page.route('**/api/details',route=>{
    const {siteId,detailUrl}=route.request().postDataJSON(),book=books.find(book=>book.siteId===siteId&&book.detailUrl===detailUrl);
    assert(book,'unexpected detail request');
    return json(route,{...book,author:book.detailAuthor??book.author});
  });
  await page.route('**/api/chapter-images',route=>{
    reads.push(route.request().postDataJSON());return json(route,{images:['https://fixture.example/page.png']});
  });
  await page.route('**/api/image?**',route=>route.fulfill({contentType:'image/png',path:'__ROOT__/tests/fixtures/reader-page.png'}));
  await page.addInitScript(()=>{localStorage.clear();sessionStorage.clear();});
  step('merge-partial-credits-and-traditional-title');
  await page.setViewportSize({width:1280,height:900});
  await page.goto(base+'/s/'+encodeURIComponent('装备'));
  await page.waitForFunction(count=>document.querySelector('#search-status')?.textContent.includes(count+' / '+count),sites.length);
  const works=page.locator('.search-primary .search-work');
  const work=works.filter({has:page.getByRole('button',{name:'装备我最强',exact:true})});
  assert(await works.count()===2,'same-title sources became duplicate work cards');
  assert(await work.count()===1,'equipment work is not unique');
  assert(await work.locator('.search-source').count()===6,'merging dropped source entries');
  assert(await work.locator('.search-source-fold').evaluate(node=>!node.open),'sources are not folded initially');
  assert((await work.locator('.search-source-fold summary').innerText()).includes('5 个源'),'source count includes duplicate entries');
  await work.locator('.search-chapter').first().waitFor();
  await page.screenshot({path:screenshotDir+'/equipment-merged-desktop.png'});
  step('refine-detail-credits-without-losing-source-selection');
  await work.locator('.search-source-fold summary').click();
  const guazi=work.locator('.search-source[data-site="guazimanhua"]');
  await guazi.click();
  await page.waitForFunction(()=>document.querySelector('.search-primary .search-work .search-author')?.textContent.includes('极'));
  assert(await works.count()===2,'detail credits split the merged card');
  assert(await guazi.getAttribute('aria-pressed')==='true','detail regrouping lost the chosen source');
  assert(!(await work.locator('.search-author').innerText()).includes('&#'),'escaped author text leaked to the UI');
  assert(await work.locator('.search-source-fold').evaluate(node=>node.open),'regrouping collapsed the source picker');
  await work.locator('.search-order').click();
  assert((await work.locator('.search-chapter').first().innerText()).includes('第2话'),'chapter order stopped working');
  step('keep-source-choices-accessible-on-mobile');
  const sizes=[];
  for(const width of [393,320]) {
    await page.setViewportSize({width,height:852});
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth);
    assert(overflow<=1,'source choices overflow the mobile viewport');sizes.push({width,overflow});
  }
  await page.screenshot({path:screenshotDir+'/equipment-merged-mobile.png'});
  step('open-chapter-from-a-preserved-alternative-entry');
  const alternative=work.locator('.search-source[data-site="dumanwu"]').last();
  await alternative.click();
  await page.waitForFunction(()=>document.querySelector('.search-primary .search-work .search-chapter')?.textContent.includes('来源5'));
  assert(await alternative.getAttribute('aria-pressed')==='true','alternative source entry was not selected');
  await work.locator('.search-chapter').first().click();
  await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='loaded');
  assert(reads[0]?.siteId==='dumanwu'&&reads[0]?.chapterUrl===books[5].chapters[1].url,'reader opened a different source or chapter');
  assert(!errors.length,'browser errors: '+errors.join('; '));
  return {works:2,mergedCandidates:6,mergedSources:5,sourceSelectionPreserved:true,editionSeparate:true,sizes,reading:true,errors};
}
