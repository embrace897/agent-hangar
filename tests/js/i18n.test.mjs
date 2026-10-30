import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

const source = fs.readFileSync(new URL('../../central/app/static/i18n.js', import.meta.url), 'utf8');

function translator(language = 'en') {
  const events = [];
  const document = { documentElement: {}, body: null, addEventListener: (_, fn) => events.push(fn) };
  const window = { confirm() {}, prompt() {}, alert() {} };
  const context = { window, document, navigator: { language }, localStorage: { getItem: () => language } };
  context.NodeFilter = { SHOW_TEXT: 4, SHOW_ELEMENT: 1, FILTER_ACCEPT: 1, FILTER_REJECT: 2 };
  context.MutationObserver = class {
    constructor(callback) { window.mutation = callback; }
    observe() {}
  };
  document.createTreeWalker = () => ({ nextNode: () => null });
  document.querySelector = selector => selector === '.lang-float' ? {} : null;
  vm.runInNewContext(source, context);
  return { window, document, start: () => { events.forEach(fn => fn()); } };
}

test('translates exact strings, counters and units', () => {
  const { window } = translator();
  assert.equal(window.tr('Organograma'), 'Org chart');
  assert.equal(window.tr('Para você decidir (3)'), 'For you to decide (3)');
  assert.equal(window.tr('Times (4)'), 'Teams (4)');
  assert.equal(window.tr('8 erros'), '8 errors');
  assert.equal(window.tr('2 d atrás'), '2 d ago');
});

test('preserves unknown text and uses the selected locale', () => {
  const { window, document } = translator();
  assert.equal(window.tr('A synthetic sentence with no known phrases.'), 'A synthetic sentence with no known phrases.');
  assert.equal(document.documentElement.lang, 'en');
  const pt = translator('pt');
  assert.equal(pt.window.t('Organograma'), 'Organograma');
  assert.equal(pt.document.documentElement.lang, 'pt-BR');
});

test('DOM updates preserve protected agent output while translating UI text', () => {
  const { window, document, start } = translator();
  document.body = { nodeType: 1, closest: () => null };
  document.title = 'Organograma';
  start();
  const protectedText = {
    nodeType: 3,
    nodeValue: 'Organograma',
    parentElement: { closest: selector => selector.includes('[data-noi18n]') ? {} : null },
  };
  const uiText = { nodeType: 3, nodeValue: 'Organograma', parentElement: { closest: () => null } };
  window.mutation([{ type: 'characterData', target: protectedText }, { type: 'characterData', target: uiText }]);
  assert.equal(protectedText.nodeValue, 'Organograma');
  assert.equal(uiText.nodeValue, 'Org chart');
});
