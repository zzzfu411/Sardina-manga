import {createRecommendationsModel, recommendationReason} from './recommendations-model.js';

const node = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const button = (label, className, action) => {
  const element = node('button', className, label); element.type = 'button';
  element.addEventListener('click', action); return element;
};
const timeText = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(date);
};

export function createRecommendations({root, api, imageUrl, onOpenBook, getShelf}) {
  root.classList.add('recommendations'); root.hidden = true;
  const toolbar = node('div', 'recommendations-toolbar');
  const settings = node('details', 'recommendations-settings');
  settings.append(node('summary', '', '推荐设置'));
  const settingsBody = node('div', 'recommendations-settings-body');
  const personalizationLabel = node('label');
  const personalization = node('input'); personalization.type = 'checkbox';
  personalization.addEventListener('change', () => model.setPersonalization(personalization.checked));
  personalizationLabel.append(personalization, document.createTextNode('参考我的书架偏好'));
  const feedbackCount = node('p');
  const clearFeedback = button('清空推荐记录', 'recommendations-clear', () => model.clearFeedback());
  settingsBody.append(personalizationLabel, feedbackCount, clearFeedback); settings.append(settingsBody);
  const actions = node('div', 'recommendations-actions');
  const refresh = button('刷新', 'recommendations-refresh', () => model.refresh()); refresh.id = 'recommendations-refresh';
  const next = button('换一批', 'recommendations-next', () => model.nextBatch()); next.id = 'recommendations-next';
  actions.append(refresh, next); toolbar.append(settings, actions);
  const context = node('div', 'recommendations-context');
  const fetched = node('time'); fetched.id = 'recommendations-fetched';
  const origins = node('div', 'recommendations-origins'); context.append(fetched, origins);
  const warnings = node('p', 'recommendations-warnings'); warnings.setAttribute('role', 'status');
  const undoPanel = node('div', 'recommendations-undo'); undoPanel.hidden = true; undoPanel.setAttribute('role', 'status');
  const undoMessage = node('span');
  const undo = button('撤销', 'recommendations-undo-button', () => {model.undoDismiss(); grid.querySelector('button')?.focus({preventScroll: true});});
  undoPanel.append(undoMessage, undo);
  const status = node('div', 'recommendations-status'); status.id = 'recommendations-status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const message = node('p');
  const retry = button('重试', 'recommendations-refresh', () => model.refresh()); status.append(message, retry);
  const grid = node('ul', 'recommendations-grid'); grid.id = 'recommendations-grid'; grid.setAttribute('aria-label', '推荐漫画');
  root.replaceChildren(toolbar, context, warnings, undoPanel, status, grid);
  const cards = new Map();
  const exposureTimers = new Map();
  let originSignature = '';

  function createCard(id) {
    const element = node('li', 'recommendation-card'); element.dataset.cardId = id;
    const frame = node('div', 'recommendation-cover-frame');
    const view = {element, revision: null, card: null, image: null};
    const open = () => {model.opened(id); onOpenBook(view.card.book);};
    view.open = button('', 'recommendation-cover', open);
    view.placeholder = node('span', 'recommendation-cover-loading', '封面加载中'); view.placeholder.setAttribute('aria-hidden', 'true'); view.open.append(view.placeholder);
    view.error = node('div', 'recommendation-cover-error');
    const errorMessage = node('p', '', '封面未加载');
    view.retry = button('重试封面', 'recommendation-cover-retry', () => model.retryCover(id));
    view.replace = button('换一本', 'recommendation-cover-replace', () => model.replaceCover(id));
    view.error.append(errorMessage, view.retry, view.replace); frame.append(view.open, view.error);
    const heading = node('h3');
    view.title = button('', 'recommendation-title', open); heading.append(view.title);
    view.saved = node('span', 'recommendation-saved', '已在书架'); view.saved.hidden = true;
    view.reason = node('p', 'recommendation-reason');
    view.dismiss = button('不感兴趣', 'recommendation-dismiss', () => model.dismiss(id));
    element.append(frame, heading, view.saved, view.reason, view.dismiss);
    return view;
  }
  function renderCard(view, card) {
    view.card = card;
    view.title.textContent = card.book.title;
    view.open.setAttribute('aria-label', `打开《${card.book.title}》`);
    view.retry.setAttribute('aria-label', `重试《${card.book.title}》的封面`);
    view.replace.setAttribute('aria-label', `替换《${card.book.title}》的推荐`);
    view.dismiss.setAttribute('aria-label', `对《${card.book.title}》不感兴趣`);
    view.saved.hidden = !card.onShelf;
    view.reason.textContent = card.reason || recommendationReason(card.book);
    view.open.hidden = card.coverStatus === 'error'; view.error.hidden = card.coverStatus !== 'error';
    view.placeholder.hidden = card.coverStatus === 'ready'; view.replace.hidden = !card.canReplace;
    if (view.revision === card.revision) return;
    const shouldRestoreFocus = view.element.contains(document.activeElement);
    view.revision = card.revision; view.image?.remove();
    clearTimeout(exposureTimers.get(card.id)); exposureTimers.delete(card.id);
    observer?.unobserve(view.element); observer?.observe(view.element);
    const image = node('img'); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async'; image.referrerPolicy = 'no-referrer';
    image.addEventListener('load', () => model.coverLoaded(card.id, card.revision), {once: true});
    image.addEventListener('error', () => model.coverFailed(card.id, card.revision), {once: true});
    let src = imageUrl(card.book.coverUrl, card.book.siteId);
    if (card.retry) {const url = new URL(src, document.baseURI); url.searchParams.set('coverRetry', String(card.retry)); src = url.href;}
    view.image = image; view.open.append(image); image.src = src;
    if (shouldRestoreFocus) view.title.focus({preventScroll: true});
  }
  function render(state) {
    root.hidden = !state.visible;
    if (!state.visible) return;
    const busy = state.phase === 'loading';
    refresh.disabled = busy; next.disabled = busy || !state.canNext;
    next.title = state.canNext ? '继续查看未浏览的作品' : '本轮候选已看完，稍后刷新看看更新';
    personalization.checked = state.personalization;
    feedbackCount.textContent = state.dismissedCount ? `已忽略 ${state.dismissedCount} 本。记录保存在本机。` : '推荐记录保存在本机。';
    if (state.metrics.opens) feedbackCount.textContent += ` 已打开 ${state.metrics.opens} 本，开始阅读 ${state.metrics.readingStarts} 次，阅读至少 3 页 ${state.metrics.continuedReads} 次，打开失败 ${state.metrics.openFailures} 次。`;
    undoPanel.hidden = !state.canUndo;
    undoMessage.textContent = state.canUndo ? `已忽略《${state.dismissedTitle}》` : '';
    grid.setAttribute('aria-busy', String(busy));
    const warningMessages = [...state.warnings, state.feedbackWarning].filter(Boolean);
    warnings.hidden = !warningMessages.length; warnings.textContent = warningMessages.join('；');
    const at = timeText(state.fetchedAt);
    fetched.hidden = !at; fetched.textContent = at ? `获取于 ${at}` : '';
    if (at) {fetched.dateTime = state.fetchedAt; fetched.title = new Date(state.fetchedAt).toLocaleString('zh-CN');}
    else {fetched.removeAttribute('datetime'); fetched.removeAttribute('title');}
    const visibleOrigins = state.origins.filter(origin => state.cards.some(card =>
      card.book.siteId === origin.siteId && card.book.recommendationKind === origin.kind));
    const signature = JSON.stringify(visibleOrigins);
    if (signature !== originSignature) {
      origins.replaceChildren(...visibleOrigins.map(origin => {
        const link = node(origin.sourceUrl ? 'a' : 'span', '', `${origin.siteName}${origin.kind === 'popular' ? '热门' : '更新'}`);
        if (origin.sourceUrl) {link.href = origin.sourceUrl; link.target = '_blank'; link.rel = 'noopener noreferrer';}
        return link;
      }));
      originSignature = signature;
    }
    context.hidden = !at && !visibleOrigins.length;
    status.hidden = !busy && state.phase !== 'error' && Boolean(state.cards.length);
    status.classList.toggle('recommendations-status-error', state.phase === 'error');
    status.classList.toggle('recommendations-status-inline', Boolean(state.cards.length));
    retry.hidden = state.phase !== 'error';
    message.textContent = busy ? (state.cards.length ? '正在刷新推荐…' : '正在准备推荐…') : state.phase === 'error' ?
      `${state.cards.length ? '刷新失败' : '推荐加载失败'}：${state.error}` : state.emptyReason === 'shelf' ?
        '这批作品已经在书架里，可以换一批。' : state.emptyReason === 'dismissed' ?
          '这批作品已忽略，可以换一批或在设置中清空推荐记录。' : state.emptyReason === 'covers' ?
          '封面暂时无法加载，请刷新重试。' : '暂时没有可推荐的作品，稍后刷新试试。';
    const activeIds = new Set(state.cards.map(card => card.id));
    for (const [id, view] of cards) if (!activeIds.has(id)) {
      const hadFocus = view.element.contains(document.activeElement);
      observer?.unobserve(view.element); clearTimeout(exposureTimers.get(id)); exposureTimers.delete(id);
      view.element.remove(); cards.delete(id);
      if (hadFocus && state.canUndo) undo.focus({preventScroll: true});
    }
    for (const card of state.cards) {
      let view = cards.get(card.id);
      if (!view) {view = createCard(card.id); cards.set(card.id, view); grid.append(view.element);}
      renderCard(view, card);
    }
  }
  const model = createRecommendationsModel({api, getShelf, onChange: render});
  const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    for (const entry of entries) {
      const id = entry.target.dataset.cardId, view = cards.get(id);
      clearTimeout(exposureTimers.get(id)); exposureTimers.delete(id);
      if (!view || !entry.isIntersecting || entry.intersectionRatio < .5 || root.hidden) continue;
      const revision = view.card.revision;
      exposureTimers.set(id, setTimeout(() => {
        exposureTimers.delete(id);
        if (!root.hidden && document.visibilityState !== 'hidden' && view.card.revision === revision) {
          model.markExposed(id, revision); observer.unobserve(view.element);
        }
      }, 500));
    }
  }, {threshold: [.5]}) : null;
  return {show: () => model.show(), hide: () => model.hide(), shelfChanged: () => model.shelfChanged(), rememberMetadata: (book, value) => model.rememberMetadata(book, value), recordRead: (book, progress) => model.recordRead(book, progress), recordFailure: book => model.recordFailure(book)};
}
