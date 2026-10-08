// Resolve a single tap only after the short double-tap window. Never consume
// native pointer events: scrolling, pinch zoom and the long-press menu stay native.
export function createReaderGestures({onTap, onDoubleTap, onSwipe}) {
  const pointers = new Set();
  const delay = 260, movement = 8, hold = 400;
  let candidate = null, pending = null, timer = null;
  function clearPending() {
    clearTimeout(timer);
    timer = null; pending = null;
  }
  function cancel() {candidate = null; clearPending();}
  function reset() {cancel(); pointers.clear();}
  function down(point) {
    pointers.add(point.id);
    if (pointers.size !== 1 || !point.eligible || point.button !== 0) {cancel(); return;}
    const second = pending && point.time - pending.time <= delay && point.type === pending.type
      && Math.hypot(point.x - pending.x, point.y - pending.y) <= 28;
    if (second) {
      clearTimeout(timer);
      timer = null;
    } else if (pending) {
      const first = pending; clearPending(); onTap(first);
      pointers.add(point.id);
    }
    candidate = {...point, second: !!second};
  }
  function move(point) {
    if (candidate?.id !== point.id) return;
    const dx = Math.abs(point.x - candidate.x), dy = Math.abs(point.y - candidate.y);
    if (Math.hypot(dx, dy) <= movement) return;
    if (candidate.canSwipe && dx > dy * 1.5) {clearPending(); candidate.swiping = true;}
    else cancel();
  }
  function up(point) {
    pointers.delete(point.id);
    const start = candidate;
    if (!start || start.id !== point.id) return;
    candidate = null;
    if (start.swiping) {
      clearPending();
      const dx = point.x - start.x, dy = point.y - start.y;
      if (!pointers.size && point.eligible && point.time - start.time <= 700
        && Math.abs(dx) >= 50 && Math.abs(dx) > Math.abs(dy) * 1.5) onSwipe?.(dx < 0 ? 'ArrowRight' : 'ArrowLeft');
      return;
    }
    if (pointers.size || !point.eligible || point.time - start.time > hold
      || Math.hypot(point.x - start.x, point.y - start.y) > movement) {clearPending(); return;}
    if (start.second) {
      const first = pending; clearPending();
      // Double tapping a loading page still hides the chrome once. A loaded
      // image zooms only when both taps land on that same page.
      if (first?.zoomTarget && first.zoomTarget === point.zoomTarget) onDoubleTap(point);
      else onTap(point);
    } else {
      pending = point;
      timer = setTimeout(() => {timer = null; pending = null; onTap(point);}, delay);
    }
  }
  function pointerCancel(point) {pointers.delete(point.id); cancel();}
  return {down, move, up, cancel, reset, pointerCancel};
}
