const ROW_COUNT = 4;
const COVERS_PER_GROUP = 8;
const PLAYBACK_KEY = 'revyunman.cover-wall.playing.v1';

function publicUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch {return '';}
}

/** Keep the wall bounded, retaining only the metadata needed to open a book. */
export function coverWallBooks(books) {
  const covers = [], seen = new Set();
  for (const book of Array.isArray(books) ? books : []) {
    if (typeof book?.coverUrl !== 'string') continue;
    const coverUrl = publicUrl(book.coverUrl);
    if (!coverUrl) continue;
    const siteId = typeof book.siteId === 'string' ? book.siteId : '';
    const key = `${siteId}\n${coverUrl}`;
    if (seen.has(key)) continue;
    const cover = {coverUrl, siteId};
    const title = typeof book.title === 'string' ? book.title.trim() : '', detailUrl = publicUrl(book.detailUrl);
    if (siteId && title && detailUrl) {
      Object.assign(cover, {title, detailUrl, siteName: typeof book.siteName === 'string' ? book.siteName : ''});
    }
    seen.add(key); covers.push(cover);
    if (covers.length === ROW_COUNT * COVERS_PER_GROUP) break;
  }
  return covers;
}

export function coverWallRows(books) {
  const pool = coverWallBooks(books);
  if (!pool.length) return [];
  return Array.from({length: ROW_COUNT}, (_, row) => {
    const offset = Math.floor(pool.length * row / ROW_COUNT) + row;
    return Array.from({length: COVERS_PER_GROUP}, (_, index) => pool[(offset + index) % pool.length]);
  });
}

