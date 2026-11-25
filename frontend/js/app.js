/* Agentic desk: plain JavaScript, no build step. Talks to the backend under /api.
   On github.io there is no backend, so the page reads js/snapshot.js (written by backend/publish.py)
   and every action is switched off. */

const view = document.getElementById('view');
const PUBLIC = location.hostname.endsWith('github.io') || location.protocol === 'file:';
const state = { overview: null, chat: [], storeFilter: 'SALE', lastEvents: null, snap: PUBLIC ? (window.SNAPSHOT || null) : null,
  edits: {},             // draft id -> {title, price, category} typed on Approvals but not sent yet
  sending: new Set() };  // draft ids whose approve, register or delete request is still running
const currentView = () => location.hash.replace('#/', '') || 'today';

// Show a page only if it is still the one asked for: a page that finishes loading late (the Dashboard waiting on
// the store, an Approvals redraw after a slow registration) must not cover the page the reviewer has moved to.
function paint(name, ...nodes) {
  const asked = currentView();
  if ((views[asked] ? asked : 'today') === name) view.replaceChildren(...nodes);
}

// ---------------------------------------------------------------- helpers
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value == null) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Line icon from the sprite in index.html.
function icon(name) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', 'ico');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(ns, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

const won = (n) => Number(n).toLocaleString('en-US');
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const STATUS = { SALE: 'On sale', HIDDEN: 'Hidden', SUSPENSION: 'Suspended', OUTOFSTOCK: 'Out of stock', WAIT: 'Waiting', CLOSE: 'Closed' };
const statusName = (s) => STATUS[s] || s;

async function api(path, options = {}) {
  if (PUBLIC) return snapshotApi(path, options);
  const init = { method: options.method || 'GET' };
  if (options.body) {
    init.method = 'POST';
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(options.body);
  }
  const fail = (message, status) => Object.assign(new Error(message), { status });   // status 0: no answer at all
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    throw fail('The backend is not answering. Is "python server.py" still running?', 0);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw fail(data.error || `Request failed (${response.status})`, response.status);
  return data;
}

// Public copy: answer from the exported data. Nothing is sent anywhere.
function snapshotApi(path, options) {
  if (!state.snap) throw new Error('No data has been published yet. On the desk machine, run: python publish.py');
  if (options.body) throw new Error('This page is read-only. Decisions are made on the team\'s own desk.');
  const url = new URL(path, 'http://desk');
  const s = state.snap;
  if (url.pathname === '/api/overview') return s.overview;
  if (url.pathname === '/api/drafts') return s.drafts[url.searchParams.get('status') || 'pending'] || [];
  if (url.pathname === '/api/activity') return s.activity;
  if (url.pathname === '/api/sourcing') return s.sourcing || [];
  if (url.pathname === '/api/price') return priceCheck(url.searchParams, s.overview.rules);
  throw new Error('Not available on the read-only page.');
}

// Same arithmetic as backend/price.py, so the public copy can work out a floor without a server.
function priceCheck(q, rules) {
  const num = (key) => Math.max(0, Math.floor(Number(q.get(key)) || 0));
  const cost = num('cost'), shipping = num('shipping'), ad = num('ad'), target = num('target');
  if (cost <= 0) throw new Error('Enter the supplier cost.');
  const landed = cost + shipping;
  const floor = Math.ceil((landed + ad) / (1 - rules.fee_rate - rules.min_margin) / 10) * 10;
  const price = target || floor;
  const profit = price * (1 - rules.fee_rate) - landed - ad;
  const round = (x) => { const r = Math.round(x); return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };  // halves go to even, as Python does
  return {
    landed_cost: landed, ad, floor, price, has_target: Boolean(target),
    fee: round(price * rules.fee_rate), profit: round(profit),
    margin_pct: Math.round((profit / price) * 1000) / 10, clears: price >= floor, gap: price - floor,
  };
}

const when = (iso) => (iso || '').replace('T', ' ');

let toastTimer;
function toast(message, isError = false, ms = 3200) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = 'toast'; }, ms);
}

const ACTIONS = { draft_approved: 'Draft approved', draft_rejected: 'Draft rejected', draft_deleted: 'Draft deleted',
  draft_listed: 'Listed in the store', draft_described: 'Introduction written by the agent', drafts_generated: 'Drafts created', gate0_reject: 'Stopped at the price floor',
  chat: 'Question asked', sourcing_run: 'Products searched' };

function head(title, sub, ...actions) {
  return h('div', { class: 'head' },
    h('div', {}, h('h1', {}, title), sub && h('p', {}, sub)),
    actions.length ? h('div', { class: 'actions' }, actions) : null);
}

async function refreshOverview() {
  const o = await api('/api/overview');
  state.overview = o;
  const pct = Math.min(100, Math.round((o.tokens.used / o.tokens.cap) * 100));
  document.getElementById('meter-num').textContent = `${won(o.tokens.used)} of ${won(o.tokens.cap)}`;
  document.getElementById('meter-fill').style.width = pct + '%';
  document.getElementById('meter-track').classList.toggle('over', pct >= 90);
  document.getElementById('meter-foot').textContent = `${o.tokens.model}, resets Monday`;
  for (const id of ['nav-pending', 'bell-count']) {
    const badge = document.getElementById(id);
    badge.hidden = o.drafts.pending === 0;
    badge.textContent = o.drafts.pending;
  }

  // Mission card at the foot of the menu.
  const goal = o.mission.orders, count = o.orders.count, left = daysLeft(o.mission.deadline);
  document.getElementById('side-orders').textContent = count;
  document.getElementById('side-goal').textContent = `of ${goal} orders`;
  document.getElementById('side-fill').style.width = `${Math.min(100, goal ? (count / goal) * 100 : 0)}%`;
  document.getElementById('side-foot').textContent = state.snap
    ? `Read-only copy. Data from ${when(state.snap.exported_at)}.`
    : `${plural(left, 'day', 'days')} left · goal ${longDate(o.mission.deadline)}`;

  const avatar = document.getElementById('avatar');
  avatar.textContent = (o.reviewer || '·').trim().charAt(0) || '·';
  avatar.title = state.snap ? 'Group 3' : `Signed in as ${o.reviewer}`;
  return o;
}

// ---------------------------------------------------------------- price ruler
function ruler({ cost, floor, price, big = false }) {
  const max = Math.max(price || 0, floor) * 1.18;
  const pos = (v) => Math.max(0, Math.min(100, (v / max) * 100));
  const edge = (p) => (p > 84 ? 'edge-right' : p < 10 ? 'edge-left' : '');
  const node = h('div', { class: 'ruler' + (big ? ' big' : ''), role: 'img',
    'aria-label': `Cost ${won(cost)}, floor ${won(floor)}${price ? `, price ${won(price)}` : ''} KRW` });
  const track = h('div', { class: 'ruler-track' },
    h('div', { class: 'ruler-below', style: `width:${pos(floor)}%` }),
    h('div', { class: 'ruler-cost', style: `width:${pos(cost)}%` }));
  node.append(track, h('div', { class: 'ruler-cost-label' }, `cost ${won(cost)}`));
  const floorPos = pos(floor);
  node.append(h('div', { class: `ruler-mark ${edge(floorPos)}`, style: `left:${floorPos}%` },
    h('span', {}, `floor ${won(floor)}`)));
  if (price) {
    const pricePos = pos(price);
    node.append(h('div', { class: `ruler-pin ${price < floor ? 'under' : ''} ${edge(pricePos)}`, style: `left:${pricePos}%` },
      h('span', {}, `price ${won(price)}`)));
  }
  return node;
}

