async page => {
  const base = '__BASE_URL__';
  await page.route('**/__regression-harness', route => route.fulfill({contentType:'text/html',body:'<!doctype html><title>Sardina isolated storage regression</title>'}));
  await page.goto(base + '/__regression-harness');
  const storage = await page.evaluate(async () => {
    const assert = (condition, message) => {if (!condition) throw new Error(message);};
    const {createDownloadStore, downloadKey} = await import('/download-store.js');
    const request = value => new Promise((resolve, reject) => {value.onsuccess=()=>resolve(value.result);value.onerror=()=>reject(value.error);});
    const name = 'sardina.regression.' + crypto.randomUUID();
    const book = {siteId:'mangabz',title:'隔离测试',detailUrl:'https://www.mangabz.com/999990bz/'};
    const chapters = Array.from({length:1000}, (_,i) => ({name:'第'+(i+1)+'话',url:'https://www.mangabz.com/m'+(999000+i)+'/'}));
    const chapter = chapters[0], id = downloadKey(book,chapter), urls = [0,1].map(i=>'https://image.mangabz.com/test/'+i+'.png?cid=1&key=old');
    const call=indexedDB.open(name,1);
    call.onupgradeneeded=()=>{const db=call.result;db.createObjectStore('chapters',{keyPath:'id'});db.createObjectStore('pages',{keyPath:['chapterId','index']});db.createObjectStore('meta');};
    const legacy=await request(call), tx=legacy.transaction(['chapters','pages','meta'],'readwrite');
    for (const [i,ch] of chapters.slice(0,2).entries()) tx.objectStore('chapters').put({id:downloadKey(book,ch),book,chapters,chapter:ch,urls,generation:'legacy'+i,count:i?0:1,bytes:i?0:3,complete:false,updatedAt:100+i});
    tx.objectStore('pages').put({chapterId:id,index:0,blob:new Blob(['old'])});tx.objectStore('meta').put(3,'bytes');
    await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});legacy.close();
    const store=createDownloadStore({name,limit:20});
    let record=await store.getChapter(book,chapter);
    assert(record.chapters.length===1000 && record.count===1,'v1 chapter metadata lost');
    assert(await (await store.getPage(record,0)).text()==='old','v1 Blob lost');
    const list=await store.list();
    assert(list.length===2 && !('chapters' in list[0]) && !('urls' in list[0]),'list clones complete catalogs/manifests');
    const db=await request(indexedDB.open(name));
    const catalogs=await request(db.transaction('catalogs').objectStore('catalogs').getAll());
    assert(catalogs.length===1 && catalogs[0].chapters.length===1000,'catalogs not shared'); db.close();
    const renewed=await store.prepare({book,chapter,chapters,expectedGeneration:record.generation,urls:urls.map(u=>u.replace('key=old','key=new'))});
    assert(renewed.generation===record.generation && renewed.count===1 && await store.getPage(renewed,0),'ticket renewal redownloads saved pages');
    const changed=await store.prepare({book,chapter,chapters,expectedGeneration:renewed.generation,urls:['https://image.mangabz.com/different-0.png','https://image.mangabz.com/different-1.png']});
    assert(changed.generation!==renewed.generation && changed.backups.length===1 && await store.getPage(renewed,0),'changed content erased old cache');
    let limited=false;
    try {await store.putPage(changed,0,new Blob(['a'.repeat(18)]));} catch {limited=true;}
    assert(limited && (await store.getChapter(book,chapter)).count===0 && await store.getPage(renewed,0),'quota failure corrupts transaction');
    await store.putPage(changed,0,new Blob(['new']));
    const completed=await store.putPage(changed,1,new Blob(['new']));
    assert(completed.complete && !completed.backups.length && !await store.getPage(renewed,0),'old version retained after completed replacement');
    await store.remove(id);
    let fenced=false;try {await store.putPage(changed,0,new Blob(['late']));} catch {fenced=true;}
    let prepareFenced=false;try {await store.prepare({book,chapter,chapters,expectedGeneration:changed.generation,urls});} catch {prepareFenced=true;}
    assert(fenced && prepareFenced && !await store.getChapter(book,chapter),'deleted download resurrected');
    await store.close(); await request(indexedDB.deleteDatabase(name));
    return {v1Migration:true,sharedCatalogs:catalogs.length,summaryBytes:new Blob([JSON.stringify(list)]).size,ticketReuse:true,contentGenerations:true,quotaAtomicity:true,deletionFenced:true};
  });
  const second=await page.context().newPage();
  await second.route('**/__regression-harness', route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Second download tab</title>'}));
  await second.goto(base+'/__regression-harness');
  const sharedName='sardina.regression.tabs.'+Date.now();
  const setup=async (target) => target.evaluate(async name=>{
    const {createDownloadStore}=await import('/download-store.js'), {createDownloadManager}=await import('/download-model.js');
    const book={siteId:'mangabz',title:'多标签测试',detailUrl:'https://www.mangabz.com/999991bz/'}, chapter={name:'第一章',url:'https://www.mangabz.com/m999991/'};
    window.testStore=createDownloadStore({name});window.calls=0;
    window.manager=createDownloadManager({store:testStore,api:async()=>({images:['https://image.mangabz.com/a.png','https://image.mangabz.com/b.png']}),imageUrl:u=>u,validateImage:async()=>{},loadImage:async()=>{window.calls++;await new Promise(r=>setTimeout(r,150));return new Blob(['ok']);}});
    manager.add({book,chapter,chapters:[chapter]});
  },sharedName);
  await Promise.all([setup(page),setup(second)]);
  for(const target of [page,second]) await target.waitForFunction(()=>window.manager.jobs()[0]?.status==='complete');
  const calls=await Promise.all([page.evaluate(()=>window.calls),second.evaluate(()=>window.calls)]);
  if(calls.reduce((a,b)=>a+b,0)!==2)throw new Error('cross-tab duplicate downloads: '+calls);
  for(const target of [page,second])await target.evaluate(()=>testStore.close());
  await second.close();
  await page.evaluate(name=>new Promise((resolve,reject)=>{const call=indexedDB.deleteDatabase(name);call.onsuccess=resolve;call.onerror=()=>reject(call.error);}),sharedName);
  return {storage,crossTab:{calls,total:calls.reduce((a,b)=>a+b,0)}};
}
