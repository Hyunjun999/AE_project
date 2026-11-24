/* Store desk: plain JavaScript, no build step. Talks to the backend under /api. */

const view = document.getElementById('view');
const state = { overview: null, chat: [], storeFilter: 'SALE', lastEvents: null };

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

// ---------------------------------------------------------------- Today
async function today() {
  const o = await refreshOverview();
  const d = o.drafts;
  const lede = d.pending > 0
    ? `${plural(d.pending, 'draft is', 'drafts are')} waiting for your approval.`
    : 'Nothing is waiting for you.';
  let sub;
  if (o.store.error) sub = 'The store could not be read just now. See the Store page for the reason.';
  else {
    const c = o.store.count_by_status;
    const sale = c.SALE || 0;
    const rest = Object.entries(c).filter(([k]) => k !== 'SALE').map(([k, n]) => `${n} ${statusName(k).toLowerCase()}`);
    sub = `${plural(sale, 'product is', 'products are')} on sale` + (rest.length ? `, ${rest.join(', ')}.` : '.');
  }

  view.replaceChildren(
    h('p', { class: 'lede' }, lede),
    h('p', { class: 'lede-sub' }, sub),
    d.pending > 0 && h('p', { style: 'margin-top:24px' }, h('a', { class: 'btn primary', href: '#/approvals', style: 'text-decoration:none;display:inline-block' }, 'Review drafts')),

    h('h2', {}, 'Drafts'),
    h('div', { class: 'flow' },
      h('a', { href: '#/approvals', class: d.pending ? 'wait' : '' }, h('strong', {}, d.pending), 'waiting'),
      h('div', {}, h('strong', {}, d.approved), 'approved'),
      h('div', {}, h('strong', {}, d.rejected), 'rejected'),
      h('div', {}, h('strong', {}, d.published), 'published')),

    h('h2', {}, 'Store'),
    storeBar(o.store),

    h('h2', {}, 'Rules in force'),
    h('p', { class: 'muted' },
      `Every price must leave a ${Math.round(o.rules.min_margin * 100)}% margin after ${(o.rules.fee_rate * 100).toFixed(2)}% in fees. ` +
      `Titles stay within ${o.rules.max_title} characters. The agent drafts and answers; ${o.reviewer} approves.`),
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
  const [list] = await Promise.all([api('/api/drafts?status=pending'), refreshOverview()]);
  const maxTitle = state.overview.rules.max_title;

  const generate = async (useLlm, button) => {
    button.disabled = true;
    try {
      const result = await api('/api/drafts/generate', { body: { use_llm: useLlm } });
      state.lastEvents = result.events;
      toast(result.created ? `${plural(result.created, 'draft', 'drafts')} created` : 'No new drafts', !result.created && result.events.some((e) => e.kind === 'error'));
      await approvals();
    } catch (err) { toast(err.message, true); button.disabled = false; }
  };
  const formulaBtn = h('button', { class: 'btn', onclick: (e) => generate(false, e.currentTarget) }, 'Draft from keywords');
  const llmBtn = h('button', { class: 'btn', onclick: (e) => generate(true, e.currentTarget) }, 'Draft with the LLM');

  const nodes = [head('Approvals',
    'Approving marks a draft as ready. Nothing is published to the store from this page.', formulaBtn, llmBtn)];

  if (state.lastEvents && state.lastEvents.length) {
    const label = { gate0_reject: 'Below the price floor', skip: 'Skipped', draft: 'Drafted', notice: 'Note', error: 'Problem' };
    nodes.push(h('ul', { class: 'events' }, state.lastEvents.map((e) =>
      h('li', {}, h('strong', {}, `${label[e.kind] || e.kind}${e.item_id ? ` ${e.item_id}` : ''}: `), e.message))));
    state.lastEvents = null;
  }

  if (!list.length) {
    nodes.push(h('div', { class: 'empty' },
      h('h3', {}, 'No drafts are waiting'),
      h('p', {}, 'Add supplier items to backend/candidates.csv, then draft titles for them. "Draft from keywords" uses no tokens; "Draft with the LLM" spends some of this week\'s budget.')));
  } else {
    nodes.push(...list.map((d) => ticket(d, maxTitle)));
  }
  view.replaceChildren(...nodes);
}

function ticket(d, maxTitle) {
  const input = h('input', { type: 'text', value: d.title, 'aria-label': `Title of draft ${d.id}` });
  const chars = h('span', { class: 'chars' });
  const approve = h('button', { class: 'btn primary' }, 'Approve');
  const reject = h('button', { class: 'btn danger' }, 'Reject');
  const note = h('span', { class: 'note' });

  const sync = () => {
    const length = input.value.trim().length;
    const edited = input.value.trim() !== d.title;
    chars.textContent = `${length} of ${maxTitle}`;
    chars.classList.toggle('over', length > maxTitle);
    approve.textContent = edited ? 'Save title and approve' : 'Approve';
    approve.disabled = length === 0 || length > maxTitle || (d.blocked && !edited);
    note.textContent = d.blocked && !edited ? 'Edit the title to remove the blocked wording, or reject.' : '';
  };
  input.addEventListener('input', sync);

  const node = h('article', { class: 'ticket' },
    h('div', { class: 'ticket-body' },
      h('div', { class: 'ticket-top' },
        h('span', {}, `Draft ${d.id}, ${d.source === 'llm' ? 'written by the LLM' : 'built from keywords'}`),
        h('span', {}, `${d.supplier} / ${d.item_id}`)),
      h('div', { class: 'title-row' }, input, chars),
      h('p', { class: 'source-name' }, `Supplier calls it: ${d.name}`),
      d.tags.length ? h('ul', { class: 'tags', 'aria-label': 'Tags' }, d.tags.map((t) => h('li', {}, t))) : null,
      ruler({ cost: d.cost, floor: d.floor, price: d.price }),
      h('div', { class: 'money' },
        h('div', {}, h('span', {}, 'Profit per sale'), h('strong', {}, `${won(d.profit)} KRW`)),
        h('div', {}, h('span', {}, 'Margin'), h('strong', {}, `${d.margin_pct}%`)),
        h('div', {}, h('span', {}, 'Above the floor by'), h('strong', {}, `${won(d.price - d.floor)} KRW`))),
      d.flags.length ? h('ul', { class: 'flags' }, d.flags.map((f) =>
        h('li', { class: /^(banned word|title longer)/.test(f) ? 'hard' : '' }, f))) : null),
    h('div', { class: 'ticket-stub' }, approve, reject, note));

  const send = async (action) => {
    approve.disabled = reject.disabled = true;
    try {
      const body = action === 'approve' ? { title: input.value.trim() } : {};
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
    head('Store', `Read from Naver at ${s.fetched_at}. This page only reads; it changes nothing in the store.`),
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
    gate0_reject: 'Stopped at the price floor', chat: 'Question asked' };
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
const views = { today, approvals, store, price, ask, activity };

async function route() {
  const name = (location.hash.replace('#/', '') || 'today');
  const render = views[name] || today;
  document.querySelectorAll('.nav a').forEach((a) => {
    if (a.dataset.view === (views[name] ? name : 'today')) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  try {
    await render();
  } catch (err) {
    view.replaceChildren(head('Something went wrong'), h('p', { class: 'notice' }, err.message));
  }
}

window.addEventListener('hashchange', route);
route();