// ---------------------------------------------------------------- Dashboard
const DAY = 86400000;
const shortDate = (iso) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
const longDate = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'long' });

function daysLeft(deadline) {
  const end = new Date(deadline + 'T23:59:59+09:00');
  return Math.max(0, Math.ceil((end - Date.now()) / DAY));
}

function ring(count, goal) {
  const r = 54, c = 2 * Math.PI * r;
  const share = Math.max(0, Math.min(1, goal ? count / goal : 0));
  const node = h('div', { class: 'ring', role: 'img', 'aria-label': `${count} of ${goal} orders` });
  node.innerHTML =
    `<svg viewBox="0 0 132 132" aria-hidden="true"><circle class="ring-track" cx="66" cy="66" r="${r}"/>` +
    `<circle class="ring-fill" cx="66" cy="66" r="${r}" stroke-dasharray="${c.toFixed(1)}" ` +
    `stroke-dashoffset="${(c * (1 - share)).toFixed(1)}" style="--full:${c.toFixed(1)}"/></svg>`;
  node.append(h('div', { class: 'ring-num' }, h('strong', {}, count), h('span', {}, `of ${goal} orders`)));
  return node;
}

function belt(d) {
  const total = d.pending + d.approved + d.rejected + d.published;
  const stations = [['Drafted by the agent', total], ['Waiting for a person', d.pending], ['Approved', d.approved], ['Listed in the store', d.published]];
  const parcels = Math.min(total, 5);
  return h('div', { class: 'belt' + (total ? '' : ' idle') },
    h('ol', { class: 'stations' }, stations.map(([label, n]) => h('li', { class: n ? '' : 'zero' }, h('strong', {}, n), h('span', {}, label)))),
    h('div', { class: 'belt-track', 'aria-hidden': 'true' },
      Array.from({ length: parcels }, (_, i) => h('i', { class: 'parcel', style: `--i:${i};animation-delay:${(-i * 9 / parcels).toFixed(2)}s` }))),
    h('p', { class: 'belt-note' }, total
      ? (d.rejected ? `${plural(d.rejected, 'draft was', 'drafts were')} turned back at the desk. ` : '') + 'Nothing reaches the store without a person approving it.'
      : 'The belt is empty. Drafts appear once the agent proposes listings.'));
}

function ordersChart(orders) {
  const days = orders.by_day;
  const top = Math.max(4, ...days.map((x) => x.n));
  const total = days.reduce((sum, x) => sum + x.n, 0);
  return h('section', { class: 'panel' },
    h('h2', {}, icon('chart'), 'Orders per day'),
    h('p', { class: 'panel-sub' }, total ? `${plural(total, 'order', 'orders')} in the last ${days.length} days.` : `No orders in the last ${days.length} days yet.`),
    h('div', { class: 'bars', role: 'img', 'aria-label': days.map((x) => `${shortDate(x.date)}: ${x.n}`).join(', ') },
      days.map((x, i) => h('div', { class: 'bar' },
        h('span', { class: 'bar-n' }, x.n || ''),
        h('i', { style: `height:${(x.n / top) * 100}%` }),
        h('span', { class: 'bar-day' }, i % 2 === days.length % 2 ? '' : shortDate(x.date))))),
    orders.error ? h('p', { class: 'panel-warn' }, 'Orders could not be read just now, so this may be behind.') : null);
}

function marginWatch(products) {
  const sale = products.filter((p) => p.status === 'SALE');
  const known = sale.filter((p) => p.cost).sort((a, b) => (a.price - a.floor) - (b.price - b.floor));
  const unknown = sale.filter((p) => !p.cost);
  if (!sale.length) return null;
  return h('section', { class: 'panel' },
    h('h2', {}, icon('eye'), 'Margin watch'),
    h('p', { class: 'panel-sub' }, 'Each product on sale against its price floor. Cost is read from the seller code in the store.'),
    known.map((p) => {
      const gap = p.price - p.floor;
      return h('div', { class: 'watch' + (gap < 0 ? ' under' : '') },
        h('div', { class: 'watch-head' },
          h('span', { class: 'watch-name' }, p.name),
          h('strong', {}, gap < 0 ? `Below the floor by ${won(-gap)} KRW` : `Clears the floor by ${won(gap)} KRW`)),
        ruler({ cost: p.cost, floor: p.floor, price: p.price }),
        h('p', { class: 'watch-foot' }, `${p.profit < 0 ? 'Loses' : 'Earns'} ${won(Math.abs(p.profit))} KRW a sale before ads, a ${p.margin_pct}% margin.`));
    }),
    unknown.length ? h('p', { class: 'watch-missing' },
      `${plural(unknown.length, 'product has', 'products have')} no recorded cost: ${unknown.map((p) => p.name).join('; ')}. ` +
      'Write the cost as 원가 followed by the amount in a seller code field to include it.') : null);
}

