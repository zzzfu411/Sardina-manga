export const SOURCE_PREFERENCES_KEY = 'revyunman.source.preferences.v1';

const validId = value => typeof value === 'string' && /^[a-z][a-z0-9_-]{0,63}$/.test(value);

function normalize(preferences) {
  const ids = Array.isArray(preferences?.favoriteIds) ? preferences.favoriteIds : [];
  return {favoriteIds: [...new Set(ids.filter(validId))].slice(0, 256)};
}

export function loadSourcePreferences(storage) {
  try {
    const target = storage === undefined ? globalThis.localStorage : storage;
    return normalize(JSON.parse(target.getItem(SOURCE_PREFERENCES_KEY)));
  } catch {
    return {favoriteIds: []};
  }
}

/** Returns false when the browser cannot persist the change. */
export function saveSourcePreferences(preferences, storage) {
  try {
    const target = storage === undefined ? globalThis.localStorage : storage;
    target.setItem(SOURCE_PREFERENCES_KEY, JSON.stringify(normalize(preferences)));
    return true;
  } catch {
    return false;
  }
}

export function toggleFavorite(preferences, siteId) {
  const next = normalize(preferences);
  if (!validId(siteId)) return next;
  const index = next.favoriteIds.indexOf(siteId);
  if (index >= 0) next.favoriteIds.splice(index, 1);
  else if (next.favoriteIds.length < 256) next.favoriteIds.push(siteId);
  return next;
}

/** Only reorders existing sites. Unknown saved IDs can never register or hide a source. */
export function sortSources(sites, preferences) {
  if (!Array.isArray(sites)) return [];
  const favorites = new Set(normalize(preferences).favoriteIds);
  const preferred = [], remaining = [];
  for (const site of sites) {
    (favorites.has(site?.siteId) ? preferred : remaining).push(site);
  }
  return [...preferred, ...remaining];
}
