async page => {
  await page.route('**/__performance',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Isolated performance regression</title>'}));
  await page.goto('__BASE_URL__/__performance');
  return page.evaluate(async()=>{
    const {createLibraryStore}=await import('/library-store.js'),{createDownloadStore}=await import('/download-store.js');
    const assert=(ok,message)=>{if(!ok)throw new Error(message);};
    const data=new Map();let scans=0,writes=0;
    const storage={get length(){return data.size;},key(i){scans++;return [...data.keys()][i];},getItem:k=>data.get(k)??null,setItem(k,v){writes++;data.set(k,String(v));}};
    const books=Array.from({length:1000},(_,i)=>({siteId:'mangabz',title:'样本'+i,detailUrl:'https://www.mangabz.com/'+(100000+i)+'bz/',favorite:true,openedAt:1000,readAt:1000,chapterUrl:'https://www.mangabz.com/m'+(100000+i)+'/',chapterStates:Object.fromEntries(Array.from({length:20},(_,j)=>['https://www.mangabz.com/m'+(100000+i)+'/?part='+j,{read:true,updatedAt:1000}]))}));
    const library=createLibraryStore({storage,locks:null});await library.save(books);scans=0;writes=0;
    const durations=[],synchronous=[];
    for(let i=0;i<7;i++){const start=performance.now(),pending=library.updateBook({...books[0],page:i+1,readAt:2000+i});synchronous.push(performance.now()-start);await pending;durations.push(performance.now()-start);}
    assert(scans===0&&writes===7,'progress updates scanned/wrote unrelated records');
    const median=xs=>Math.round(xs.sort((a,b)=>a-b)[Math.floor(xs.length/2)]*100)/100;
    const prefix='sardina.performance.'+crypto.randomUUID()+'.',savedKeys=new Set();
    let nativeScans=0,nativeWrites=0,realStorage;
    const nativeStorage={get length(){return localStorage.length;},key(i){nativeScans++;const key=localStorage.key(i);return key?.startsWith(prefix)?key.slice(prefix.length):null;},getItem:key=>localStorage.getItem(prefix+key),setItem(key,value){nativeWrites++;localStorage.setItem(prefix+key,value);savedKeys.add(prefix+key);}};
    try {
      const nativeLibrary=createLibraryStore({storage:nativeStorage});await nativeLibrary.save(books);
      nativeScans=0;nativeWrites=0;
      const durations=[],synchronous=[],longTasks=[];
      const observer=PerformanceObserver.supportedEntryTypes.includes('longtask')?new PerformanceObserver(list=>longTasks.push(...list.getEntries().map(row=>row.duration))):null;
      observer?.observe({type:'longtask'});
      for(let i=0;i<7;i++){
        await new Promise(requestAnimationFrame);
        const start=performance.now(),pending=nativeLibrary.updateBook({...books[0],page:i+1,readAt:3000+i});synchronous.push(performance.now()-start);await pending;durations.push(performance.now()-start);
      }
      await new Promise(resolve=>setTimeout(resolve,0));observer?.disconnect();
      assert(nativeScans===0&&nativeWrites===7,'real storage progress touches unrelated records');
      const saved=JSON.parse(localStorage.getItem(prefix+'revyunman.library.v2.book.'+encodeURIComponent('mangabz::'+books[0].detailUrl)));
      assert(saved.book.page===7,'real localStorage progress was not committed');
      realStorage={books:1000,chapterStatesPerBook:20,storage:'real localStorage and Web Locks in isolated Chromium profile',scans:nativeScans,writes:nativeWrites,medianSynchronousMs:median(synchronous),medianSaveMs:median(durations),longTasks:observer?longTasks.length:null,maxLongTaskMs:observer?Math.round(Math.max(0,...longTasks)*100)/100:null};
    } finally {for(const key of savedKeys)localStorage.removeItem(key);}
    const book={siteId:'mangabz',title:'共享目录',detailUrl:'https://www.mangabz.com/990090bz/'},chapters=Array.from({length:1000},(_,i)=>({name:'第'+(i+1)+'话',url:'https://www.mangabz.com/m'+(400000+i)+'/'}));
    const name='sardina.regression.performance.'+crypto.randomUUID(),store=createDownloadStore({name});
    for(const chapter of chapters.slice(0,100))await store.enqueue({book,chapter,chapters});
    const start=performance.now(),rows=await store.list(),listMs=performance.now()-start;
    assert(rows.length===100&&rows.every(row=>!row.chapters&&!row.urls),'summary contains full catalogs');
    const req=value=>new Promise((resolve,reject)=>{value.onsuccess=()=>resolve(value.result);value.onerror=()=>reject(value.error);});
    const db=await req(indexedDB.open(name)),catalogs=await req(db.transaction('catalogs').objectStore('catalogs').getAll());db.close();
    assert(catalogs.length===1&&catalogs[0].chapters.length===1000,'duplicated catalog entries');
    const result={library:{books:1000,chapterStatesPerBook:20,storage:'in-memory Map; actual browser JS; excludes localStorage I/O',scans,writes,medianSynchronousMs:median(synchronous),medianSaveMs:median(durations)},realStorage,downloads:{tasks:rows.length,storedCatalogEntries:catalogs[0].chapters.length,summaryBytes:new Blob([JSON.stringify(rows)]).size,listMs:Math.round(listMs*100)/100}};
    for(const row of rows)await store.remove(row.id);
    const empty=await req(indexedDB.open(name));assert(await req(empty.transaction('catalogs').objectStore('catalogs').count())===0,'orphaned shared catalogs after deletion');empty.close();
    await store.close();await req(indexedDB.deleteDatabase(name));return result;
  });
}