async function today() {
  const o = await refreshOverview();
  const [pending, log] = await Promise.all([api('/api/drafts?status=pending'), api('/api/activity?limit=6')]);
  const d = o.drafts;
  const goal = o.mission.orders;
  const count = o.orders.count;
  const left = daysLeft(o.mission.deadline);
  const toGo = Math.max(0, goal - count);
  const recent = o.orders.by_day.slice(-7).reduce((sum, x) => sum + x.n, 0) / 7;

  const pace = toGo === 0 ? 'The goal is met.'
    : left === 0 ? `${toGo} short of the goal, and the deadline has passed.`
    : `${toGo} to go, which is ${(toGo / left).toFixed(1)} a day. The last 7 days averaged ${recent.toFixed(1)} a day.`;
  const waiting = d.pending > 0
    ? `${plural(d.pending, 'draft is', 'drafts are')} waiting for ${state.snap ? "the team's" : 'your'} approval.`
    : (state.snap ? 'No drafts are waiting for approval.' : 'Nothing is waiting for you.');

  const shop = o.store.error ? null : o.store;
  const sale = shop ? shop.products.filter((p) => p.status === 'SALE') : [];
  const priced = sale.filter((p) => p.cost);
  const under = priced.filter((p) => p.price < p.floor).length;

  const kpi = (label, value, sub, ic, href, alarm = false) => h(href ? 'a' : 'div', { class: 'kpi' + (alarm ? ' alarm' : ''), href },
    h('div', { class: 'kpi-text' }, h('span', { class: 'kpi-label' }, label), h('strong', {}, value), h('small', {}, sub)),
    h('span', { class: 'kpi-icon' }, icon(ic)));
  const panelHead = (ic, title, link, linkText) => h('div', { class: 'panel-head' },
    h('h2', {}, icon(ic), title),
    link ? h('a', { class: 'panel-link', href: link }, linkText, icon('arrow')) : null);

  const quick = state.snap ? null : h('nav', { class: 'board-actions', 'aria-label': 'Next steps' },
    [['#/sourcing', 'search', 'Find products', 'Search the supplier, no tokens'],
     ['#/approvals', 'check', 'Review drafts', d.pending ? `${plural(d.pending, 'draft', 'drafts')} waiting` : 'Queue is empty'],
     ['#/ask', 'bot', 'Ask the agent', 'About 1,000 tokens a question']].map(([href, ic, title, sub]) =>
      h('a', { href }, h('span', { class: 'qa-icon' }, icon(ic)), h('span', {}, h('strong', {}, title), h('small', {}, sub)), h('span', { class: 'go' }, icon('arrow')))));

  const queuePanel = h('section', { class: 'panel' },
    panelHead('check', 'Waiting for approval', pending.length ? '#/approvals' : null, 'Open all'),
    h('p', { class: 'panel-sub' }, pending.length ? `${plural(pending.length, 'draft', 'drafts')} priced and ready for a decision.` : 'The queue is empty.'),
    pending.length
      ? h('ul', { class: 'queue' }, pending.slice(0, 5).map((x) => h('li', {},
          x.extra && x.extra.thumb ? h('img', { src: x.extra.thumb, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }) : h('span', { class: 'no-pic' }),
          h('div', { style: 'min-width:0' },
            h('a', { href: '#/approvals' }, x.title),
            h('span', {}, `${won(x.price)} KRW`, h('span', { class: 'm' }, `${x.margin_pct}%`))))))
      : null);

  const activityPanel = h('section', { class: 'panel' },
    panelHead('activity', 'Recent activity', '#/activity', 'Full log'),
    log.length
      ? h('ul', { class: 'mini-log', style: 'margin-top:var(--s4)' }, log.slice(0, 6).map((r) => h('li', {},
          h('span', {}, when(r.ts).slice(5, 16)), h('strong', {}, ACTIONS[r.action] || r.action), h('em', {}, r.detail))))
      : h('p', { class: 'panel-sub' }, 'Nothing has happened yet.'));

  const storePanel = h('section', { class: 'panel' },
    panelHead('store', 'Store', '#/store', 'All products'),
    storeBar(o.store),
    h('p', { class: 'panel-rules' },
      `Every price must leave a ${Math.round(o.rules.min_margin * 100)}% margin after ${(o.rules.fee_rate * 100).toFixed(2)}% in fees. ` +
      `Titles stay within ${o.rules.max_title} characters. The agent drafts and answers; ${o.reviewer} approves.`));

  const margin = (!o.store.error && marginWatch(o.store.products)) || null;

  paint('today', 
    h('section', { class: 'board' },
      h('div', { class: 'board-main' },
        ring(count, goal),
        h('div', { class: 'board-copy' },
          h('p', { class: 'lede' }, `${plural(left, 'day', 'days')} left to reach ${goal} orders.`),
          h('p', { class: 'lede-sub' }, pace),
          h('p', { class: 'lede-sub' }, waiting, ' ',
            d.pending > 0 ? h('a', { href: '#/approvals' }, state.snap ? 'See the drafts' : 'Review drafts') : ''),
          h('p', { class: 'board-foot' }, `Goal date ${longDate(o.mission.deadline)}.` + (o.orders.since ? ` Orders counted since ${longDate(o.orders.since)}.` : ''))),
        quick),
      belt(d)),

    h('div', { class: 'kpis' },
      kpi('Products on sale', shop ? sale.length : '–', shop ? `${shop.products.length - sale.length} not on sale` : 'store not read', 'box', '#/store'),
      kpi('Priced below the floor', shop ? under : '–', shop ? `of ${priced.length} with a recorded cost` : 'store not read', 'alert', null, Boolean(under)),
      kpi('Drafts waiting', d.pending, `${d.approved} approved, ${d.rejected} rejected`, 'clock', '#/approvals'),
      kpi('Tokens this week', won(o.tokens.used), `of ${won(o.tokens.cap)}`, 'coins', null)),

    h('div', { class: 'dash-row' }, ordersChart(o.orders), queuePanel),

    margin
      ? h('div', { class: 'dash-row' }, margin, h('div', { class: 'dash-stack' }, storePanel, activityPanel))
      : h('div', { class: 'dash-row even' }, storePanel, activityPanel),
  );
}

function storeBar(store) {
  if (store.error) return h('p', { class: 'notice' }, store.error);
  const entries = Object.entries(store.count_by_status).sort((a, b) => (a[0] === 'SALE' ? -1 : b[0] === 'SALE' ? 1 : b[1] - a[1]));
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return h('p', { class: 'muted' }, 'The store has no products yet.');
  const cls = (s) => (['SALE', 'SUSPENSION', 'OUTOFSTOCK'].includes(s) ? `seg-${s}` : 'seg-other');
  return h('div', {},
    h('div', { class: 'statusbar', role: 'img', 'aria-label': entries.map(([s, n]) => `${n} ${statusName(s)}`).join(', ') },
      entries.map(([s, n]) => h('span', { class: cls(s), style: `width:${(n / total) * 100}%` }))),
    h('ul', { class: 'legend' }, entries.map(([s, n]) => h('li', {}, h('i', { class: cls(s) }), `${n} ${statusName(s).toLowerCase()}`))));
}

// ---------------------------------------------------------------- Approvals
async function approvals() {
  if (state.snap) return approvalsReadOnly();
  const [list, ready, listed] = await Promise.all([api('/api/drafts?status=pending'), api('/api/drafts?status=approved'),
    api('/api/drafts?status=published'), refreshOverview()]);
  if (currentView() !== 'approvals') return;   // the reviewer moved on while this loaded
  const maxTitle = state.overview.rules.max_title;

  const nodes = [head('Approvals',
    'Set the price, check the title, the introduction and the Naver category, then approve. Approving registers the product in the store and shows it at once, unless you untick "Show in the store right away".')];

  if (state.lastEvents && state.lastEvents.length) {
    const label = { gate0_reject: 'Below the price floor', skip: 'Skipped', draft: 'Drafted', notice: 'Note', error: 'Problem' };
    nodes.push(h('ul', { class: 'events' }, state.lastEvents.map((e) =>
      h('li', {}, h('strong', {}, `${label[e.kind] || e.kind}${e.item_id ? ` ${e.item_id}` : ''}: `), e.message))));
    state.lastEvents = null;
  }

  if (!list.length) {
    nodes.push(h('div', { class: 'empty' },
      h('h3', {}, 'No drafts are waiting'),
      h('p', {}, 'Ask the agent to find products on the Sourcing page. Its picks arrive here for you to price and approve.'),
      h('a', { class: 'btn primary', href: '#/sourcing', style: 'text-decoration:none;display:inline-block' }, 'Go to Sourcing')));
  } else {
    nodes.push(h('div', { class: 'ticket-grid' }, list.map((d) => ticket(d, maxTitle))));
  }

  if (ready.length) {
    nodes.push(h('h2', {}, `Ready to list (${ready.length})`),
      h('p', { class: 'panel-sub' }, 'Approved but not in the store yet, for example when registering failed. Choose the Naver category and register.'),
      h('div', { class: 'ticket-grid' }, ready.map(listCard)));
  }
  if (listed.length) {
    nodes.push(h('h2', {}, `In the store (${listed.length})`), h('div', { class: 'ticket-grid' }, listed.map(listedCard)));
  }
  paint('approvals', ...nodes);
}

const STORE_CENTRE = 'https://sell.smartstore.naver.com/#/products/origin-list';

// After a card is done, redraw Approvals from the server, if the reviewer is still on it. Other cards keep
// what was typed into them (state.edits) and their "being sent" state (state.sending) across the redraw.
function redrawApprovals(done) {
  if (done) done.classList.add('leaving');
  setTimeout(() => {
    if (currentView() !== 'approvals') { refreshOverview().catch(() => {}); return; }
    approvals().catch((e) => {
      if (done) done.classList.remove('leaving');
      toast(e.message, true, 9000);
    });
  }, 260);
}

