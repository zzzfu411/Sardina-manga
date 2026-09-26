async (page, step = () => {}) => {
  const base='__BASE_URL__', root='__ROOT__', output='__OUTPUT_DIR__';
  const assert=(value,message)=>{if(!value)throw new Error(message);};
  const book={siteId:'mangabz',siteName:'漫画巴士',title:'复审合成漫画',author:'合成作者',detailUrl:'https://www.mangabz.com/910026bz/',coverUrl:'https://image.mangabz.com/audit-cover.png'};
  const a={name:'第1话',url:'https://www.mangabz.com/m910026/'},b={name:'第2话',url:'https://www.mangabz.com/m910027/'};
  const imageUrl='https://image.mangabz.com/audit-page.png', results={},errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  async function setup(options={}) {
    await page.unrouteAll({behavior:'ignoreErrors'});
    await page.route('**/__reaudit',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Isolated audit setup</title>'}));
    await page.goto(base+'/__reaudit');
    await page.evaluate(async()=>{
      localStorage.clear();sessionStorage.clear();
      await new Promise((resolve,reject)=>{const request=indexedDB.deleteDatabase('sardina.downloads.v1');request.onsuccess=resolve;request.onerror=()=>reject(request.error);request.onblocked=()=>reject(new Error('old download connection remains open'));});
      localStorage.setItem('revyunman.reader.preferences.v2',JSON.stringify({mode:'paged',prefetch:'off'}));
    });
    const counts={images:0,chapters:0,details:0,purposes:[]};
    await page.route('**/api/**',async route=>{
      const raw=route.request().url(),pathname=raw.replace(/^https?:\/\/[^/]+/,'').split('?')[0];
      const send=data=>route.fulfill({contentType:'application/json',body:JSON.stringify({data})});
      if(pathname==='/api/sites')return send([{siteId:book.siteId,siteName:book.siteName}]);
      if(pathname==='/api/home-sections')return send({featured:[]});
      if(pathname==='/api/recommendations')return send({items:[{...book,recommendationKind:'popular'}],origins:[],nextBatch:null});
      if(pathname==='/api/book-metadata')return send({});
      if(pathname==='/api/details'){counts.details++;return send({...book,chapters:options.catalog||[a,b],catalogCompleteness:'complete'});}
      if(pathname==='/api/chapter-images'){counts.chapters++;return send({images:[imageUrl]});}
      if(pathname==='/api/image'){
        if(decodeURIComponent((raw.match(/[?&]url=([^&]+)/)||[])[1]||'')===imageUrl){
          counts.images++;counts.purposes.push((raw.match(/[?&]purpose=([^&]+)/)||[])[1]||'');
          if(options.corrupt&&counts.images===1)return route.fulfill({contentType:'image/png',body:'not-an-image'});
        }
        return route.fulfill({contentType:'image/png',path:root+'/tests/fixtures/reader-page.png'});
      }
      return send([]);
    });
    await page.setViewportSize({width:393,height:852});
    return counts;
  }
  const pathFor=()=>page.evaluate(({book,a})=>{
    const encode=value=>btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value)))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
    return '/read/'+encode([book.siteId,book.detailUrl,{title:book.title}])+'/'+encode(a.url);
  },{book,a});
  async function seedBook(extra={}) {
    await page.evaluate(async({book,a,extra})=>{const {createLibraryStore}=await import('/library-store.js');await createLibraryStore({storage:localStorage}).save([{...book,chapterUrl:a.url,chapterName:a.name,page:0,favorite:false,readAt:Date.now(),openedAt:Date.now(),...extra}]);},{book,a,extra});
  }

  step('bad-image-one-retry-and-quality-events');
  let counts=await setup({corrupt:true});
  await page.goto(base+'/discover');await page.locator('.recommendation-title').first().click();
  await page.getByRole('button',{name:'开始阅读',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='error');
  await page.locator('.ry-page-retry').click();
  await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='loaded');
  await page.waitForFunction(()=>{const metrics=JSON.parse(localStorage.getItem('revyunman.recommendations.v1'))?.metrics;return metrics?.openRecoveries===1&&metrics.readingStarts===1;});
  const metrics=await page.evaluate(()=>JSON.parse(localStorage.getItem('revyunman.recommendations.v1')).metrics);
  assert(counts.images===2,'manual retry did not fetch exactly one new image');
  assert(metrics.openFailures===1&&metrics.openRecoveries===1&&metrics.readingStarts===1,'reader outcome metrics lost failure, recovery or reading start');
  assert(counts.purposes.every(value=>value==='reader'),'current page lost foreground purpose');
  results.retry={requests:counts.images,metrics,purpose:counts.purposes};

  step('removed-online-chapter-still-reads-downloaded-blob');
  counts=await setup({catalog:[b]});
  const path=await pathFor();
  await page.evaluate(async({book,a,b,imageUrl})=>{
    const {createDownloadStore}=await import('/download-store.js');const store=createDownloadStore();
    const canvas=document.createElement('canvas');canvas.width=30;canvas.height=50;const blob=await new Promise(resolve=>canvas.toBlob(resolve));
    const record=await store.prepare({book,chapter:a,chapters:[a,b],urls:[imageUrl]});await store.putPage(record,0,blob);await store.putCatalog(book,[b]);await store.close();
  },{book,a,b,imageUrl});
  await page.goto(base+path);await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='loaded');
  assert(counts.images===0&&counts.chapters===0,'saved chapter fetched remote images');
  assert(await page.locator('#next-chapter').isDisabled(),'orphan local chapter guessed a new sequence');
  await page.locator('#reader-back').click();await page.waitForFunction(()=>document.querySelector('#detail-dialog').open);
  await page.getByRole('button',{name:'关闭漫画详情',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#detail-dialog').open);
  await page.locator('.shelf-open').click();await page.locator('#downloads-open').click();
  await page.locator('.download-row').getByRole('button',{name:'阅读',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='loaded');
  results.localChapter={deepLink:true,downloadManager:true,remoteImages:counts.images,manifests:counts.chapters};

  step('cross-tab-delete-survives-exit-and-explicit-reopen');
  await setup();await page.goto(base+await pathFor());
  await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='loaded');
  await page.waitForFunction(()=>document.querySelector('#shelf-count').textContent==='1');
  const other=await page.context().newPage();
  await other.route('**/__reaudit',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Other audit tab</title>'}));await other.goto(base+'/__reaudit');
  await other.evaluate(async()=>{const {createLibraryStore}=await import('/library-store.js');await createLibraryStore({storage:localStorage}).save([]);});
  await page.waitForFunction(()=>document.querySelector('#shelf-count').textContent==='0');
  await page.locator('#reader-back').click();await page.waitForFunction(()=>document.querySelector('#detail-dialog').open);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#detail-content')?.innerText.includes('章节目录'));
  assert(await page.locator('#shelf-count').innerText()==='0','reader exit resurrected deleted book');
  await page.getByRole('button',{name:'开始阅读',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#shelf-count').textContent==='1');
  results.deletion={exitKeptDeleted:true,explicitNewRead:true};await other.close();

  step('cross-tab-recommendation-preferences-and-clear');
  await setup();
  const feedbackTab=await page.context().newPage();await feedbackTab.route('**/__reaudit',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Other recommendation tab</title>'}));await feedbackTab.goto(base+'/__reaudit');
  for(const tab of [page,feedbackTab])await tab.evaluate(async()=>{const {createRecommendationFeedback}=await import('/recommendations-feedback.js');window.auditFeedback=createRecommendationFeedback({storage:localStorage});});
  await page.evaluate(async book=>{auditFeedback.dismiss(book);auditFeedback.setPersonalization(false);await auditFeedback.settled();},book);
  await feedbackTab.evaluate(async book=>{auditFeedback.markExposed({...book,title:'另一部漫画',detailUrl:'https://www.mangabz.com/910028bz/'});await auditFeedback.settled();},book);
  const feedback=await page.evaluate(()=>{auditFeedback.sync();return auditFeedback.snapshot();});
  assert(feedback.dismissed.length===1&&feedback.personalization===false,'other tab exposure overwrote explicit preference');
  await feedbackTab.evaluate(async()=>{auditFeedback.clear();await auditFeedback.settled();});
  await page.waitForFunction(()=>auditFeedback.snapshot().dismissed.length===0);
  assert(await page.evaluate(()=>auditFeedback.snapshot().personalization===false),'clear changed explicit preference');
  results.feedback={twoRealTabs:true,dismissalPreserved:true,preferencePreserved:true,clearSynced:true};await feedbackTab.close();

  step('empty-filter-favorite-toggle-and-removal-undo');
  await setup();await seedBook();await page.goto(base+'/');await page.locator('.shelf-open').click();
  await page.locator('#shelf-only-favorites').check();await page.getByRole('button',{name:'查看全部',exact:true}).click();
  assert(!await page.locator('#shelf-only-favorites').isChecked(),'view all left favorites enabled');
  assert(await page.locator('#shelf-grid .book-card').count()===1,'view all did not restore record');
  await page.locator('#shelf-grid .book-title').click();
  // A history card resumes; return to its details to change collection state.
  await page.waitForFunction(()=>document.querySelector('#reader-dialog').open);await page.locator('#reader-back').click();
  await page.getByRole('button',{name:'加入收藏',exact:true}).click();await page.getByRole('button',{name:'取消收藏',exact:true}).click();
  await page.getByRole('button',{name:'关闭漫画详情',exact:true}).click();await page.locator('.shelf-open').click();
  await page.locator('#shelf-grid .remove-book').click();await page.locator('#shelf-undo').getByRole('button',{name:'撤销',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#shelf-count').textContent==='1');
  const saved=await page.evaluate(async()=>{const {createLibraryStore}=await import('/library-store.js');return createLibraryStore({storage:localStorage}).books[0];});
  assert(saved.favorite===false&&saved.chapterUrl===a.url,'unfavorite/undo discarded progress');
  results.shelf={allFiltersCleared:true,unfavoriteKeepsProgress:true,removeUndo:true};
  await setup();await page.goto(base+'/discover');await page.locator('.shelf-open').click();await page.getByRole('button',{name:'去找漫画',exact:true}).click();
  assert(await page.evaluate(()=>location.pathname==='/'&&document.activeElement.id==='home-keyword'),'empty shelf search did not focus visible home search');
  results.shelf.emptySearch=true;

  step('owned-history-close-and-browser-forward');
  await setup();await page.goto(base+'/discover');await page.locator('.recommendation-title').first().click();await page.getByRole('button',{name:'开始阅读',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='loaded');
  const length=await page.evaluate(()=>history.length);
  await page.locator('#reader-back').click();await page.waitForFunction(()=>location.pathname.startsWith('/m/')&&document.querySelector('#detail-dialog').open);
  await page.getByRole('button',{name:'关闭漫画详情',exact:true}).click();await page.waitForFunction(()=>location.pathname==='/discover'&&!document.querySelector('#detail-dialog').open);
  assert(await page.evaluate(()=>history.length)===length,'closing overlays pushed new entries');
  await page.goForward();await page.waitForFunction(()=>document.querySelector('#detail-dialog').open);
  await page.goForward();await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='loaded');
  results.history={closeAddsNoEntries:true,forwardDetails:true,forwardReader:true};

  step('thousand-book-ui-budget-and-whole-library-search');
  await setup();await page.evaluate(async()=>{
    const {createLibraryStore}=await import('/library-store.js');
    const books=Array.from({length:1000},(_,i)=>({siteId:'mangabz',title:'样本'+i,detailUrl:'https://www.mangabz.com/'+(100000+i)+'bz/',favorite:true,openedAt:1000,readAt:1000,chapterUrl:'https://www.mangabz.com/m'+(100000+i)+'/',chapterStates:Object.fromEntries(Array.from({length:20},(_,j)=>['https://www.mangabz.com/m'+(100000+i)+'/?part='+j,{read:true,updatedAt:1000}]))}));
    await createLibraryStore({storage:localStorage}).save(books);
  });await page.goto(base+'/');await page.waitForFunction(()=>document.querySelector('#shelf-count').textContent==='1000');
  const shelfPerf=await page.evaluate(async()=>{const start=performance.now();document.querySelector('.shelf-open').click();const synchronousMs=performance.now()-start;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {synchronousMs,renderedMs:performance.now()-start,cards:document.querySelectorAll('#shelf-grid .book-card').length,nodes:document.querySelectorAll('#shelf-grid *').length};});
  assert(shelfPerf.cards<=48&&shelfPerf.nodes<1000,'shelf DOM grows with total library');
  await page.locator('#shelf-pagination [data-page-action="next"]').click();
  assert(await page.locator('#shelf-grid .book-card').count()<=48,'next page accumulated previous cards');
  await page.locator('#shelf-query').fill('样本999');assert(await page.locator('#shelf-grid .book-title').innerText()==='样本999','search excludes unrendered books');
  results.largeShelf=shelfPerf;await page.screenshot({path:output+'/shelf-393.png'});

  step('shelf-scale-and-throttled-cpu');
  const cdp=await page.context().newCDPSession(page);results.shelfScale=[];
  try {
    for(const [size,cpuRate] of [[100,1],[1000,1],[3000,1],[1000,4]]){
      await setup();await page.evaluate(async size=>{
        const {createLibraryStore}=await import('/library-store.js');
        await createLibraryStore({storage:localStorage}).save(Array.from({length:size},(_,i)=>({siteId:'mangabz',title:'规模样本'+i,detailUrl:'https://www.mangabz.com/'+(200000+i)+'bz/',favorite:true,openedAt:1000})));
      },size);await page.goto(base+'/');await page.waitForFunction(size=>document.querySelector('#shelf-count').textContent===String(size),size);
      await cdp.send('Emulation.setCPUThrottlingRate',{rate:cpuRate});
      const measurement=await page.evaluate(async size=>{
        const longTasks=[],observer=new PerformanceObserver(list=>longTasks.push(...list.getEntries().map(row=>row.duration)));observer.observe({type:'longtask'});
        const opened=performance.now();document.querySelector('.shelf-open').click();const openMs=performance.now()-opened;
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        const cards=document.querySelectorAll('#shelf-grid .book-card').length,nodes=document.querySelectorAll('#shelf-grid *').length;
        const input=document.querySelector('#shelf-query');input.value='规模样本'+(size-1);const filtered=performance.now();input.dispatchEvent(new Event('input',{bubbles:true}));const filterMs=performance.now()-filtered;
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));observer.disconnect();
        return {openMs,filterMs,cards,nodes,found:document.querySelector('#shelf-grid .book-title')?.textContent,longTasks:longTasks.length,maxLongTaskMs:Math.max(0,...longTasks)};
      },size);
      assert(measurement.cards<=48&&measurement.nodes<1000,'larger shelf exceeded its render budget');
      assert(measurement.found==='规模样本'+(size-1),'scale search omitted unrendered records');
      results.shelfScale.push({books:size,chapterStatesPerBook:0,cpuRate,...measurement});
      await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});
    }
  } finally {await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});await cdp.detach();}

  step('two-thousand-chapters-lazy-catalog-and-keyboard');
  const chapters=Array.from({length:2000},(_,i)=>({name:'第'+(i+1)+'话',url:'https://www.mangabz.com/m'+(910026+i)+'/'}));
  await setup({catalog:chapters});await page.goto(base+'/discover');await page.locator('.recommendation-title').first().click();await page.waitForSelector('.chapter-entry');
  assert(await page.locator('.chapter-entry').count()<=80,'details rendered all chapters');
  await page.locator('.chapter-tools input').fill('第1999话');await page.locator('.chapter-entry > button').first().click();
  await page.waitForFunction(()=>document.querySelector('.ry-reader-page')?.dataset.state==='loaded');
  assert(await page.locator('#reader-chapter-list button').count()===0,'hidden reader catalog rendered all chapters');
  await page.locator('#reader-catalog-open').click();
  assert(await page.locator('#reader-chapter-list button').count()<=80,'reader catalog exceeds window');
  assert(await page.locator('#reader-chapter-list [aria-current="true"]').innerText()==='第1999话','catalog did not locate current chapter');
  await page.locator('#reader-chapter-search').fill('第1话');assert(await page.locator('#reader-chapter-list button').count()===1,'catalog filter did not search full collection');
  await page.locator('#reader-chapter-search').fill('');
  await page.locator('#reader-chapter-list button').first().focus();await page.keyboard.press('End');
  assert(await page.evaluate(()=>document.activeElement.closest('#reader-catalog')!==null),'catalog keyboard focus escaped');
  await page.keyboard.press('Escape');await page.setViewportSize({width:320,height:852});
  assert(await page.locator('#reader-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth+1),'reader overflows mobile viewport');
  results.catalog={chapters:2000,detailsBound:80,hiddenEntries:0,readerBound:80,allChaptersSearch:true,currentChapter:true,keyboardContained:true};
  await page.screenshot({path:output+'/reader-320.png'});

  step('pending-covers-pause-resume-and-genuine-errors');
  await setup();
  const held=[];let coverRequests=0, onCoverRequest;
  const firstCover=new Promise(resolve=>onCoverRequest=resolve);
  await page.route('**/api/image?auditCover=*',async route=>{coverRequests++;const gate=new Promise(resolve=>held.push(resolve));onCoverRequest?.();onCoverRequest=null;await gate;try{await route.fulfill({contentType:'image/png',path:root+'/tests/fixtures/reader-page.png'});}catch{}});
  await page.evaluate(async()=>{
    const {createCoverPause}=await import('/cover-pause.js');window.auditPause=createCoverPause();window.auditCoverErrors=0;
    const img=new Image();img.id='pending-cover';img.onerror=()=>auditCoverErrors++;document.body.append(img);img.src=location.origin+'/api/image?auditCover=1';
  });
  await firstCover;
  await page.waitForFunction(()=>document.querySelector('#pending-cover')?.complete===false);
  await page.evaluate(()=>{auditPause.pause();auditPause.resume();auditPause.pause();});
  assert(await page.locator('#pending-cover').getAttribute('src')===null,'pending cover kept its connection');
  const resumedCover=new Promise(resolve=>onCoverRequest=resolve);
  await page.evaluate(()=>auditPause.resume());await resumedCover;await page.waitForFunction(()=>document.querySelector('#pending-cover').hasAttribute('src'));
  // Route interception may not see the cancelled initial request; releasing
  // every outstanding request is enough to verify restoration and callbacks.
  await page.waitForFunction(()=>document.querySelector('#pending-cover').getAttribute('src')!==null);
  while(held.length)held.shift()();
  await page.waitForFunction(()=>document.querySelector('#pending-cover').naturalWidth>0);
  assert(await page.evaluate(()=>auditCoverErrors===0),'cover cancellation fired normal error handlers');
  await page.route('**/api/image?auditBroken=*',route=>route.fulfill({contentType:'image/png',body:'broken'}));
  await page.evaluate(()=>document.querySelector('#pending-cover').src='/api/image?auditBroken=1');
  await page.waitForFunction(()=>auditCoverErrors===1);
  results.coverPause={restored:true,cancelNotCountedAsFailure:true,genuineFailureDelivered:true,requests:coverRequests};
  assert(!errors.length,'browser errors: '+errors.join('; '));
  return {...results,errors};
}
