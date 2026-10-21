// Entrypoint do container de job (1 por execução, efêmero). Roda a tarefa com o harness escolhido
// (HARNESS_ID: "claude-code" | "codex" | "hermes" | "deepseek-harness" | "mock"), captura o resultado
// e um diff das mudanças feitas em /workspace, e devolve tudo via callback HTTP para a central. Nunca
// fica em pé: termina e é removido assim que o callback é enviado.
'use strict';
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const env = process.env;
const WORKDIR = '/workspace';
// No Kubernetes o volume do workspace pertence a outro uid (emptyDir de root): sem isto o git recusa o repositório
// ("dubious ownership") e o diff do job sai vazio. Vai pelo ambiente, então vale também para os CLIs dos harnesses.
if (!env.GIT_CONFIG_COUNT) Object.assign(env, { GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'safe.directory', GIT_CONFIG_VALUE_0: '*' });
const REAL_HARNESS_ID = env.HARNESS_ID || env.MODE || 'claude-code'; // MODE: compat. com versão anterior
const TASK = env.TASK || '';
const SYSTEM_PROMPT = env.SYSTEM_PROMPT || '';
const MCP_CONFIG_JSON = env.MCP_CONFIG_JSON || '{}';
const CALLBACK_URL = env.JOB_CALLBACK_URL;
const JOB_TOKEN = env.JOB_TOKEN || '';
// Nomes genéricos vindos da central; cada harness abaixo mapeia para a env var do seu próprio provedor.
const LLM_BASE_URL = env.LLM_BASE_URL || '';
const LLM_API_KEY = env.LLM_API_KEY || '';
const LLM_MODEL = env.LLM_MODEL || '';
// Perfil de execução resolvido pela central (services/execution.py), já no vocabulário deste harness:
// {effort, max_output_tokens, max_turns, prompt_cache}. Só vem o que o harness e o modelo aceitam.
const EXEC = (() => { try { return JSON.parse(env.LLM_EXEC_JSON || '{}') || {}; } catch { return {}; } })();
// Sem chave associada -> a central já sabe que é mock (resolve_harness marca mock=True), mas o
// entrypoint decide sozinho pela ausência de LLM_API_KEY, sem precisar de uma env var extra pra isso.
const HARNESS_ID = LLM_API_KEY ? REAL_HARNESS_ID : 'mock';

function run(cmd, args, opts = {}) {
  return execFileSync(cmd, args, { cwd: WORKDIR, encoding: 'utf8', maxBuffer: 1024 * 1024 * 20, ...opts });
}

function safe(fn, fallback = '') {
  try { return fn(); } catch (e) { return fallback + String(e.message || e); }
}

function mcpServers() {
  try { return JSON.parse(MCP_CONFIG_JSON || '{}'); } catch (e) {
    console.error('MCP_CONFIG_JSON inválido, ignorando:', e);
    return {};
  }
}

async function callback(status, result, diff, logs, usage) {
  const body = JSON.stringify({ status, result: String(result || '').slice(0, 20000),
    diff: String(diff || '').slice(0, 50000), logs: String(logs || '').slice(0, 20000), usage: usage || null });
  try {
    await fetch(CALLBACK_URL, { method: 'POST', headers: { 'Content-Type': 'application/json',
      'Authorization': `Bearer ${JOB_TOKEN}` }, body });
  } catch (e) {
    console.error('falha ao enviar callback:', e); // sem callback, a central marca o job como timeout
  }
}

function setupWorkspace() {
  fs.mkdirSync(WORKDIR, { recursive: true });
  safe(() => run('git', ['init', '-q']));
  // Estado interno dos harnesses não é "trabalho" do usuário. Vai em .git/info/exclude (não num .gitignore,
  // que apareceria no diff); HOME fica fora do workspace (ver Dockerfile), então caches/logs dos CLIs
  // (~/.npm, ~/.codex…) nem chegam aqui.
  safe(() => fs.writeFileSync(path.join(WORKDIR, '.git', 'info', 'exclude'),
    '.codex/\n.claude/\n.hermes/\n.dsh/\n.npm/\n.cache/\n.config/\n.local/\n'));
}

function gitDiff() {
  return safe(() => { run('git', ['add', '-A']); return run('git', ['diff', '--cached']); });
}

function runMock() {
  fs.writeFileSync(path.join(WORKDIR, 'JOB_NOTES.md'),
    `# Job mock\n\nTarefa recebida (nenhuma conexão de LLM real associada a este harness):\n\n${TASK}\n`);
  return { status: 'passed', result: `[mock:${REAL_HARNESS_ID}] tarefa recebida: ${TASK}`, logs: '' };
}