// No answer, or a server or tunnel error: the desk may have acted anyway, so show its current state.
const unclearAnswer = (err) => !err.status || err.status >= 500;

// The top line of a card: what it is, the supplier link, and (where allowed) the delete button.
function ticketTop(d, label, canDelete) {
  const extra = d.extra || {};
  return h('div', { class: 'ticket-top' }, h('span', {}, label),
    h('span', { class: 'top-right' },
      extra.url ? h('a', { href: extra.url, target: '_blank', rel: 'noopener' }, `${d.supplier} / ${d.item_id}`) : h('span', {}, `${d.supplier} / ${d.item_id}`),
      canDelete ? deleteButton(d) : null));
}

// Two clicks delete a draft: the first arms the button, the second deletes. It disarms itself after a few seconds.
function deleteButton(d) {
  const button = h('button', { type: 'button', class: 'del', title: 'Delete this draft' });
  let timer = null;
  let armedAt = 0;
  const disarm = () => {
    clearTimeout(timer);
    timer = null;
    button.classList.remove('armed');
    button.replaceChildren(icon('trash'));
    button.setAttribute('aria-label', `Delete draft ${d.id}`);
  };
  button.addEventListener('click', async () => {
    if (!timer) {
      button.classList.add('armed');
      button.replaceChildren(icon('trash'), 'Delete?');
      button.setAttribute('aria-label', `Click again to delete draft ${d.id}`);
      armedAt = Date.now();
      timer = setTimeout(disarm, 4000);
      return;
    }
    if (Date.now() - armedAt < 400) return;   // the second half of a double-click is not a confirmation
    clearTimeout(timer);
    button.disabled = true;
    state.sending.add(d.id);
    try {
      const r = await api(`/api/drafts/${d.id}/delete`, { body: {} });
      state.sending.delete(d.id);
      delete state.edits[d.id];
      toast(r.message);
      redrawApprovals(button.closest('.ticket'));
    } catch (err) {
      state.sending.delete(d.id);
      toast(err.message, true);
      if (!button.isConnected) { redrawApprovals(null); return; }   // the card was redrawn locked meanwhile
      button.disabled = false;
      disarm();
    }
  });
  disarm();
  return button;
}

// Naver category chooser for one draft. Suggestions load one card at a time so the supplier's API is not flooded.
let categoryQueue = Promise.resolve();
function categoryPicker(d, onChange, preset) {
  const select = h('select', { 'aria-label': `Naver category for draft ${d.id}`, disabled: true }, h('option', { value: '' }, 'Finding categories…'));
  const custom = h('input', { type: 'text', inputmode: 'numeric', placeholder: 'Category id, e.g. 50006668', hidden: true, 'aria-label': `Naver category id for draft ${d.id}` });
  const hint = h('p', { class: 'list-hint' });
  let loaded = false;
  let locked = false;
  let touched = false;   // the reviewer chose something; programmatic changes do not fire these events
  const value = () => (select.value === 'other' ? custom.value.trim() : select.value);
  const sync = () => { custom.hidden = select.value !== 'other'; onChange(); };
  const byHand = () => { touched = true; sync(); };
  const load = () => {
    if (!select.isConnected) return null;   // the page was redrawn before this card's turn came
    return api(`/api/drafts/${d.id}/categories`).then((r) => {
      select.replaceChildren(...r.options.map((o, i) => h('option', { value: o.id, selected: i === 0 }, o.name)),
        h('option', { value: 'other' }, 'Another category (type its id)'));
      hint.textContent = r.supplier_category ? `Supplier files it under: ${r.supplier_category}` : '';
    }).catch((err) => {
      select.replaceChildren(h('option', { value: 'other' }, 'Type a category id'));
      hint.textContent = err.message;
    }).finally(() => {
      if (!select.isConnected) { loaded = true; return; }   // this card was replaced meanwhile; its copy owns the edits now
      if (preset) {   // what the reviewer had chosen before the page was redrawn
        const known = [...select.options].some((o) => o.value === preset);
        select.value = known ? preset : 'other';
        if (!known) custom.value = preset;
        touched = true;
      }
      loaded = true;
      select.disabled = custom.disabled = locked;
      sync();
    });
  };
  categoryQueue = categoryQueue.then(load, load);
  select.addEventListener('change', byHand);
  custom.addEventListener('input', byHand);
  return {
    node: h('div', { class: 'list-pick' }, h('label', {}, 'Naver category'), select, custom, hint),
    value,
    loaded: () => loaded,
    touched: () => touched,
    valid: () => /^\d{5,}$/.test(value()),
    lock: (on) => { locked = on; if (loaded) select.disabled = custom.disabled = on; },
  };
}

// A registration that got no clear answer leaves a marker on the draft until Register settles it.
const sendingMarker = (d) => d.status === 'approved' && d.listing && d.listing.state;

function cardHead(d, label) {
  const extra = d.extra || {};
  return [
    ticketTop(d, label, d.status === 'approved' && !sendingMarker(d)),
    h('div', { class: 'ticket-main' },
      extra.thumb ? h('img', { class: 'ticket-pic small', src: extra.thumb, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }) : null,
      h('div', { class: 'ticket-text' },
        h('p', { class: 'list-title' }, d.title),
        h('p', { class: 'source-name' }, `${won(d.price)} KRW · ${d.margin_pct}% margin · approved by ${d.decided_by || 'the team'}`))),
  ];
}

// An approved draft: pick the Naver category, then register it hidden.
function listCard(d) {
  if (!d.listable) {
    return h('article', { class: 'ticket' },
      h('div', { class: 'ticket-body' }, ...cardHead(d, `Draft ${d.id}, approved`),
        h('p', { class: 'list-hint' }, 'This draft came from the candidates file, not a supplier the desk can list from. List it by hand in the store centre, or delete it here.')));
  }
  const marker = sendingMarker(d);
  const button = h('button', { class: 'btn primary', disabled: true }, marker ? 'Check the store and register' : 'Register in store');
  const saved = state.edits[d.id] || {};
  const showNow = h('input', { type: 'checkbox', id: `show-${d.id}` });
  showNow.checked = saved.show ?? true;
  showNow.addEventListener('change', () => { state.edits[d.id] = { ...(state.edits[d.id] || {}), show: showNow.checked }; });
  const note = h('span', { class: 'note' });
  let busy = state.sending.has(d.id);   // still running from before a redraw
  const picker = categoryPicker(d, () => {
    button.disabled = busy || !picker.valid();
    if (picker.touched()) state.edits[d.id] = { ...(state.edits[d.id] || {}), category: picker.value() };
  }, saved.category);
  if (busy) { picker.lock(true); showNow.disabled = true; note.textContent = 'Being registered…'; }

  button.addEventListener('click', async () => {
    busy = button.disabled = showNow.disabled = true;
    state.sending.add(d.id);
    picker.lock(true);
    note.textContent = 'Copying images to Naver and registering. This takes up to a minute.';
    try {
      const r = await api(`/api/drafts/${d.id}/list`, { body: { category_id: picker.value(), show: showNow.checked } });
      state.sending.delete(d.id);
      delete state.edits[d.id];
      toast(r.message);
      redrawApprovals(card);
    } catch (err) {
      state.sending.delete(d.id);
      toast(err.message, true, 9000);
      if (unclearAnswer(err) || !card.isConnected) { redrawApprovals(card); return; }
      note.textContent = '';
      busy = showNow.disabled = false;
      picker.lock(false);
      button.disabled = !picker.valid();
    }
  });

  const card = h('article', { class: 'ticket' },
    h('div', { class: 'ticket-body' }, ...cardHead(d, `Draft ${d.id}, approved`),
      marker ? h('p', { class: 'list-hint warn' },
        `A registration${d.listing.since ? ` at ${when(d.listing.since).slice(0, 16)}` : ''}${d.listing.by ? ` by ${d.listing.by}` : ''} got no clear answer from Naver, ` +
        'so the product may already be in the store. Look in the store centre first. Register searches the store before sending, so it will not create a second copy.') : null,
      d.description ? h('div', { class: 'desc-set' }, h('span', { class: 'desc-label' }, 'Introduction on the product page'), h('p', { class: 'desc-read' }, d.description)) : null,
      picker.node,
      h('label', { class: 'show-now', for: `show-${d.id}` }, showNow, 'Show in the store right away')),
    h('div', { class: 'ticket-stub' }, button, note));
  return card;
}

