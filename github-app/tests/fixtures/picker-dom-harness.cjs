// Execute the actual server-rendered picker script with a minimal DOM.
// A real browser pass complements this test for layout and native events.
const assert = require('node:assert/strict');
const vm = require('node:vm');
let source = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { source += chunk; });
process.stdin.on('end', async () => {
  class Element {
    constructor(dataset = {}) { this.dataset = dataset; this.attributes = {}; this.listeners = {}; this.children = []; this.value = ''; this.hidden = false; this._html = ''; this.textContent = ''; }
    set innerHTML(value) { this._html = value; this.children = []; }
    get innerHTML() { return this._html; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    appendChild(child) { this.children.push(child); }
    dispatch(name) { this.listeners[name](); }
  }
  async function run(response) {
    const body = new Element(), toolbar = new Element(), search = new Element(), count = new Element();
    const filters = ['all', 'available', 'waiting'].map((filter) => new Element({filter}));
    const elements = {'picker-body': body, 'picker-toolbar': toolbar, 'picker-search': search, 'picker-count': count};
    const location = {href: '', replace(url) { this.href = url; }};
    const context = {
      document: {getElementById: (id) => elements[id], querySelectorAll: () => filters, createElement: () => new Element()},
      window: {location}, console: {error() {}},
      fetch: async (url) => { assert.equal(url, '/app/repos'); if (response.error) throw Error('network'); return response; },
    };
    vm.runInNewContext(source, context);
    await new Promise((resolve) => setImmediate(resolve));
    return {body, toolbar, search, count, filters, location};
  }
  const ok = (data) => ({ok: true, status: 200, json: async () => data});
  const repos = [
    {org: '__proto__', repo: 'safe', plan: 'air', initialized: true},
    {org: 'example', repo: '<img src=x onerror=alert(1)>', plan: 'air', initialized: true},
    {org: 'example', repo: 'waiting', plan: 'air', initialized: false},
    {org: 'example', repo: 'limited', plan: 'air', initialized: false, scan_limit_reached: true},
  ];
  const billing_accounts = [{installation_id: 123, account_login: 'example', plan: 'flash', credit_remaining_usd: 3.5}];
  const ui = await run(ok({repos, billing_accounts}));
  assert.equal(ui.count.textContent, '4 repositories');
  assert.equal(ui.toolbar.hidden, false);
  let markup = ui.body.children.map((group) => group.innerHTML).join('');
  assert.match(markup, /\/dashboard\/__proto__\/safe/);
  assert.match(markup, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(markup, /<img src=x onerror/);
  assert.match(markup, /\/credits\/123/);
  assert.match(markup, /Monthly limit reached/);
  ui.filters[2].dispatch('click');
  assert.equal(ui.count.textContent, '2 repositories shown');
  assert.equal(ui.filters[2].attributes['aria-pressed'], 'true');
  markup = ui.body.children.map((group) => group.innerHTML).join('');
  assert.doesNotMatch(markup, /\/dashboard\//);
  assert.match(markup, /\/credits\/123/);
  ui.search.value = 'limited'; ui.search.dispatch('input');
  assert.equal(ui.count.textContent, '1 repository shown');
  ui.search.value = 'no-such-repo'; ui.search.dispatch('input');
  assert.equal(ui.body.children[0].textContent, 'No repositories match your search or filter.');
  const empty = await run(ok({repos: [], billing_accounts: []}));
  assert.match(empty.body.innerHTML, /No managed repositories yet/);
  assert.match(empty.body.innerHTML, /plan=air/);
  const flash = await run(ok({repos: [], billing_accounts}));
  assert.equal(flash.location.href, '/credits/123');
  for (const response of [{error: true}, {ok: false, status: 500}, {ok: true, status: 200, json: async () => {throw Error('bad json');}}, ok({repos: null})]) {
    const failed = await run(response);
    assert.match(failed.body.innerHTML, /Could not load repositories/);
    assert.doesNotMatch(failed.body.innerHTML, /No managed repositories yet/);
  }
  const expired = await run({ok: false, status: 401});
  assert.equal(expired.location.href, '/auth/logout');
  console.log('picker API, filters, escaping, error, empty, and billing states passed');
});
