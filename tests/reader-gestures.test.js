import test from 'node:test';
import assert from 'node:assert/strict';
import {createReaderGestures} from '../web/reader-gestures.js';

function fixture() {
  let time = 0, nextTimer = 0;
  const timers = new Map(), actions = [];
  const gesture = createReaderGestures({
    onTap: point => actions.push(['tap', point.zoomTarget]),
    onDoubleTap: point => actions.push(['zoom', point.zoomTarget]),
    schedule: (callback, delay) => {timers.set(++nextTimer, {at: time + delay, callback}); return nextTimer;},
    unschedule: id => timers.delete(id),
  });
  function advance(ms) {
    time += ms;
    for (const [id, timer] of timers) if (timer.at <= time) {timers.delete(id); timer.callback();}
  }
  const point = changes => ({id: 1, type: 'touch', button: 0, x: 100, y: 300, time, eligible: true, zoomTarget: 'page-1', ...changes});
  function tap(changes) {gesture.down(point(changes)); advance(40); gesture.up(point(changes));}
  return {gesture, actions, advance, point, tap};
}

test('one short tap toggles once, with room for natural finger jitter', () => {
  const f = fixture(); f.gesture.down(f.point()); f.advance(60); f.gesture.move(f.point({x: 104, y: 303})); f.gesture.up(f.point({x: 104, y: 303}));
  assert.deepEqual(f.actions, []); f.advance(300); assert.deepEqual(f.actions, [['tap', 'page-1']]);
  f.advance(1000); assert.equal(f.actions.length, 1);
});

test('double tap zooms the touched page without hiding and showing the toolbars', () => {
  const f = fixture(); f.tap(); f.advance(130); f.tap({x: 103}); f.advance(500);
  assert.deepEqual(f.actions, [['zoom', 'page-1']]);
});

test('second tap can finish beyond the timer window if it began within it', () => {
  const f = fixture(); f.tap(); f.advance(240); f.gesture.down(f.point()); f.advance(80); f.gesture.up(f.point()); f.advance(500);
  assert.deepEqual(f.actions, [['zoom', 'page-1']]);
});

test('two taps on a loading page toggle once instead of attempting zoom', () => {
  const f = fixture(); f.tap({zoomTarget: null}); f.advance(50); f.tap({zoomTarget: null}); f.advance(500);
  assert.deepEqual(f.actions, [['tap', null]]);
});

test('swiping away and back to the starting point is never a tap', () => {
  const f = fixture(); f.gesture.down(f.point()); f.gesture.move(f.point({y: 390})); f.gesture.move(f.point()); f.gesture.up(f.point()); f.advance(500);
  assert.deepEqual(f.actions, []);
});

test('long press, secondary button, or release on an interactive control never toggles', () => {
  const f = fixture(); f.gesture.down(f.point()); f.advance(650); f.gesture.up(f.point());
  f.tap({type: 'mouse', button: 2});
  f.gesture.down(f.point()); f.gesture.up(f.point({eligible: false})); f.advance(500);
  assert.deepEqual(f.actions, []);
});

test('pinch rejects both fingers, including a second finger on chrome, then recovers', () => {
  const f = fixture(); f.gesture.down(f.point()); f.gesture.down(f.point({id: 2, eligible: false}));
  f.gesture.up(f.point({id: 2, eligible: false})); f.gesture.up(f.point()); f.advance(500);
  assert.deepEqual(f.actions, []); f.tap(); f.advance(500); assert.equal(f.actions.length, 1);
});

test('native scroll cancellation and later lifting another finger cannot re-arm a tap', () => {
  const f = fixture(); f.gesture.down(f.point()); f.gesture.down(f.point({id: 2}));
  f.gesture.pointerCancel(f.point()); f.gesture.up(f.point({id: 2})); f.advance(500); assert.deepEqual(f.actions, []);
  f.tap(); f.advance(500); assert.equal(f.actions.length, 1);
});

test('scroll, panel opening and session reset cancel pending taps', () => {
  for (const action of ['cancel', 'reset']) {
    const f = fixture(); f.tap(); f.gesture[action](); f.advance(500); assert.deepEqual(f.actions, []);
    f.tap(); f.advance(500); assert.equal(f.actions.length, 1);
  }
});

test('a button tap after a canvas tap does not later hide the button or its panel', () => {
  const f = fixture(); f.tap(); f.advance(50); f.tap({eligible: false}); f.advance(500);
  assert.deepEqual(f.actions, []);
});

test('distant taps and different pointer types remain separate taps', () => {
  for (const second of [{x: 180}, {type: 'mouse'}]) {
    const f = fixture(); f.tap(); f.advance(50); f.tap(second); f.advance(500);
    assert.deepEqual(f.actions, [['tap', 'page-1'], ['tap', 'page-1']]);
  }
});