function listedCard(d) {
  const l = d.listing || {};
  return h('article', { class: 'ticket' },
    h('div', { class: 'ticket-body' }, ...cardHead(d, `Draft ${d.id}, in the store`),
      h('div', { class: 'listed' },
        h('span', { class: 'pill' }, l.display === 'ON' ? 'Registered on display' : 'Registered hidden'),
        // The store centre's product list shows the smartstore (channel) number, so lead with that one.
        h('span', {}, `Store no. ${l.channel_product_no || '–'}`),
        l.origin_product_no ? h('span', { class: 'muted' }, `origin ${l.origin_product_no}`) : null,
        l.listed_at ? h('span', { class: 'muted' }, `registered ${when(l.listed_at)}${l.listed_by ? ` by ${l.listed_by}` : ''}`) : null),
      h('p', { class: 'list-hint' }, l.display === 'ON' ? 'It is on display. Check the page in the store centre.'
        : 'Check the page in the store centre, then switch its display on there.')),
    h('div', { class: 'ticket-stub' }, h('a', { class: 'btn', href: STORE_CENTRE, target: '_blank', rel: 'noopener', style: 'text-decoration:none' }, 'Open the store centre')));
}

async function approvalsReadOnly() {
  const o = await refreshOverview();
  const groups = [['pending', 'Waiting for a decision'], ['approved', 'Approved'], ['published', 'Published to the store'], ['rejected', 'Rejected']];
  const nodes = [head('Approvals', 'The agent drafts each listing; a person approves or rejects it before anything reaches the store.')];
  let total = 0;
  for (const [status, title] of groups) {
    const list = state.snap.drafts[status] || [];
    if (!list.length) continue;
    total += list.length;
    nodes.push(h('h2', {}, `${title} (${list.length})`), h('div', { class: 'ticket-grid' }, list.map((d) => ticket(d, o.rules.max_title))));
  }
  if (!total) nodes.push(h('div', { class: 'empty' }, h('h3', {}, 'No drafts yet'), h('p', {}, 'Drafts appear here once the agent has proposed listings.')));
  paint('approvals', ...nodes);
}

function decisionText(d) {
  if (d.status === 'pending') return 'Waiting for a decision';
  const verb = { approved: 'Approved', rejected: 'Rejected', published: 'Published' }[d.status] || d.status;
  return `${verb} by ${d.decided_by || 'the team'}${d.decided_at ? ` on ${when(d.decided_at)}` : ''}`;
}

