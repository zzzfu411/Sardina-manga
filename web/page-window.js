/** A bounded page over the full filtered collection. Indices are zero-based. */
export function pageWindow(items, {page = 0, size = 48} = {}) {
  size = Number.isFinite(size) ? Math.max(1, Math.trunc(size)) : 48;
  const total = items.length, pages = Math.max(1, Math.ceil(total / size));
  page = Math.max(0, Math.min(pages - 1, Number.isFinite(page) ? Math.trunc(page) : 0));
  const start = page * size, end = Math.min(total, start + size);
  return {items: items.slice(start, end), page, pages, start, end, total, hasPrevious: page > 0, hasNext: page + 1 < pages};
}

export function pageForIndex(index, size = 48) {
  return Math.floor(Math.max(0, Number(index) || 0) / Math.max(1, Math.trunc(Number(size) || 48)));
}
