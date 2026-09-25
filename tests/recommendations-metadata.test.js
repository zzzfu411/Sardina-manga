import test from 'node:test';
import assert from 'node:assert/strict';
import {createRecommendationMetadata,METADATA_KEY} from '../web/recommendations-metadata.js';

const book={siteId:'mangabz',detailUrl:'https://www.mangabz.com/91bz/',title:'作品',tags:['校园']};
test('sparse metadata preserves known tags and authors without importing chapters or unknown fields',()=>{
  const data=new Map(),storage={getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)},cache=createRecommendationMetadata({storage,now:()=>2000000000000});
  cache.remember(book,{author:'作者',description:'日常故事',tags:[],chapters:['private'],arbitrary:true});
  assert.deepEqual(cache.apply(book),{...book,author:'作者',description:'日常故事'});
  cache.remember(book,{author:'',description:'',tags:[]});assert.equal(cache.apply(book).author,'作者');
  assert.ok(!data.get(METADATA_KEY).includes('private'));
  const newer=createRecommendationMetadata({storage,now:()=>2000000000000+8*86400000});assert.equal(newer.has(book),false);
});

test('metadata storage is bounded and a storage failure cannot block recommendations',()=>{
  const data=new Map(),storage={getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)},cache=createRecommendationMetadata({storage,now:()=>2000000000000});
  for(let i=0;i<220;i++)cache.remember({...book,detailUrl:'https://www.mangabz.com/'+i+'bz/'},{author:'作者'+i});
  assert.equal(Object.keys(JSON.parse(data.get(METADATA_KEY))).length,200);
  const failed=createRecommendationMetadata({storage:{getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}},now:()=>2000000000000});
  assert.doesNotThrow(()=>failed.remember(book,{description:'来自详情的资料'}));assert.equal(failed.apply(book).description,'来自详情的资料');
});
