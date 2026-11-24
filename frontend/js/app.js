/* Agentic desk: plain JavaScript, no build step. Talks to the backend under /api.
   On github.io there is no backend, so the page reads js/snapshot.js (written by backend/publish.py)
   and every action is switched off. */

const view = document.getElementById('view');
const PUBLIC = location.hostname.endsWith('github.io') || location.protocol === 'file:';
const state = { overview: null, chat: [], storeFilter: 'SALE', lastEvents: null, snap: PUBLIC ? (window.SNAPSHOT || null) : null };

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

const won = (n) => Number(n).toLocaleString('en-US');
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const STATUS = { SALE: 'On sale', SUSPENSION: 'Suspended', OUTOFSTOCK: 'Out of stock', WAIT: 'Waiting', CLOSE: 'Closed' };
const statusName = (s) => STATUS[s] || s;

async function api(path, options = {}) {
  if (PUBLIC) return snapshotApi(path, options);
  const init = { method: options.method || 'GET' };
  if (options.body) {
    init.method = 'POST';
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    throw new Error('The backend is not answering. Is "python server.py" still running?');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
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
function toast(message, isError = false) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = 'toast'; }, 3200);
}

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
  const badge = document.getElementById('nav-pending');
  badge.hidden = o.drafts.pending === 0;
  badge.textContent = o.drafts.pending;
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
    h('h2', {}, 'Orders per day'),
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
    h('h2', {}, 'Margin watch'),
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
  const labelActions = { draft_approved: 'Draft approved', draft_rejected: 'Draft rejected', drafts_generated: 'Drafts created', gate0_reject: 'Stopped at the price floor', chat: 'Question asked', sourcing_run: 'Products searched' };

  view.replaceChildren(
    h('section', { class: 'board' },
      h('div', { class: 'board-main' },
        ring(count, goal),
        h('div', { class: 'board-copy' },
          h('p', { class: 'lede' }, `${plural(left, 'day', 'days')} left to reach ${goal} orders.`),
          h('p', { class: 'lede-sub' }, pace),
          h('p', { class: 'lede-sub' }, waiting, ' ',
            d.pending > 0 ? h('a', { href: '#/approvals' }, state.snap ? 'See the drafts' : 'Review drafts') : ''),
          h('p', { class: 'board-foot' }, `Goal date ${longDate(o.mission.deadline)}.` + (o.orders.since ? ` Orders counted since ${longDate(o.orders.since)}.` : '')))),
      belt(d)),

    h('div', { class: 'fields' },
      h('div', {}, h('span', {}, 'Products on sale'), h('strong', {}, shop ? sale.length : '–'),
        h('small', {}, shop ? `${shop.products.length - sale.length} not on sale` : 'store not read')),
      h('div', { class: under ? 'alarm' : '' }, h('span', {}, 'Priced below the floor'), h('strong', {}, shop ? under : '–'),
        h('small', {}, shop ? `of ${priced.length} with a recorded cost` : 'store not read')),
      h('div', {}, h('span', {}, 'Drafts waiting'), h('strong', {}, d.pending), h('small', {}, `${d.approved} approved, ${d.rejected} rejected`)),
      h('div', {}, h('span', {}, 'Tokens this week'), h('strong', {}, won(o.tokens.used)), h('small', {}, `of ${won(o.tokens.cap)}`))),

    h('div', { class: 'cols' },
      ordersChart(o.orders),
      h('section', { class: 'panel' },
        h('h2', {}, 'Waiting for approval'),
        pending.length
          ? h('ul', { class: 'queue' }, pending.slice(0, 4).map((x) => h('li', {},
              h('a', { href: '#/approvals' }, x.title),
              h('span', {}, `${won(x.price)} KRW, ${x.margin_pct}% margin`))))
          : h('p', { class: 'panel-sub' }, 'The queue is empty.'))),

    (!o.store.error && marginWatch(o.store.products)) || '',

    h('div', { class: 'cols' },
      h('section', { class: 'panel' },
        h('h2', {}, 'Recent activity'),
        log.length
          ? h('ul', { class: 'mini-log' }, log.slice(0, 6).map((r) => h('li', {},
              h('span', {}, when(r.ts).slice(5, 16)), h('strong', {}, labelActions[r.action] || r.action), h('em', {}, r.detail))))
          : h('p', { class: 'panel-sub' }, 'Nothing has happened yet.')),
      h('section', { class: 'panel' },
        h('h2', {}, 'Store'),
        storeBar(o.store),
        h('p', { class: 'panel-rules' },
          `Every price must leave a ${Math.round(o.rules.min_margin * 100)}% margin after ${(o.rules.fee_rate * 100).toFixed(2)}% in fees. ` +
          `Titles stay within ${o.rules.max_title} characters. The agent drafts and answers; ${o.reviewer} approves.`))),
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
  const [list] = await Promise.all([api('/api/drafts?status=pending'), refreshOverview()]);
  const maxTitle = state.overview.rules.max_title;

  const nodes = [head('Approvals',
    'Set the price, fix the title if needed, then approve or reject. Nothing is published to the store from this page.')];

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
    nodes.push(...list.map((d) => ticket(d, maxTitle)));
  }
  view.replaceChildren(...nodes);
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
    nodes.push(h('h2', {}, `${title} (${list.length})`), ...list.map((d) => ticket(d, o.rules.max_title)));
  }
  if (!total) nodes.push(h('div', { class: 'empty' }, h('h3', {}, 'No drafts yet'), h('p', {}, 'Drafts appear here once the agent has proposed listings.')));
  view.replaceChildren(...nodes);
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
  const input = h('input', { type: 'text', value: d.title, 'aria-label': `Title of draft ${d.id}`, readonly: Boolean(state.snap) });
  const priceInput = h('input', { type: 'number', value: d.price, min: lowest, step: '10', inputmode: 'numeric', id: `price-${d.id}` });
  const chars = h('span', { class: 'chars' });
  const approve = h('button', { class: 'btn primary' }, 'Approve');
  const reject = h('button', { class: 'btn danger' }, 'Reject');
  const note = h('span', { class: 'note' });
  const figures = h('div', {});

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
    approve.textContent = titleEdited || priceEdited ? 'Save changes and approve' : 'Approve';
    approve.disabled = length === 0 || length > maxTitle || (d.blocked && !titleEdited) || price < lowest;
    note.textContent = price < lowest ? `The price must be at least ${won(lowest)} KRW.`
      : d.blocked && !titleEdited ? 'Edit the title to remove the blocked wording, or reject.' : '';
  };
  input.addEventListener('input', sync);
  priceInput.addEventListener('input', sync);

  const node = h('article', { class: 'ticket' },
    h('div', { class: 'ticket-body' },
      h('div', { class: 'ticket-top' },
        h('span', {}, `Draft ${d.id}, ${d.source === 'llm' ? 'written by the agent' : 'built from keywords'}`),
        extra.url ? h('a', { href: extra.url, target: '_blank', rel: 'noopener' }, `${d.supplier} / ${d.item_id}`) : h('span', {}, `${d.supplier} / ${d.item_id}`)),
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
      (extra.notes || []).length ? h('ul', { class: 'notes' }, extra.notes.map((n) => h('li', {}, n))) : null),
    state.snap
      ? h('div', { class: 'ticket-stub' }, h('span', { class: `decision ${d.status}` }, decisionText(d)))
      : h('div', { class: 'ticket-stub' }, approve, reject, note));
  if (state.snap) {
    chars.textContent = `${d.title.length} of ${maxTitle}`;
    drawFigures(d.price);
    return node;
  }

  const send = async (action) => {
    approve.disabled = reject.disabled = true;
    try {
      const body = action === 'approve' ? { title: input.value.trim(), price: Math.floor(Number(priceInput.value) || 0) } : {};
      const result = await api(`/api/drafts/${d.id}/${action}`, { body });
      toast(result.message);
      node.classList.add('leaving');
      setTimeout(async () => {
        node.remove();
        await refreshOverview();
        if (!view.querySelector('.ticket')) approvals();
      }, 260);
    } catch (err) { toast(err.message, true); reject.disabled = false; sync(); }
  };
  approve.addEventListener('click', () => send('approve'));
  reject.addEventListener('click', () => send('reject'));
  sync();
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
    const status = h('p', { class: 'form-note', 'aria-live': 'polite' });
    const go = async (useLlm, button) => {
      if (!keyword.value.trim()) { keyword.focus(); return; }
      const buttons = form.querySelectorAll('button');
      buttons.forEach((b) => { b.disabled = true; });
      status.textContent = 'Searching the supplier and checking each item. This takes a few seconds.';
      try {
        const r = await api('/api/source', { body: { keyword: keyword.value, market: market.value, picks: Number(picks.value), look: 10, use_llm: useLlm } });
        const problem = r.events.find((e) => e.kind === 'error');
        toast(problem ? problem.message : `${plural(r.created, 'draft', 'drafts')} sent to Approvals`, Boolean(problem));
        state.lastSourcing = r.events;
        await sourcing();
      } catch (err) { toast(err.message, true); buttons.forEach((b) => { b.disabled = false; }); status.textContent = ''; }
    };
    const form = h('form', { class: 'source-form', onsubmit: (e) => { e.preventDefault(); go(true); } },
      h('div', { class: 'field grow' }, h('label', { for: 'kw' }, 'What to look for'), keyword),
      h('div', { class: 'field' }, h('label', { for: 'market' }, 'Where'), market),
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
  view.replaceChildren(...nodes);
}

