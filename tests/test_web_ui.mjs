import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Exercise real connection-recovery functions without a browser or providers.
// The DOM shim covers their status fields; visual behavior is checked in-browser.
function app(fetch) {
  const nodes = new Map();
  const timers = [];
  const document = {
    activeElement: null,
    createElement(tag) {
      const node = {
        tagName: tag.toUpperCase(), value: '', hidden: false, disabled: false, inert: false,
        textContent: '', children: [], attributes: {}, dataset: {}, listeners: {}, className: '',
        append(...children) { this.children.push(...children); },
        replaceChildren(...children) {
          if (this.contains(document.activeElement) && document.activeElement !== this) document.activeElement = document.getElementById('body');
          this.children = children; this.textContent = '';
        },
        contains(node) { return this === node || this.children.some(child => child.contains(node)); },
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, callback) { this.listeners[name] = callback; },
        focus() { document.activeElement = this; },
        getClientRects() { return this.hidden ? [] : [{}]; },
        querySelectorAll() { return this.children; },
      };
      const classes = new Set();
      node.classList = {
        contains(name) { return classes.has(name); },
        toggle(name, value) { if (value ?? !classes.has(name)) classes.add(name); else classes.delete(name); },
      };
      return node;
    },
    getElementById(id) {
      if (!nodes.has(id)) { const node = this.createElement('div'); node.value = 'existing'; nodes.set(id, node); }
      return nodes.get(id);
    },
    querySelector(selector) { return this.getElementById(selector); },
    querySelectorAll() { return []; },
  };
  const context = vm.createContext({
    fetch,
    document,
    window: {addEventListener() {}, innerWidth: 1200},
    setTimeout(fn, delay) { timers.push(delay); return timers.length; },
    clearTimeout() {},
  });
  const source = readFileSync(new URL('../research_ui/static/app.js', import.meta.url), 'utf8');
  vm.runInContext(source.replace(/^refreshConnection\(\);\s*$/m, ''), context);
  return {context, timers, document, nodes};
}

function textOf(node) {
  return [node.textContent, ...node.children.map(textOf)].filter(Boolean).join(' ');
}

test('an unknown job after a restart stops polling and reconciles history', async () => {
  const {context, timers} = app(async () => ({ok: false, status: 404, json: async () => ({error: 'Job not found.'})}));
  await vm.runInContext(`
    state.job = {id:'old-job',state:'running'};
    var refreshes = 0;
    renderJob = () => {};
    refreshConnection = async () => { refreshes++; };
    pollJob();
  `, context);
  assert.equal(vm.runInContext('state.job.state', context), 'failed');
  assert.equal(vm.runInContext('refreshes', context), 1);
  assert.deepEqual(timers, []);
});

test('a temporary service error retries status without starting another run', async () => {
  let calls = 0;
  const {context, timers} = app(async (path) => {
    calls++;
    assert.equal(path, '/api/jobs/active-job');
    return {ok: false, status: 503, json: async () => ({error: 'Temporary problem.'})};
  });
  await vm.runInContext("state.job={id:'active-job',state:'running'}; pollJob();", context);
  assert.equal(vm.runInContext('state.job.state', context), 'running');
  assert.equal(calls, 1);
  assert.deepEqual(timers, [5000]);
});

test('refresh clears a vanished active job rather than disabling new research forever', async () => {
  const {context} = app(async (path) => ({ok: true, json: async () => path === '/api/status'
    ? {active_job: null, writer: {configured: true}, verifiers: []}
    : {runs: []}}));
  await vm.runInContext(`
    state.job={id:'old-job',state:'running'};
    state.data={};
    renderJob=()=>{};
    renderHistory=()=>{};
    renderProviderChoices=()=>{};
    refreshConnection();
  `, context);
  assert.equal(vm.runInContext('state.job.state', context), 'failed');
});

test('mobile Show more reveals all completed runs, including five to eight runs', () => {
  for (const count of [6, 12]) {
    const {context, document} = app();
    document.getElementById('history-filter').value = '';
    vm.runInContext(`window.innerWidth=320; state.runs=Array.from({length:${count}}, (_,i)=>({id:'run-'+i,status:'completed'})); renderHistory();`, context);
    const history = document.getElementById('run-history');
    assert.equal(history.children.filter(node => node.className.startsWith('history-item')).length, 4);
    history.children.find(node => node.textContent.startsWith('Show ')).listeners.click();
    assert.equal(history.children.filter(node => node.className.startsWith('history-item')).length, count);
  }
});

test('simple table passages render as text-only table cells', () => {
  const {context} = app();
  context.quote = '**Results**\n| Metric | Value |\n| --- | --- |\n| <em>Revenue</em> | 20 |';
  const rendered = vm.runInContext('quoteNode(quote)', context);
  assert.equal(rendered.tagName, 'FIGURE');
  const table = rendered.children.find(node => node.tagName === 'TABLE');
  assert.deepEqual(table.children[1].children.map(node => node.textContent), ['<em>Revenue</em>', '20']);
  assert.ok(table.children.every(row => row.children.every(cell => cell.children.length === 0)));
});