function ticket(d, maxTitle) {
  const rules = state.overview.rules;
  const extra = d.extra || {};
  const lowest = d.lowest_price || d.floor;
  const saved = (!state.snap && state.edits[d.id]) || {};   // typed before the page was redrawn
  const input = h('input', { type: 'text', value: saved.title ?? d.title, 'aria-label': `Title of draft ${d.id}`, readonly: Boolean(state.snap) });
  const priceInput = h('input', { type: 'number', value: saved.price ?? d.price, min: lowest, step: '10', inputmode: 'numeric', id: `price-${d.id}` });
  // The introduction shown above the supplier's pictures, written by the agent and checked here before it goes live.
  const description = h('textarea', { id: `desc-${d.id}`, rows: 3, maxlength: 600, value: saved.description ?? d.description ?? '',
    placeholder: 'Three short lines about the product. Leave empty to show only the supplier\'s pictures.' });
  const showNow = h('input', { type: 'checkbox', id: `show-${d.id}` });
  showNow.checked = saved.show ?? true;   // approving shows the product at once unless this is unticked
  const writeIt = h('button', { type: 'button', class: 'btn small' }, 'Write with the agent');
  writeIt.addEventListener('click', async () => {
    lock(true);   // nothing is approved or rejected while the agent is writing
    writeIt.textContent = 'Writing…';
    try {
      const r = await api(`/api/drafts/${d.id}/describe`, { body: {} });
      d.description = r.description;   // the desk keeps it on the draft now
      description.value = r.description;
      sync();
      toast(`Written by the agent. Check every line before approving. (${won(r.tokens_used)} of ${won(r.tokens_cap)} tokens used this week)`, false, 6000);
      refreshOverview().catch(() => {});
    } catch (err) { toast(err.message, true, 9000); }
    writeIt.textContent = 'Write with the agent';
    if (node.isConnected) { lock(false); sync(); } else redrawApprovals(null);
  });
  const chars = h('span', { class: 'chars' });
  const approve = h('button', { class: 'btn primary' }, 'Approve and list');
  const reject = h('button', { class: 'btn danger' }, 'Reject');
  const note = h('span', { class: 'note' });
  const figures = h('div', {});
  let busy = !state.snap && state.sending.has(d.id);   // its request is still running from before a redraw
  const picker = state.snap || !d.listable ? null : categoryPicker(d, () => sync(), saved.category);
  const verb = () => (!picker ? 'approve' : showNow.checked ? 'approve and list' : 'approve and list hidden');

  const drawFigures = (price) => {
    const profit = Math.round(price * (1 - rules.fee_rate) - d.cost);
    figures.replaceChildren(
      ruler({ cost: d.cost, floor: d.floor, price }),
      h('div', { class: 'money' },
        h('div', {}, h('span', {}, 'Profit per sale'), h('strong', {}, `${won(profit)} KRW`)),
        h('div', {}, h('span', {}, 'Margin'), h('strong', {}, `${price ? ((profit / price) * 100).toFixed(1) : '0.0'}%`)),
        h('div', {}, h('span', {}, price < d.floor ? 'Below the floor by' : 'Above the floor by'), h('strong', {}, `${won(Math.abs(price - d.floor))} KRW`))));
  };

  const sync = () => {
    const length = input.value.trim().length;
    const titleEdited = input.value.trim() !== d.title;
    const price = Math.floor(Number(priceInput.value) || 0);
    const priceEdited = price !== d.price;
    chars.textContent = `${length} of ${maxTitle}`;
    chars.classList.toggle('over', length > maxTitle);
    drawFigures(price);
    // Until the picker has loaded, keep the category saved before the redraw; after that, only a choice made by hand.
    const category = !picker ? undefined : !picker.loaded() ? saved.category : picker.touched() ? picker.value() : undefined;
    const descEdited = description.value.trim() !== (d.description || '');
    if (titleEdited || priceEdited || descEdited || !showNow.checked || category !== undefined) {
      state.edits[d.id] = { title: input.value, price: priceInput.value, description: description.value, show: showNow.checked, category };
    } else delete state.edits[d.id];
    if (busy) return;
    const noCategory = picker && !picker.valid();
    const v = verb();
    approve.textContent = titleEdited || priceEdited || descEdited ? `Save changes, ${v}` : v[0].toUpperCase() + v.slice(1);
    approve.disabled = length === 0 || length > maxTitle || (d.blocked && !titleEdited) || price < lowest || noCategory;
    note.textContent = price < lowest ? `The price must be at least ${won(lowest)} KRW.`
      : d.blocked && !titleEdited ? 'Edit the title to remove the blocked wording, or reject.'
      : noCategory ? (picker.loaded() ? 'Choose a Naver category to approve.' : 'Finding the Naver category…') : '';
  };
  input.addEventListener('input', sync);
  priceInput.addEventListener('input', sync);
  description.addEventListener('input', sync);
  showNow.addEventListener('change', sync);

  const node = h('article', { class: 'ticket' },
    h('div', { class: 'ticket-body' },
      ticketTop(d, `Draft ${d.id}, ${d.source === 'llm' ? 'picked by the agent' : 'built from keywords'}`, !state.snap),
      h('div', { class: 'ticket-main' },
        extra.thumb ? h('img', { class: 'ticket-pic', src: extra.thumb, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }) : null,
        h('div', { class: 'ticket-text' },
          h('div', { class: 'title-row' }, input, chars),
          h('p', { class: 'source-name' }, `Supplier calls it: ${d.name}`),
          d.reason ? h('p', { class: 'reason' }, h('strong', {}, d.source === 'llm' ? 'Why the agent picked it: ' : 'Why it was picked: '), d.reason) : null,
          d.tags.length ? h('ul', { class: 'tags', 'aria-label': 'Tags' }, d.tags.map((t) => h('li', {}, t))) : null)),
      state.snap ? null : h('div', { class: 'price-set' },
        h('label', { for: `price-${d.id}` }, 'Price you set (KRW)'), priceInput,
        h('small', {}, `Lowest allowed ${won(lowest)}` + (lowest > d.floor ? ', the supplier\'s resale minimum.' : ', the price floor.'))),
      figures,
      d.flags.length ? h('ul', { class: 'flags' }, d.flags.map((f) =>
        h('li', { class: /^(banned word|title longer)/.test(f) ? 'hard' : '' }, f))) : null,
      (extra.notes || []).length ? h('ul', { class: 'notes' }, extra.notes.map((n) => h('li', {}, n))) : null,
      state.snap
        ? (d.description ? h('div', { class: 'desc-set' }, h('span', { class: 'desc-label' }, 'Introduction on the product page'), h('p', { class: 'desc-read' }, d.description)) : null)
        : (picker ? h('div', { class: 'desc-set' },
            h('div', { class: 'desc-head' }, h('label', { for: `desc-${d.id}` }, 'Introduction on the product page'), writeIt), description,
            h('small', {}, 'Shown above the supplier\'s pictures. Check that every line is true of this product.')) : null),
      picker ? picker.node : null,
      picker ? h('label', { class: 'show-now', for: `show-${d.id}` }, showNow, 'Show in the store right away') : null,
      !state.snap && !d.listable ? h('p', { class: 'list-hint' }, 'This draft came from the candidates file, not a supplier the desk can list from, so approving does not register it.') : null),
    state.snap
      ? h('div', { class: 'ticket-stub' }, h('span', { class: `decision ${d.status}` }, decisionText(d)))
      : h('div', { class: 'ticket-stub' }, approve, reject, note));
  if (state.snap) {
    chars.textContent = `${d.title.length} of ${maxTitle}`;
    drawFigures(d.price);
    return node;
  }

  const lock = (on) => {
    busy = input.readOnly = priceInput.readOnly = description.readOnly = on;
    approve.disabled = reject.disabled = showNow.disabled = writeIt.disabled = on;
    if (picker) picker.lock(on);
  };
  const send = async (action) => {
    lock(true);
    state.sending.add(d.id);
    if (action === 'approve' && picker) note.textContent = 'Approving, then copying the images to Naver and registering. This takes up to a minute.';
    try {
      const body = action === 'approve'
        ? { title: input.value.trim(), price: Math.floor(Number(priceInput.value) || 0), category_id: picker ? picker.value() : '',
            description: description.value.trim(), show: showNow.checked } : {};
      const result = await api(`/api/drafts/${d.id}/${action}`, { body });
      state.sending.delete(d.id);
      const failed = action === 'approve' && picker && !result.listed;   // approved, but the store did not take it
      if (failed) state.edits[d.id] = { show: showNow.checked, category: picker.value() };   // for the Ready-to-list card
      else delete state.edits[d.id];
      toast(result.message, failed, failed ? 12000 : 3200);
      redrawApprovals(node);   // a listed draft moves to "In the store", a failed one to "Ready to list"
    } catch (err) {
      state.sending.delete(d.id);
      toast(err.message, true, 9000);
      // No clear answer (the tunnel gave up while the desk kept working), or the page was redrawn meanwhile: show the desk's state.
      if (unclearAnswer(err) || !node.isConnected) { redrawApprovals(node); return; }
      lock(false);
      sync();
    }
  };
  approve.addEventListener('click', () => send('approve'));
  reject.addEventListener('click', () => send('reject'));
  sync();
  if (busy) { lock(true); note.textContent = 'Being sent…'; }
  return node;
}

// ---------------------------------------------------------------- Sourcing
const MARKET = { dome: 'Domeggook', supply: 'Domeme' };

function runBlock(run) {
  const picked = run.items.filter((i) => i.outcome === 'picked').length;
  const dropped = run.items.filter((i) => i.outcome === 'dropped').length;
  const word = { picked: 'Picked', passed: 'Passed over', dropped: 'Dropped' };
  return h('section', { class: 'panel run' },
    h('h2', {}, `"${run.keyword}" on ${MARKET[run.market] || run.market}`),
    h('p', { class: 'panel-sub' },
      `${when(run.ts).slice(0, 16)}. ${won(run.total)} items on the supplier's site, ${run.looked} looked at, ` +
      `${dropped} dropped by the rules, ${picked} picked ${run.used_llm ? 'by the agent' : 'by lowest cost, without the LLM'}.`),
    h('ul', { class: 'found' }, run.items.map((i) => h('li', { class: i.outcome },
      i.thumb ? h('img', { src: i.thumb, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }) : h('span', { class: 'no-pic' }),
      h('div', {},
        i.url ? h('a', { href: i.url, target: '_blank', rel: 'noopener' }, i.name) : h('span', {}, i.name),
        h('p', {}, `Cost ${won(i.cost)}` + (i.shipping ? ` + ${won(i.shipping)} shipping` : ', free shipping') +
          `, floor ${won(i.floor)} KRW` + (i.moq > 1 ? `, minimum order ${i.moq}` : '')),
        h('p', { class: 'why' }, h('strong', {}, `${word[i.outcome] || i.outcome}: `), i.why || ''))))));
}