// ---------------------------------------------------------------- Store
async function store() {
  const o = await refreshOverview();
  const s = o.store;
  if (s.error) {
    view.replaceChildren(head('Store'),
      h('p', { class: 'notice' }, s.error),
      h('p', { class: 'muted' }, 'Check the Naver keys in backend/.env and that this machine\'s IP is registered in the Commerce API centre.'));
    return;
  }
  const filters = ['SALE', ...Object.keys(s.count_by_status).filter((k) => k !== 'SALE'), 'ALL'];
  if (!filters.includes(state.storeFilter)) state.storeFilter = 'ALL';
  const rows = s.products.filter((p) => state.storeFilter === 'ALL' || p.status === state.storeFilter);

  view.replaceChildren(
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

  view.replaceChildren(
    head('Price check', `Minimum margin ${Math.round(rules.min_margin * 100)}%, fees ${(rules.fee_rate * 100).toFixed(2)}%. No tokens are used here.`),
    h('div', { class: 'price-grid' }, form, result));
}

// ---------------------------------------------------------------- Ask
async function ask() {
  if (!state.overview) await refreshOverview();
  if (state.snap) {
    const chats = state.snap.chats || [];
    view.replaceChildren(
      head('Ask the agent', 'Questions the team asked and what the agent answered. It reads the store and the drafts; it cannot change anything.'),
      chats.length
        ? h('div', { class: 'chat' }, chats.flatMap((c) => [h('div', { class: 'q' }, c.question), h('div', { class: 'a' }, c.answer), h('div', { class: 'chat-when' }, when(c.ts))]))
        : h('div', { class: 'empty' }, h('h3', {}, 'No questions recorded yet'), h('p', {}, 'Answers appear here after the team asks the agent something.')));
    return;
  }
  const log = h('div', { class: 'chat', 'aria-live': 'polite' });
  const draw = () => log.replaceChildren(...state.chat.map((m) => h('div', { class: m.role }, m.text)));
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

  view.replaceChildren(
    head('Ask the agent', 'It reads the store and the drafts, then answers. It cannot change anything. Each question spends roughly 1,000 tokens.'),
    state.chat.length ? null : h('div', { class: 'suggest' }, ideas.map((q) => h('button', { class: 'chip', onclick: () => send(q) }, q))),
    log,
    h('form', { class: 'ask-form', onsubmit: (e) => { e.preventDefault(); send(); } }, input, button));
  draw();
  input.focus();
}

// ---------------------------------------------------------------- Activity
async function activity() {
  const rows = await api('/api/activity?limit=100');
  const label = { draft_approved: 'Draft approved', draft_rejected: 'Draft rejected', drafts_generated: 'Drafts created',
    gate0_reject: 'Stopped at the price floor', chat: 'Question asked', sourcing_run: 'Products searched' };
  view.replaceChildren(
    head('Activity', 'Every decision and every agent run, newest first.'),
    rows.length
      ? h('div', { class: 'table-wrap' }, h('table', {},
          h('thead', {}, h('tr', {}, h('th', {}, 'When'), h('th', {}, 'What'), h('th', {}, 'Detail'))),
          h('tbody', {}, rows.map((r) => h('tr', {},
            h('td', { class: 'num', style: 'white-space:nowrap' }, r.ts.replace('T', ' ')),
            h('td', {}, label[r.action] || r.action), h('td', {}, r.detail))))))
      : h('div', { class: 'empty' }, h('h3', {}, 'Nothing has happened yet'), h('p', {}, 'Decisions and agent runs will be listed here.')));
}

// ---------------------------------------------------------------- router
const views = { today, sourcing, approvals, store, price, ask, activity };

async function route() {
  const name = (location.hash.replace('#/', '') || 'today');
  const render = views[name] || today;
  document.querySelectorAll('.nav a').forEach((a) => {
    if (a.dataset.view === (views[name] ? name : 'today')) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  if (PUBLIC && !state.snap) {
    view.replaceChildren(head('Agentic desk'), h('div', { class: 'empty' }, h('h3', {}, 'No data has been published yet'),
      h('p', {}, 'The team publishes the desk\'s data from its own machine. Please check back shortly.')));
    return;
  }
  try {
    await render();
  } catch (err) {
    view.replaceChildren(head('Something went wrong'), h('p', { class: 'notice' }, err.message));
  }
}

if (state.snap) {
  document.body.classList.add('snapshot');
  document.querySelector('.nav').after(h('p', { class: 'snap-note' },
    h('strong', {}, 'Read-only view'), ` Data from ${when(state.snap.exported_at)}. Approvals and the agent run on the team's own desk.`));
}
window.addEventListener('hashchange', route);
route();