function runClaudeCode() {
  const servers = mcpServers();
  let mcpConfigPath = null;
  if (Object.keys(servers).length) {
    mcpConfigPath = '/tmp/hangar-mcp.json';
    fs.writeFileSync(mcpConfigPath, JSON.stringify({ mcpServers: servers }, null, 2));
  }
  const args = ['-p', TASK, '--output-format', 'json', '--permission-mode', 'acceptEdits'];
  if (SYSTEM_PROMPT) args.push('--append-system-prompt', SYSTEM_PROMPT);
  if (LLM_MODEL) args.push('--model', LLM_MODEL);
  if (mcpConfigPath) args.push('--mcp-config', mcpConfigPath);
  if (EXEC.effort) args.push('--effort', EXEC.effort);
  if (EXEC.max_turns) args.push('--max-turns', String(EXEC.max_turns));
  const childEnv = { ...env, ANTHROPIC_API_KEY: LLM_API_KEY };
  if (LLM_BASE_URL) childEnv.ANTHROPIC_BASE_URL = LLM_BASE_URL;
  if (EXEC.max_output_tokens) childEnv.CLAUDE_CODE_MAX_OUTPUT_TOKENS = String(EXEC.max_output_tokens);
  if (EXEC.prompt_cache === false) childEnv.DISABLE_PROMPT_CACHING = '1';
  let stdout = '', stderr = '', failed = false;
  try {
    stdout = run('claude', args, { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    failed = true;
    stdout = e.stdout ? e.stdout.toString() : '';
    stderr = (e.stderr ? e.stderr.toString() : '') + '\n' + String(e.message || e);
  }
  let parsed = null;
  try { parsed = JSON.parse(stdout); } catch { /* saída não-JSON: usa o texto cru */ }
  const result = parsed && typeof parsed.result === 'string' ? parsed.result : (stdout || stderr || '(sem saída)');
  const isError = failed || (parsed && parsed.is_error);
  // --output-format json traz usage (tokens de cache contam como entrada) e o custo que o próprio CLI calculou
  const u = (parsed && parsed.usage) || null;
  const usage = u ? {
    input_tokens: (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0) + (u.cache_read_input_tokens || 0),
    output_tokens: u.output_tokens || 0, reported_cost_usd: parsed.total_cost_usd } : null;
  return { status: isError ? 'failed' : 'passed', result, logs: stderr.slice(-8000), usage };
}

function runCodex() {
  const servers = mcpServers();
  for (const [name, cfg] of Object.entries(servers)) {
    safe(() => run('codex', ['mcp', 'add', name, '--url', cfg.url]));
  }
  const fullPrompt = SYSTEM_PROMPT ? `${SYSTEM_PROMPT}\n\n---\nTarefa:\n${TASK}` : TASK;
  const outFile = '/tmp/codex-last-message.txt';
  // OPENAI_BASE_URL sozinho não funciona: o provedor "openai" embutido do Codex CLI fala com um
  // WebSocket fixo em api.openai.com. Testado manualmente contra a OpenRouter: um "model_provider"
  // customizado com wire_api="responses" (não "chat" — descontinuado nesta versão) funciona via REST.
  const args = ['exec', fullPrompt, '--skip-git-repo-check', '--sandbox', 'workspace-write',
    '--dangerously-bypass-approvals-and-sandbox', '--ephemeral', '--json', '-o', outFile,
    '-c', 'model_providers.central.name="central"',
    '-c', `model_providers.central.base_url="${LLM_BASE_URL}"`,
    '-c', 'model_providers.central.wire_api="responses"',
    '-c', 'model_providers.central.env_key="OPENAI_API_KEY"',
    '-c', 'model_provider="central"'];
  if (LLM_MODEL) args.push('--model', LLM_MODEL);
  if (EXEC.effort) args.push('-c', `model_reasoning_effort="${EXEC.effort}"`);
  const childEnv = { ...env, OPENAI_API_KEY: LLM_API_KEY };
  let stdout = '', stderr = '', failed = false;
  try {
    stdout = run('codex', args, { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    failed = true;
    stdout = e.stdout ? e.stdout.toString() : '';
    stderr = (e.stderr ? e.stderr.toString() : '') + '\n' + String(e.message || e);
  }
  const lastMessage = safe(() => fs.readFileSync(outFile, 'utf8'), '');
  const result = lastMessage || stdout || stderr || '(sem saída)';
  return { status: failed ? 'failed' : 'passed', result, logs: (stderr + '\n' + stdout).slice(-8000),
    usage: codexUsage(stdout) };
}

// --json do Codex é JSONL; cada turno fecha com {"type":"turn.completed","usage":{input_tokens,output_tokens}}
function codexUsage(jsonl) {
  let input = 0, output = 0, seen = false;
  for (const line of String(jsonl || '').split('\n')) {
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    if (ev && ev.type === 'turn.completed' && ev.usage) {
      seen = true;
      input += ev.usage.input_tokens || 0;
      output += ev.usage.output_tokens || 0;
    }
  }
  return seen ? { input_tokens: input, output_tokens: output } : null;
}

function runHermes() {
  // hermes mcp add persiste em $HERMES_HOME (fixo, ver Dockerfile) — mesmo padrão de MCP dos outros.
  const servers = mcpServers();
  for (const [name, cfg] of Object.entries(servers)) {
    safe(() => run('hermes', ['mcp', 'add', name, '--url', cfg.url]));
  }
  const fullPrompt = SYSTEM_PROMPT ? `${SYSTEM_PROMPT}\n\n---\nTarefa:\n${TASK}` : TASK;
  // -z/--oneshot: "send a single prompt and print ONLY the final response text to stdout... approvals
  // are auto-bypassed" (confirmado via `hermes --help`) — não precisa de parsing de JSON, o stdout já
  // é a resposta final. --provider openai-api usa OPENAI_API_KEY/OPENAI_BASE_URL automaticamente.
  // --usage-file: relatório JSON de tokens/custo, escrito mesmo se a execução falhar (fora do /workspace
  // para não aparecer no diff)
  const usageFile = '/tmp/hermes-usage.json';
  const args = ['-z', fullPrompt, '--provider', 'openai-api', '--usage-file', usageFile];
  if (LLM_MODEL) args.push('-m', LLM_MODEL);
  if (EXEC.effort) args.push('--reasoning', EXEC.effort);
  const childEnv = { ...env, OPENAI_API_KEY: LLM_API_KEY };
  if (LLM_BASE_URL) childEnv.OPENAI_BASE_URL = LLM_BASE_URL;
  let stdout = '', stderr = '', failed = false;
  try {
    stdout = run('hermes', args, { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    failed = true;
    stdout = e.stdout ? e.stdout.toString() : '';
    stderr = (e.stderr ? e.stderr.toString() : '') + '\n' + String(e.message || e);
  }
  const result = stdout.trim() || stderr || '(sem saída)';
  let usage = null;
  try {
    const r = JSON.parse(fs.readFileSync(usageFile, 'utf8'));
    usage = { input_tokens: (r.input_tokens || 0) + (r.cache_read_tokens || 0) + (r.cache_write_tokens || 0),
      output_tokens: r.output_tokens || 0, reported_cost_usd: r.estimated_cost_usd };
  } catch { /* sem relatório: segue sem usage */ }
  return { status: failed ? 'failed' : 'passed', result, logs: stderr.slice(-8000), usage };
}

function runDeepseekHarness() {
  // Sem --profile headless: "answer one task, print the result, and exit" (confirmado via `dsh --help`).
  // DSH_PERMISSION_MODE=danger-full-access desliga o approval interativo (confirmado no --dump-default-config).
  // Sem mecanismo de MCP genérico verificado (o catálogo de plugins do DSH é outra coisa) — não wireamos
  // mcps aqui; e sem confirmação de que DEEPSEEK_BASE_URL é honrado pelo provedor nativo (setamos por via
  // das dúvidas, mas só o caminho com a API oficial da DeepSeek foi testado de verdade).
  const fullPrompt = SYSTEM_PROMPT ? `${SYSTEM_PROMPT}\n\n---\nTarefa:\n${TASK}` : TASK;
  const args = ['--profile', 'headless', fullPrompt];
  const childEnv = { ...env, DEEPSEEK_API_KEY: LLM_API_KEY, DSH_PERMISSION_MODE: 'danger-full-access' };
  if (LLM_BASE_URL) childEnv.DEEPSEEK_BASE_URL = LLM_BASE_URL;
  let stdout = '', stderr = '', failed = false;
  try {
    stdout = run('dsh', args, { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    failed = true;
    stdout = e.stdout ? e.stdout.toString() : '';
    stderr = (e.stderr ? e.stderr.toString() : '') + '\n' + String(e.message || e);
  }
  const result = stdout.trim() || stderr || '(sem saída)';
  return { status: failed ? 'failed' : 'passed', result, logs: stderr.slice(-8000) };
}

const RUNNERS = { mock: runMock, 'claude-code': runClaudeCode, codex: runCodex,
                 hermes: runHermes, 'deepseek-harness': runDeepseekHarness };

async function main() {
  setupWorkspace();
  const runner = RUNNERS[HARNESS_ID];
  if (!runner) {
    await callback('failed', `harness desconhecido: '${HARNESS_ID}' (opções: ${Object.keys(RUNNERS).join(', ')})`, '', '');
    return;
  }
  const out = runner();
  const diff = gitDiff();
  // DSH não expõe contagem de tokens no modo headless: usage fica null (custo 0 na central)
  await callback(out.status, out.result, diff, out.logs, out.usage);
}

main().catch(async (e) => {
  console.error('erro fatal no job:', e);
  await callback('failed', `Erro fatal no container do job: ${e.message || e}`, '', String(e.stack || e));
  process.exitCode = 1;
});
