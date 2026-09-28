const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync('codex_nomad_surface/ui_components/assets/template_delivery.js', 'utf8')
  .replace('export default function', 'function mount');

function environment() {
  const context = {
    window: {},
    document: { createElement: () => ({ setAttribute() {}, style: {}, remove() {} }) },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context;
}
function mount(env, data = { token: 'one', text: 'Prompt', enabled: true }) {
  const nodes = [], acknowledgements = [];
  env.mount({ data, parentElement: { append: (...items) => nodes.push(...items) },
    setTriggerValue: (key, token) => acknowledgements.push([key, token]) });
  return { nodes, acknowledgements };
}

test('missing bridge remains retryable; success and remount append exactly once', () => {
  const env = environment();
  const first = mount(env);
  assert.match(first.nodes[0].textContent, /retained/);
  assert.deepEqual(first.acknowledgements, []);
  let appended = 0;
  env.window.codexNomadSurface = { appendToChatInput: text => {
    assert.equal(text, 'Prompt'); appended++; return true;
  }};
  first.nodes[1].onclick();
  assert.deepEqual(first.acknowledgements, [['ack', 'one']]);
  const second = mount(env);
  assert.deepEqual(second.acknowledgements, [['ack', 'one']]);
  assert.equal(appended, 1);
});

test('a failed append is not acknowledged and can be retried', () => {
  const env = environment();
  env.window.codexNomadSurface = { appendToChatInput: () => false };
  const result = mount(env);
  assert.deepEqual(result.acknowledgements, []);
  env.window.codexNomadSurface.appendToChatInput = () => true;
  result.nodes[1].onclick();
  assert.deepEqual(result.acknowledgements, [['ack', 'one']]);
});

test('paused operations do not append or acknowledge', () => {
  const env = environment();
  env.window.codexNomadSurface = { appendToChatInput: () => { throw Error('must not run'); } };
  const result = mount(env, {token: 'one', text: 'Prompt', enabled: false});
  assert.deepEqual(result.acknowledgements, []);
  assert.equal(result.nodes[1].disabled, true);
});
