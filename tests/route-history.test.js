import test from 'node:test';
import assert from 'node:assert/strict';
import {createRouteHistory} from '../web/route-history.js';

function fixture(path = '/') {
  let index = 0, id = 0;
  const entries = [{path, state: null}], location = {pathname: path};
  const history = {
    get state() {return entries[index].state;},
    get length() {return entries.length;},
    replaceState(state, _, path) {entries[index] = {state, path}; location.pathname = path;},
    pushState(state, _, path) {entries.splice(++index, Infinity, {state, path}); location.pathname = path;},
    go(delta) {index += delta; location.pathname = entries[index].path;},
  };
  return {history, location, navigation: createRouteHistory({history, location, makeId: () => `entry-${++id}`})};
}

test('closing reader then details returns to discovery without adding close entries', () => {
  const {navigation, history, location} = fixture('/discover');
  navigation.navigate('/m/book', {background: '/discover'});
  navigation.navigate('/read/book/chapter-1', {background: '/discover'});
  navigation.navigate('/read/book/chapter-2', {background: '/discover'});
  assert.equal(history.length, 3);
  assert.equal(navigation.close('detail', '/m/book'), true);
  assert.equal(location.pathname, '/m/book');
  assert.equal(navigation.close('page', '/discover'), true);
  assert.equal(location.pathname, '/discover');
  assert.equal(history.length, 3);
  history.go(1);
  assert.equal(location.pathname, '/m/book');
  history.go(1);
  assert.equal(location.pathname, '/read/book/chapter-2');
  assert.equal(history.state.background, '/discover');
});

test('a direct reader link uses replacements and does not leave the app on close', () => {
  const {navigation, history, location} = fixture('/read/book/chapter-1');
  assert.equal(navigation.close('detail', '/m/book'), false);
  assert.equal(location.pathname, '/m/book');
  assert.equal(navigation.close('page', '/'), false);
  assert.equal(location.pathname, '/');
  assert.equal(history.length, 1);
});

test('reloading an owned entry retains its parent even if book metadata changes its token', () => {
  const {navigation, history, location} = fixture('/s/query');
  navigation.navigate('/m/old-title-token', {background: '/s/query'});
  navigation.navigate('/read/new-title-token/chapter', {background: '/s/query'});
  const reloaded = createRouteHistory({history, location});
  assert.equal(reloaded.close('detail', '/m/new-title-token'), true);
  assert.equal(location.pathname, '/m/old-title-token');
  assert.equal(history.length, 3);
});