test('ambiguous table passages preserve the entire original text', () => {
  const {context} = app();
  for (const quote of [
    '| Region | Revenue |\n| --- | --- |\n| North \\| South | 20 |',
    'Consolidated\nUnaudited\n| Metric | Value |\n| --- | --- |\n| Revenue | 20 |',
    '| Metric | Value |\n| --- | --- |\n| Revenue | 20 |\nAmounts exclude tax.',
    '| Metric | Value |\n| --- | --- |\n| Revenue | 20 | 30 |',
    '| Revenue | 20 |',
  ]) {
    context.quote = quote;
    const rendered = vm.runInContext('quoteNode(quote)', context);
    assert.equal(rendered.tagName, 'BLOCKQUOTE');
    assert.equal(rendered.textContent, quote);
  }
});

test('the open run drawer contains Tab focus and restores background controls on close', () => {
  const {context, document} = app();
  const drawer = document.getElementById('run-drawer');
  const first = document.getElementById('runs-close'), last = document.getElementById('connection-refresh');
  drawer.children = [first, document.getElementById('history-filter'), last];
  vm.runInContext('toggleRuns(true)', context);
  assert.equal(document.getElementById('main-content').inert, true);
  assert.equal(document.getElementById('.topbar').inert, true);
  assert.equal(document.activeElement, document.getElementById('history-filter'));
  for (const [from, shiftKey, to] of [[last, false, first], [first, true, last]]) {
    from.focus();
    let prevented = false;
    drawer.listeners.keydown({key:'Tab', shiftKey, preventDefault() { prevented = true; }});
    assert.equal(prevented, true);
    assert.equal(document.activeElement, to);
  }
  drawer.listeners.keydown({key:'Escape', preventDefault() {}});
  assert.equal(document.getElementById('main-content').inert, false);
  assert.equal(document.getElementById('.topbar').inert, false);
  assert.equal(drawer.inert, true);
  assert.equal(document.activeElement, document.getElementById('runs-toggle'));
});

test('coverage labels describe keyword findings rather than assert missing coverage', () => {
  const {context, document} = app();
  vm.runInContext(`state.data={sections:[],coverage:{items:[
    {label:'Growth',status:'review_needed',claim_ids:[],source_hits:[]},
    {label:'Margins',status:'partial',missing_figures:['18'],claim_ids:[],source_hits:[]}
  ]}}; renderCoverage();`, context);
  const content = textOf(document.getElementById('panel-coverage'));
  assert.match(content, /No mention found/);
  assert.match(content, /Figures to review: 18/);
  assert.doesNotMatch(content, /Not in brief|the brief does not|Not stated in the brief/);
});

test('summary and source footer distinguish failed checks from unchecked or unresolved items', () => {
  const {context, document} = app();
  vm.runInContext(`state.data={sections:[{title:'Snapshot',claims:[
    {id:'confirmed',text:'Confirmed type',kind_confirmed:true},
    {id:'unconfirmed',text:'Unconfirmed type',checks:{kind_confirmed:false}},
    {id:'unchecked',text:'Unchecked type',checks:{kind_confirmed:null}},
    {id:'unrecorded',text:'No check recorded'}
  ]}],sources:[{status:'evidence'},{status:'excluded'},{status:'unclear'},{status:'not_checked'}],audit:{},coverage:{items:[]}};
  renderSummary(); renderBrief();`, context);
  const summary = textOf(document.getElementById('report-summary'));
  assert.match(summary, /1 statement type unconfirmed/);
  assert.match(summary, /2 statement types not checked/);
  assert.match(summary, /1 of 4 sources excluded/);
  assert.match(summary, /2 sources not resolved/);
  const brief = textOf(document.getElementById('panel-brief'));
  assert.match(brief, /4 documents, 1 excluded, 2 not resolved/);
});

test('history expansion and other-run toggles keep focus in the refreshed drawer list', () => {
  const {context, document} = app();
  const filter = document.getElementById('history-filter'); filter.value = '';
  vm.runInContext(`window.innerWidth=320; state.runs=[
    ...Array.from({length:6}, (_,i)=>({id:'run-'+i,status:'completed'})),
    {id:'failed-run',status:'failed'}
  ]; renderHistory();`, context);
  const history = document.getElementById('run-history');
  for (const prefix of ['Show 2 more', 'Show 1 failed', 'Hide failed']) {
    const control = history.children.find(node => node.textContent.startsWith(prefix));
    assert.ok(control);
    control.focus(); control.listeners.click();
    assert.ok(history.contains(document.activeElement), `${prefix} should leave focus inside the current history`);
  }
  filter.focus(); vm.runInContext('renderHistory()', context);
  assert.equal(document.activeElement, filter);
});
