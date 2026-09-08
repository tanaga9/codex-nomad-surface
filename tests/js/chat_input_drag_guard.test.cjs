const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const source = readFileSync('codex_nomad_surface/ui_components/assets/chat_input_drag_guard.js', 'utf8');
const marker = 'data-codex-inactive-chat-dropzone';

function setup() {
  class UiEvent extends Event {
    constructor(type, fields = {}) { super(type); Object.assign(this, fields); }
  }
  const element = (css = {}) => ({
    css, children: [], textContent: '', attributes: new Set(),
    setAttribute(name) { this.attributes.add(name); },
    removeAttribute(name) { this.attributes.delete(name); },
  });
  const full = { position: 'absolute', top: '0px', right: '0px', bottom: '0px', left: '0px' };
  const dropzone = element(full);
  const label = element({ ...full, pointerEvents: 'none' });
  label.textContent = 'Drag and drop files here';
  const input = element(); input.parentElement = dropzone;
  dropzone.children = [input]; dropzone.nextElementSibling = label;
  const uploadButton = element({ position: 'static' });
  const buttonInput = element(); buttonInput.parentElement = uploadButton;
  uploadButton.children = [buttonInput]; uploadButton.nextElementSibling = element();
  const root = { querySelectorAll: () => [input, buttonInput] };
  const window = new EventTarget();
  Object.assign(window, { innerWidth: 1000, innerHeight: 800, getComputedStyle: e => e.css });
  const document = new EventTarget();
  let roots = [root]; let observer; let styles = 0;
  Object.assign(document, {
    body: {}, head: { append: () => styles++ }, createElement: element,
    querySelectorAll: () => roots,
  });
  const context = vm.createContext({ window, document, setTimeout,
    MutationObserver: class { constructor(fn) { observer = fn; } observe() {} },
  });
  vm.runInContext(source, context);
  const fire = (type, fields) => window.dispatchEvent(new UiEvent(type, fields));
  const start = () => fire('dragover', { dataTransfer: { types: ['Files'] } });
  return { window, document, context, fire, start, label, dropzone, uploadButton,
    visible: () => !label.attributes.has(marker), mutate: () => observer([{target: {closest: () => root}}]),
    removeChat: () => { roots = []; observer([{target: {closest: () => root}}]); }, styles: () => styles,
  };
}

test('stale overlay on installation is hidden without touching upload button', () => {
  const s = setup();
  assert.equal(s.visible(), false);
  assert.equal(s.dropzone.attributes.has(marker), true);
  assert.equal(s.uploadButton.attributes.size, 0);
});
for (const [x, y] of [[0, 300], [1000, 300], [500, 0], [500, 800]]) {
  test(`exit at ${x},${y} hides overlay and reentry restores drop target`, () => {
    const s = setup(); s.start(); assert.equal(s.visible(), true);
    s.fire('dragleave', { clientX: x, clientY: y });
    assert.equal(s.visible(), false);
    s.start(); assert.equal(s.visible(), true);
    assert.equal(s.dropzone.attributes.size, 0);
  });
}
for (const [type, fields] of [
  ['dragend', {}], ['keydown', { key: 'Escape' }], ['mousemove', { buttons: 0 }],
]) {
  test(`${type} ends drag without emitting events to other components`, () => {
    const s = setup(); let events = 0;
    s.window.addEventListener('dragleave', () => events++);
    s.window.addEventListener('drop', () => events++);
    s.start(); s.fire(type, fields);
    assert.equal(s.visible(), false); assert.equal(events, 0);
  });
}
test('child transitions, held drag and React renders preserve active drop target', () => {
  const s = setup(); s.start();
  s.fire('dragleave', { clientX: 500, clientY: 300 });
  s.fire('mousemove', { buttons: 1 }); s.mutate();
  assert.equal(s.visible(), true);
});
test('drop reaches native handler intact before hiding and works again', async () => {
  const s = setup(); const files = [{}]; const received = [];
  s.window.addEventListener('drop', e => {
    assert.equal(s.visible(), true); received.push(e.dataTransfer.files);
  });
  for (let i = 0; i < 2; i++) {
    s.start(); s.fire('drop', { dataTransfer: { files } });
    await new Promise(resolve => setTimeout(resolve, 0)); assert.equal(s.visible(), false);
  }
  assert.deepEqual(received, [files, files]);
});
test('old drop cleanup cannot hide a new drag', async () => {
  const s = setup(); s.start(); s.fire('drop'); s.start();
  await new Promise(resolve => setTimeout(resolve, 0)); assert.equal(s.visible(), true);
});
test('late overlay mount after cancellation stays hidden', () => {
  const s = setup(); s.start(); s.fire('dragend');
  s.label.removeAttribute(marker); s.mutate();
  assert.equal(s.visible(), false);
});
test('unknown overlay layout is left untouched', () => {
  const s = setup(); s.start(); s.label.css = { position: 'static' };
  s.fire('dragend'); assert.equal(s.visible(), true);
  assert.equal(s.dropzone.attributes.size, 0);
});
test('installation is idempotent and text drags do not restore file overlay', () => {
  const s = setup(); vm.runInContext(source, s.context);
  assert.equal(s.styles(), 1);
  s.fire('dragover', { dataTransfer: { types: ['text/plain'] } });
  assert.equal(s.visible(), false);
});
test('window blur, hidden document and unmounted chat are cleaned up', () => {
  const s = setup(); s.start(); s.fire('blur'); assert.equal(s.visible(), false);
  s.start(); s.document.hidden = true;
  s.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(s.visible(), false);
  s.removeChat(); assert.equal(s.label.attributes.size, 0);
});

test('drop cleanup waits past microtasks scheduled by native handlers', async () => {
  const s = setup(); s.start();
  s.fire('drop');
  await Promise.resolve();
  assert.equal(s.visible(), true);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(s.visible(), false);
});
