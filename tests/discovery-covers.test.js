import test from 'node:test';
import assert from 'node:assert/strict';
import {createDiscoveryCoverLoader} from '../web/discovery-covers.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const book = id => ({siteId:'manhuagui', detailUrl:`https://www.manhuagui.com/comic/${id}/`});
const result = id => ({...book(id), coverUrl:`https://cf.mhgui.com/cpic/h/${id}.jpg`});
function harness() {
  const calls=[], loaded=[], errors=[];
  const loader=createDiscoveryCoverLoader({api:(path,body,signal)=>new Promise((resolve,reject)=>calls.push({path,body,signal,resolve,reject}))});
  const request=(id,refresh=false)=>loader.request(book(id),{refresh,onLoad:url=>loaded.push([id,url]),onError:error=>errors.push([id,error.message])});
  return {loader,request,calls,loaded,errors};
}

test('cover requests are opt-in, at most three run, and responses do not change queue identity', async () => {
  const h=harness();
  for(let i=1;i<=8;i++) h.request(i);
  assert.equal(h.calls.length,0); h.loader.resume(); assert.equal(h.calls.length,3);
  h.calls[1].resolve(result(2)); await tick();
  assert.deepEqual(h.loaded,[[2,result(2).coverUrl]]); assert.equal(h.calls.length,4);
  assert.equal(h.calls[3].body.detailUrl,book(4).detailUrl);
  assert.ok(h.calls.every(call=>call.path==='/api/discovery/cover'));
});

test('leaving pauses in-flight and queued work, late responses cannot update hidden cards', async () => {
  const h=harness(); h.loader.resume(); for(let i=1;i<=5;i++) h.request(i);
  h.loader.pause(); assert.ok(h.calls.every(call=>call.signal.aborted));
  h.calls[0].resolve(result(1)); h.calls[1].reject(new Error('late')); await tick();
  assert.equal(h.calls.length,3); assert.deepEqual(h.loaded,[]); assert.deepEqual(h.errors,[]);
  h.loader.resume(); assert.equal(h.calls.length,6);
  const resumed=h.calls[3], id=Number(resumed.body.detailUrl.split('/').at(-2));
  resumed.resolve(result(id)); await tick(); assert.deepEqual(h.loaded,[[id,result(id).coverUrl]]);
});

test('switching rankings discards old queued requests and ignores aborted settlements', async () => {
  const h=harness(); h.loader.resume(); for(let i=1;i<=8;i++) h.request(i);
  h.loader.clear(); h.request(99); h.loader.resume();
  assert.equal(h.calls.length,4); assert.equal(h.calls[3].body.detailUrl,book(99).detailUrl);
  for(const call of h.calls.slice(0,3)) call.resolve(result(1)); await tick();
  assert.deepEqual(h.loaded,[]); assert.equal(h.calls.length,4);
  h.calls[3].resolve(result(99)); await tick(); assert.deepEqual(h.loaded,[[99,result(99).coverUrl]]);
});

test('revisiting shares cached covers while an explicit retry refreshes only that book', async () => {
  const h=harness(); h.loader.resume(); h.request(1); h.request(1);
  assert.equal(h.calls.length,1); h.calls[0].resolve(result(1)); await tick();
  h.loader.clear(); h.loader.resume(); h.request(1);
  assert.equal(h.calls.length,1); assert.equal(h.loaded.length,2);
  h.request(1,true); assert.equal(h.calls[1].body.refresh,true);
  h.calls[1].resolve({...result(1),coverUrl:'https://cf.mhgui.com/cpic/h/1_new.jpg'}); await tick();
  assert.equal(h.loaded.at(-1)[1],'https://cf.mhgui.com/cpic/h/1_new.jpg');
});

test('a failed cover does not block the queue and a retry can recover it', async () => {
  const h=harness(); h.loader.resume(); for(let i=1;i<=4;i++) h.request(i);
  h.calls[0].reject(new Error('封面暂不可用')); await tick();
  assert.equal(h.calls.length,4); assert.deepEqual(h.errors,[[1,'封面暂不可用']]);
  h.request(1,true); h.calls[1].resolve(result(2)); await tick();
  assert.equal(h.calls[4].body.detailUrl,book(1).detailUrl); assert.equal(h.calls[4].body.refresh,true);
  h.calls[4].resolve(result(1)); await tick(); assert.equal(h.loaded.at(-1)[0],1);
});

test('wrong book identities or unsafe covers are never displayed', async () => {
  for(const payload of [result(2),{...result(1),siteId:'manben'},{...result(1),coverUrl:'javascript:alert(1)'},null]) {
    const h=harness(); h.loader.resume(); h.request(1); h.calls[0].resolve(payload); await tick();
    assert.deepEqual(h.loaded,[]); assert.match(h.errors[0][1],/不匹配/);
  }
});

test('cover cache is bounded rather than retaining every visited book', async () => {
  const h=harness(); h.loader.resume();
  for(let i=1;i<=129;i++) {h.request(i); h.calls.at(-1).resolve(result(i)); await tick();}
  h.request(1); assert.equal(h.calls.length,130);
  h.request(129); assert.equal(h.calls.length,130);
});