async function sourcing() {
  const [runs] = await Promise.all([api('/api/sourcing'), refreshOverview()]);
  const lead = state.snap
    ? 'What the agent looked at on the supplier\'s site, what the rules dropped, and what it picked.'
    : 'Type what to look for. The rules drop what we cannot sell, the agent picks from the rest, and its picks wait in Approvals for your price.';
  const nodes = [head('Sourcing', lead)];

  if (!state.snap) {
    const keyword = h('input', { type: 'text', id: 'kw', placeholder: '강아지 발톱깎이', autocomplete: 'off' });
    const market = h('select', { id: 'market' }, h('option', { value: 'dome' }, 'Domeggook (bulk)'), h('option', { value: 'supply' }, 'Domeme (single units)'));
    const picks = h('select', { id: 'picks' }, [1, 2, 3, 4, 5].map((n) => h('option', { value: n, selected: n === 3 }, n)));
    const moq = h('select', { id: 'moq' }, [[1, '1 unit only'], [3, 'Up to 3'], [10, 'Up to 10']].map(([n, label]) => h('option', { value: n, selected: n === 1 }, label)));
    const status = h('p', { class: 'form-note', 'aria-live': 'polite' });
    const go = async (useLlm, button) => {
      if (!keyword.value.trim()) { keyword.focus(); return; }
      const buttons = form.querySelectorAll('button');
      buttons.forEach((b) => { b.disabled = true; });
      status.textContent = 'Searching the supplier and checking each item. This takes a few seconds.';
      try {
        const r = await api('/api/source', { body: { keyword: keyword.value, market: market.value, picks: Number(picks.value), look: 20, max_moq: Number(moq.value), use_llm: useLlm } });
        const problem = r.events.find((e) => e.kind === 'error');
        toast(problem ? problem.message : `${plural(r.created, 'draft', 'drafts')} sent to Approvals`, Boolean(problem));
        state.lastSourcing = r.events;
        await sourcing();
      } catch (err) { toast(err.message, true); buttons.forEach((b) => { b.disabled = false; }); status.textContent = ''; }
    };
    const form = h('form', { class: 'source-form', onsubmit: (e) => { e.preventDefault(); go(true); } },
      h('div', { class: 'field grow' }, h('label', { for: 'kw' }, 'What to look for'), keyword),
      h('div', { class: 'field' }, h('label', { for: 'market' }, 'Where'), market),
      h('div', { class: 'field' }, h('label', { for: 'moq' }, 'Minimum order'), moq),
      h('div', { class: 'field' }, h('label', { for: 'picks' }, 'Picks'), picks),
      h('div', { class: 'source-go' },
        h('button', { class: 'btn primary', type: 'submit' }, 'Ask the agent to pick'),
        h('button', { class: 'btn', type: 'button', onclick: () => go(false) }, 'Pick the cheapest, no tokens')));
    nodes.push(form, status);
    if (state.lastSourcing && state.lastSourcing.length) {
      nodes.push(h('ul', { class: 'events' }, state.lastSourcing.map((e) => h('li', {}, e.message))));
    }
    state.lastSourcing = null;
  }

  if (runs.length) nodes.push(...runs.map(runBlock));
  else nodes.push(h('div', { class: 'empty' }, h('h3', {}, 'Nothing has been searched yet'),
    h('p', {}, state.snap ? 'Searches appear here after the team runs one.' : 'Start with a product you would buy yourself this week. Searching and rule checks use no tokens; only the agent\'s pick does.')));
  paint('sourcing', ...nodes);
}

// ---------------------------------------------------------------- Store
async function store() {
  const o = await refreshOverview();
  const s = o.store;
  if (s.error) {
    paint('store', head('Store'),
      h('p', { class: 'notice' }, s.error),
      h('p', { class: 'muted' }, 'Check the Naver keys in backend/.env and that this machine\'s IP is registered in the Commerce API centre.'));
    return;
  }
  const filters = ['SALE', ...Object.keys(s.count_by_status).filter((k) => k !== 'SALE'), 'ALL'];
  if (!filters.includes(state.storeFilter)) state.storeFilter = 'ALL';
  const rows = s.products.filter((p) => state.storeFilter === 'ALL' || p.status === state.storeFilter);

  paint('store', 
    head('Store', `Read from Naver at ${when(s.fetched_at)}. This page only reads; it changes nothing in the store.`),
    h('div', { class: 'chips' }, filters.map((f) => h('button', {
      class: 'chip', 'aria-pressed': String(state.storeFilter === f),
      onclick: () => { state.storeFilter = f; store(); },
    }, f === 'ALL' ? `All (${s.products.length})` : `${statusName(f)} (${s.count_by_status[f] || 0})`))),
    rows.length
      ? h('div', { class: 'table-wrap' }, h('table', {},
          h('thead', {}, h('tr', {}, h('th', {}, 'Product'), h('th', {}, 'Status'), h('th', { class: 'r' }, 'Price'), h('th', { class: 'r' }, 'Stock'), h('th', { class: 'r' }, 'Product no.'))),
          h('tbody', {}, rows.map((p) => h('tr', {},
            h('td', {}, p.name), h('td', {}, h('span', { class: `pill ${p.status}` }, statusName(p.status))),
            h('td', { class: 'r' }, won(p.price)), h('td', { class: 'r' }, won(p.stock)), h('td', { class: 'r' }, p.no))))))
      : h('div', { class: 'empty' }, h('h3', {}, 'No products with this status'), h('p', {}, 'Choose another filter above.')));
}

// ---------------------------------------------------------------- Price check
async function price() {
  if (!state.overview) await refreshOverview();
  const rules = state.overview.rules;
  const result = h('div', {}, h('div', { class: 'empty' }, h('h3', {}, 'Enter a supplier cost'), h('p', {}, 'The floor is the lowest price that still leaves the minimum margin after fees.')));
  const field = (id, label, hint, placeholder) => h('div', { class: 'field' },
    h('label', { for: id }, label),
    h('input', { type: 'number', id, min: '0', step: '10', inputmode: 'numeric', placeholder }),
    hint && h('small', {}, hint));
  const form = h('form', { class: 'price-form', onsubmit: (e) => e.preventDefault() },
    field('cost', 'Supplier cost (KRW)', null, '9800'),
    field('shipping', 'Shipping we pay (KRW)', 'Leave empty if the buyer pays shipping.', '3000'),
    field('ad', 'Ad cost per sale (KRW)', 'Cost per click divided by conversion rate.', '0'),
    field('target', 'Price we want to charge (KRW)', 'Leave empty to see only the floor.', '17900'));

  let timer;
  const update = async () => {
    const q = new URLSearchParams();
    for (const id of ['cost', 'shipping', 'ad', 'target']) q.set(id, form.querySelector('#' + id).value);
    if (!q.get('cost')) return;
    try {
      const r = await api('/api/price?' + q);
      const verdict = !r.has_target ? `The floor is ${won(r.floor)} KRW.`
        : r.clears ? `Clears the floor by ${won(r.gap)} KRW.` : `Below the floor by ${won(-r.gap)} KRW.`;
      result.replaceChildren(
        h('p', { class: 'verdict' + (r.clears ? '' : ' no') }, verdict),
        ruler({ cost: r.landed_cost + r.ad, floor: r.floor, price: r.has_target ? r.price : 0, big: true }),
        h('div', { class: 'breakdown' },
          h('div', {}, h('span', {}, r.has_target ? 'Price' : 'Price at the floor'), h('span', { class: 'num' }, won(r.price))),
          h('div', {}, h('span', {}, 'Cost and shipping'), h('span', { class: 'num' }, '− ' + won(r.landed_cost))),
          h('div', {}, h('span', {}, `Fees (${(rules.fee_rate * 100).toFixed(2)}%)`), h('span', { class: 'num' }, '− ' + won(r.fee))),
          r.ad ? h('div', {}, h('span', {}, 'Ads'), h('span', { class: 'num' }, '− ' + won(r.ad))) : null,
          h('div', {}, h('span', {}, `Profit per sale (${r.margin_pct}% margin)`), h('span', { class: 'num' }, won(r.profit)))));
    } catch (err) { result.replaceChildren(h('p', { class: 'notice' }, err.message)); }
  };
  form.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(update, 250); });

  paint('price', 
    head('Price check', `Minimum margin ${Math.round(rules.min_margin * 100)}%, fees ${(rules.fee_rate * 100).toFixed(2)}%. No tokens are used here.`),
    h('div', { class: 'price-grid' },
      h('section', { class: 'panel' }, h('h2', { style: 'margin-bottom:var(--s4)' }, icon('coins'), 'Costs'), form),
      h('section', { class: 'panel' }, h('h2', { style: 'margin-bottom:var(--s3)' }, icon('tag'), 'Result'), result)));
}

