import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

const source = fs.readFileSync(new URL('../../central/app/static/i18n.js', import.meta.url), 'utf8');
const labels = {
  'o agente chama a ferramenta do cliente': 'the agent calls the client tool',
  'o agente usa o resultado e responde': 'the agent uses the result and responds',
  'tipo': 'type',
  'política de código': 'code policy',
  'configuração': 'configuration',
};

test('coding probe labels translate in English and remain unchanged in Portuguese', () => {
  for (const language of ['en', 'pt']) {
    const window = { confirm() {}, prompt() {}, alert() {} };
    vm.runInNewContext(source, {
      window,
      document: { documentElement: {}, body: null, addEventListener() {} },
      navigator: { language },
      localStorage: { getItem: () => language },
    });
    for (const [label, translated] of Object.entries(labels)) {
      assert.equal(language === 'en' ? window.tr(label) : window.t(label), language === 'en' ? translated : label);
    }
  }
});
