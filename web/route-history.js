const layer = path => path.startsWith('/read/') ? 'reader' : path.startsWith('/m/') ? 'detail' : 'page';
const localPath = path => typeof path === 'string' && path.startsWith('/') && !path.startsWith('//');
const owned = value => value?.sardinaRoute === 1 && typeof value.id === 'string' && localPath(value.path) && Array.isArray(value.ancestors);

/** Only traverse entries which this application actually inserted. */
export function createRouteHistory({history, location, makeId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`} = {}) {
  function current() {
    const value = history.state;
    if (owned(value) && value.path === location.pathname) return value;
    const entry = {sardinaRoute: 1, id: makeId(), path: location.pathname, layer: layer(location.pathname), ancestors: [], background: localPath(value?.background) ? value.background : '/'};
    history.replaceState(entry, '', location.pathname);
    return entry;
  }
  current();
  return {
    navigate(path, {replace = false, background = '/'} = {}) {
      if (!localPath(path)) throw new Error('无效的应用路径');
      const previous = current();
      if (path === location.pathname) return;
      const nextLayer = layer(path);
      // Turning a chapter, or refreshing a details URL, stays in the same layer.
      replace ||= nextLayer !== 'page' && nextLayer === previous.layer;
      const ancestors = nextLayer === 'page' ? [] : replace ? previous.ancestors : [...previous.ancestors, {id: previous.id, path: previous.path, layer: previous.layer}];
      const entry = {sardinaRoute: 1, id: replace ? previous.id : makeId(), path, layer: nextLayer, ancestors, background: nextLayer === 'page' ? path : background};
      history[replace ? 'replaceState' : 'pushState'](entry, '', path);
    },
    /** Returns true when popstate will restore the parent, false for a deep-link fallback. */
    close(parentLayer, fallback) {
      const entry = current();
      const index = entry.ancestors.findLastIndex(item => item.layer === parentLayer && localPath(item.path));
      if (index >= 0) {
        history.go(index - entry.ancestors.length);
        return true;
      }
      this.navigate(fallback, {replace: true, background: parentLayer === 'page' ? fallback : entry.background});
      return false;
    },
  };
}