/** Covers pause while being selected; offscreen replicas add no keyboard stops. */
export function createCoverWall({root, imageUrl, control, onOpenBook}) {
  const doc = root.ownerDocument, view = doc.defaultView;
  const reducedMotion = view?.matchMedia?.('(prefers-reduced-motion: reduce)');
  let books = [], visible = false, explicitPlayback = null, rendered = '', generation = 0;
  let hovering = false, focusing = false, observer = null, syncTargets = () => {};
  try {const saved = JSON.parse(view.localStorage.getItem(PLAYBACK_KEY)); if (typeof saved === 'boolean') explicitPlayback = saved;} catch {}
  root.classList.add('cover-wall'); root.removeAttribute('inert'); root.hidden = true;
  if (onOpenBook) {root.removeAttribute('aria-hidden'); root.setAttribute('role', 'region'); root.setAttribute('aria-label', '精选漫画');}
  else {root.setAttribute('aria-hidden', 'true'); root.removeAttribute('aria-label');}
  control.classList.add('cover-wall-control'); control.type = 'button';
  control.hidden = true;
  // Keep the actual click target stable when focus/hover changes mid-click.
  const icon = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('viewBox', '0 0 20 20'); icon.setAttribute('aria-hidden', 'true');
  icon.setAttribute('focusable', 'false');
  const path = doc.createElementNS('http://www.w3.org/2000/svg', 'path');
  const text = doc.createElement('span'); icon.append(path); control.replaceChildren(icon, text);

  function motion() {
    const playing = explicitPlayback ?? !reducedMotion?.matches;
    const running = visible && books.length > 0 && !doc.hidden && playing && !hovering && !focusing;
    root.hidden = !visible || !books.length;
    root.dataset.motion = running ? 'running' : 'paused';
    control.hidden = !visible || !books.length;
    const label = playing ? '暂停封面流动' : '播放封面流动';
    control.setAttribute('aria-label', label); control.title = label;
    control.setAttribute('aria-pressed', String(!playing));
    control.dataset.action = playing ? 'pause' : 'play';
    path.setAttribute('d', playing ? 'M5 4h3v12H5zM12 4h3v12h-3z' : 'M6 3.5v13l10-6.5z');
    const caption = playing ? '暂停' : '播放';
    if (text.textContent !== caption) text.textContent = caption;
  }

  function render() {
    const signature = JSON.stringify(books);
    if (rendered === signature) return;
    rendered = signature;
    observer?.disconnect();
    const token = ++generation, fragment = doc.createDocumentFragment(), replicas = new Map(), failed = new Set();
    const ratios = new WeakMap();
    syncTargets = () => {
      for (const copies of replicas.values()) {
        const focused = copies.find(copy => copy === doc.activeElement && !copy.disabled);
        const candidate = focused || copies.find(copy => !copy.disabled && (ratios.get(copy) || 0) >= .6);
        for (const copy of copies) if (copy.tagName === 'BUTTON') {
          copy.tabIndex = copy === candidate ? 0 : -1;
          copy.setAttribute('aria-hidden', String(copy !== candidate));
        }
      }
    };
    observer = new view.IntersectionObserver(entries => {
      if (token !== generation) return;
      for (const entry of entries) ratios.set(entry.target, entry.intersectionRatio);
      syncTargets();
    }, {threshold: [0, .6, 1]});
    for (const row of coverWallRows(books)) {
      const track = doc.createElement('div'); track.className = 'cover-wall-track';
      for (let repeat = 0; repeat < 2; repeat++) {
        const group = doc.createElement('div'); group.className = 'cover-wall-group';
        for (const book of row) {
          const actionable = Boolean(onOpenBook && book.detailUrl);
          const tile = doc.createElement(actionable ? 'button' : 'span'); tile.className = 'cover-wall-cover';
          if (actionable) {
            tile.type = 'button'; tile.disabled = true; tile.tabIndex = -1;
            tile.setAttribute('aria-label', `打开《${book.title}》`); tile.setAttribute('aria-hidden', 'true');
            const caption = doc.createElement('span'); caption.className = 'cover-wall-title'; caption.textContent = book.title;
            tile.append(caption);
            tile.addEventListener('click', () => {explicitPlayback = false; motion(); onOpenBook({...book});});
            tile.addEventListener('pointerenter', event => {if (event.pointerType === 'mouse') {hovering = true; motion();}});
            tile.addEventListener('pointerleave', () => {hovering = false; motion();});
            observer.observe(tile);
          }
          const image = doc.createElement('img'); image.alt = ''; image.draggable = false;
          image.decoding = 'async'; image.fetchPriority = 'low'; image.referrerPolicy = 'no-referrer';
          const key = `${book.siteId}\n${book.coverUrl}`;
          if (!replicas.has(key)) replicas.set(key, []);
          replicas.get(key).push(tile);
          image.onload = () => {
            if (token !== generation || failed.has(key)) return;
            tile.classList.add('is-ready'); if (actionable) tile.disabled = false; syncTargets();
          };
          image.onerror = () => {
            if (token !== generation) return;
            failed.add(key);
            // Both copies retain the same geometry and appearance at the seam.
            for (const copy of replicas.get(key)) {copy.classList.add('is-unavailable'); if (actionable) copy.disabled = true;}
            syncTargets();
          };
          image.src = imageUrl(book.coverUrl, book.siteId);
          tile.append(image); group.append(tile);
        }
        track.append(group);
      }
      fragment.append(track);
    }
    root.replaceChildren(fragment);
  }

  root.addEventListener('focusin', () => {focusing = true; syncTargets(); motion();});
  root.addEventListener('focusout', event => {if (!root.contains(event.relatedTarget)) {focusing = false; syncTargets(); motion();}});
  control.addEventListener('click', () => {
    explicitPlayback = !(explicitPlayback ?? !reducedMotion?.matches);
    try {view.localStorage.setItem(PLAYBACK_KEY, JSON.stringify(explicitPlayback));} catch {}
    motion();
  });
  doc.addEventListener('visibilitychange', motion);
  if (reducedMotion?.addEventListener) reducedMotion.addEventListener('change', motion);
  else reducedMotion?.addListener?.(motion);
  motion();
  return {
    setBooks(value) {
      books = coverWallBooks(value);
      if (visible) render();
      motion();
    },
    setVisible(value) {
      visible = !!value;
      if (!visible) {hovering = false; focusing = false;}
      if (visible) render();
      motion();
    },
  };
}
