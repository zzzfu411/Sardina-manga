const layer = path => path.startsWith('/read/') ? 'reader' : path.startsWith('/m/') ? 'detail' : 'page';
const localPath = path => typeof path === 'string' && path.startsWith('/') && !path.startsWith('//');
const owned = value => value?.sardinaRoute === 1 && typeof value.id === 'string' && localPath(value.path) && Array.isArray(value.ancestors);
const overlayNames = new Set(['shelf', 'downloads', 'sources']);

/** Only traverse entries which this application actually inserted. */
export function createRouteHistory({history, location, makeId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`} = {}) {
  const path = () => location.pathname + (location.search || '');
  const ancestor = entry => ({id: entry.id, path: entry.path, layer: entry.layer});
  function current() {
    const value = history.state;
    if (owned(value) && value.path === path()) return value;
    const entry = {sardinaRoute: 1, id: makeId(), path: path(), layer: layer(path()), ancestors: [], overlays: [], background: localPath(value?.background) ? value.background : '/'};
    history.replaceState(entry, '', path());
    return entry;
  }
  current();
  return {
    overlays() {return (current().overlays || []).filter(name => overlayNames.has(name));},
    openOverlay(name) {
      if (!overlayNames.has(name)) return;
      const previous = current(), overlays = previous.overlays || [];
      if (overlays.includes(name)) return;
      history.pushState({...previous, id: makeId(), layer: 'overlay', ancestors: [...previous.ancestors, ancestor(previous)], overlays: [...overlays, name]}, '', previous.path);
    },
    closeOverlay(name) {
      const entry = current();
      if (entry.overlays?.at(-1) !== name) return false;
      if (entry.ancestors.length) {history.go(-1); return true;}
      history.replaceState({...entry, layer: layer(entry.path), overlays: []}, '', entry.path);
      return false;
    },
    navigate(path, {replace = false, background = '/'} = {}) {
      if (!localPath(path)) throw new Error('无效的应用路径');
      const previous = current();
      if (path === previous.path && !previous.overlays?.length) return;
      const nextLayer = layer(path);
      // Turning a chapter, or refreshing a details URL, stays in the same layer.
      replace ||= nextLayer !== 'page' && nextLayer === previous.layer;
      const ancestors = nextLayer === 'page' ? [] : replace ? previous.ancestors : [...previous.ancestors, ancestor(previous)];
      const entry = {sardinaRoute: 1, id: replace ? previous.id : makeId(), path, layer: nextLayer, ancestors, overlays: [], background: nextLayer === 'page' ? path : background};
      history[replace ? 'replaceState' : 'pushState'](entry, '', path);
    },
    /** Returns true when popstate will restore the parent, false for a deep-link fallback. */
    close(parentLayer, fallback) {
      const entry = current();
      const index = entry.ancestors.findLastIndex(item => (item.layer === parentLayer || parentLayer === 'page' && item.layer === 'overlay') && localPath(item.path));
      if (index >= 0) {
        history.go(index - entry.ancestors.length);
        return true;
      }
      this.navigate(fallback, {replace: true, background: parentLayer === 'page' ? fallback : entry.background});
      return false;
    },
  };
}