// ---------------------------------------------------------------- Ask
async function ask() {
  if (!state.overview) await refreshOverview();
  if (state.snap) {
    const chats = state.snap.chats || [];
    paint('ask', 
      head('Ask the agent', 'Questions the team asked and what the agent answered. It reads the store and the drafts; it cannot change anything.'),
      chats.length
        ? h('section', { class: 'panel', style: 'margin-top:0' }, h('div', { class: 'chat' }, chats.flatMap((c) => [h('div', { class: 'q' }, c.question), h('div', { class: 'a' }, c.answer), h('div', { class: 'chat-when' }, when(c.ts))])))
        : h('div', { class: 'empty' }, h('h3', {}, 'No questions recorded yet'), h('p', {}, 'Answers appear here after the team asks the agent something.')));
    return;
  }
  const log = h('div', { class: 'chat', 'aria-live': 'polite' });
  const draw = () => log.replaceChildren(...(state.chat.length
    ? state.chat.map((m) => h('div', { class: m.role }, m.text))
    : [h('div', { class: 'chat-empty' }, icon('bot'), h('p', {}, 'Ask about products, drafts, prices or margins. Try one of the suggestions on the right.'))]));
  const input = h('input', { type: 'text', placeholder: 'Ask about products, drafts, prices or margins', 'aria-label': 'Question', autocomplete: 'off' });
  const button = h('button', { class: 'btn primary' }, 'Ask');

  const send = async (text) => {
    const question = (text || input.value).trim();
    if (!question) return;
    input.value = '';
    button.disabled = true;
    state.chat.push({ role: 'q', text: question }, { role: 'a', text: 'Reading the store and drafts…' });
    draw();
    try {
      const r = await api('/api/ask', { body: { question } });
      state.chat[state.chat.length - 1].text = r.answer;
    } catch (err) { state.chat[state.chat.length - 1].text = err.message; }
    draw();
    button.disabled = false;
    input.focus();
    refreshOverview().catch(() => {});
  };
  const ideas = ['What is on sale right now?', 'Which draft has the lowest margin?', 'What should I check before approving?'];

  paint('ask', 
    head('Ask the agent', 'It reads the store and the drafts, then answers. It cannot change anything. Each question spends roughly 1,000 tokens.'),
    h('div', { class: 'ask-grid' },
      h('section', { class: 'panel chat-panel' },
        log,
        h('form', { class: 'ask-form', onsubmit: (e) => { e.preventDefault(); send(); } }, input, button)),
      h('div', {},
        h('section', { class: 'panel' },
          h('h2', { style: 'margin-bottom:var(--s3)' }, icon('bot'), 'Try asking'),
          h('div', { class: 'suggest' }, ideas.map((q) => h('button', { class: 'chip', type: 'button', onclick: () => send(q) }, q)))),
        h('section', { class: 'panel tight' },
          h('h2', {}, icon('eye'), 'What it can see'),
          h('ul', { class: 'side-list' },
            ['Products in the store, with prices and status', 'Drafts waiting, approved and rejected', 'Price floors and margins'].map((t) => h('li', {}, icon('check'), t))),
          h('p', { class: 'panel-rules' }, `This week: ${won(state.overview.tokens.used)} of ${won(state.overview.tokens.cap)} tokens used.`)))));
  draw();
  if (state.pendingQuestion) {
    const q = state.pendingQuestion;
    state.pendingQuestion = null;
    send(q);
  } else {
    input.focus();
  }
}

// ---------------------------------------------------------------- Activity
async function activity() {
  const [rows] = await Promise.all([api('/api/activity?limit=100'), refreshOverview()]);
  paint('activity', 
    head('Activity', 'Every decision and every agent run, newest first.'),
    rows.length
      ? h('div', { class: 'table-wrap' }, h('table', {},
          h('thead', {}, h('tr', {}, h('th', {}, 'When'), h('th', {}, 'What'), h('th', {}, 'Detail'))),
          h('tbody', {}, rows.map((r) => h('tr', {},
            h('td', { class: 'num', style: 'white-space:nowrap' }, r.ts.replace('T', ' ')),
            h('td', {}, ACTIONS[r.action] || r.action), h('td', {}, r.detail))))))
      : h('div', { class: 'empty' }, h('h3', {}, 'Nothing has happened yet'), h('p', {}, 'Decisions and agent runs will be listed here.')));
}

// ---------------------------------------------------------------- router
const views = { today, sourcing, approvals, store, price, ask, activity };
const TITLES = { today: 'Dashboard', sourcing: 'Sourcing', approvals: 'Approvals', store: 'Store', price: 'Price check', ask: 'Ask the agent', activity: 'Activity' };

function setMenu(open) {
  document.body.classList.toggle('nav-open', open);
  document.getElementById('scrim').hidden = !open;
  document.getElementById('menu-btn').setAttribute('aria-expanded', String(open));
}

async function route() {
  const asked = location.hash.replace('#/', '') || 'today';
  const name = views[asked] ? asked : 'today';
  const render = views[name];
  document.querySelectorAll('.nav a').forEach((a) => {
    if (a.dataset.view === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  document.getElementById('crumb').textContent = TITLES[name];
  document.title = `${TITLES[name]} · Agentic desk`;
  setMenu(false);
  window.scrollTo(0, 0);
  if (PUBLIC && !state.snap) {
    view.replaceChildren(head('Agentic desk'), h('div', { class: 'empty' }, h('h3', {}, 'No data has been published yet'),
      h('p', {}, 'The team publishes the desk\'s data from its own machine. Please check back shortly.')));
    return;
  }
  try {
    await render();
  } catch (err) {
    paint(name, head('Something went wrong'), h('p', { class: 'notice' }, err.message));
  }
}

// ---------------------------------------------------------------- shell
if (PUBLIC) {
  document.body.classList.add('snapshot');
  const mode = document.getElementById('mode');
  mode.classList.add('read');
  document.getElementById('mode-text').textContent = state.snap ? `Read-only · ${when(state.snap.exported_at)}` : 'Read-only';
  mode.title = "Approvals and the agent run on the team's own desk.";
  document.getElementById('quick-ask').hidden = true;
}

document.getElementById('menu-btn').addEventListener('click', () => setMenu(!document.body.classList.contains('nav-open')));
document.getElementById('scrim').addEventListener('click', () => setMenu(false));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setMenu(false); });

// The question box in the top bar opens Ask the agent and sends the question there.
document.getElementById('quick-ask').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = document.getElementById('quick-ask-input');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';
  state.pendingQuestion = question;
  if (location.hash === '#/ask') route();
  else location.hash = '#/ask';
});

window.addEventListener('hashchange', route);
route();
