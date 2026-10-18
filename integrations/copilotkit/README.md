# CopilotKit example: an Agent Hangar agent inside your app

A small Next.js app with an Agent Hangar agent inside it. The app talks to the agent over the
[AG-UI protocol](https://github.com/ag-ui-protocol/ag-ui), through [CopilotKit](https://www.copilotkit.ai) (v2 API).
The screen is an order card plus a chat. It shows three things:

- **Streaming chat.** The agent's answer arrives word by word.
- **A tool of your app.** `open_order` is declared in the browser with `useFrontendTool`. The agent asks for it, the
  browser runs it (here it highlights the order), and the agent continues with the result.
- **State shared with the screen.** The order (`{ order: { id, item, status } }`) is AG-UI state. The agent changes it
  with JSON Patch (`STATE_DELTA`), and the screen follows.

```
browser (CopilotChat, useFrontendTool, useAgent)
   │  /api/copilotkit  (same origin; no key in the browser)
   ▼
Next.js server: CopilotRuntime + HttpAgent ──AG-UI, invoke key──▶ Agent Hangar /gw/<agent>/agui
                                                                     │ budget, audit, usage per channel
                                                                     ▼
                                                     the agent (chat agent, Digital employee, harness or remote)
```

The invoke key lives only in the Next.js server's environment. The browser talks to `/api/copilotkit` on its own
origin, so the hangar needs no CORS setting.

## Run it

1. In Agent Hangar, pick an agent that is running in production, or in stage (see `HANGAR_GATEWAY` below).
2. Create an **invoke** key scoped to that agent, under **Keys**.
3. Configure and start the app:

```bash
cp .env.example .env.local    # then fill in HANGAR_URL, HANGAR_AGENT and HANGAR_KEY
npm install
npm run dev                   # http://localhost:3000
```

Or run it as a container:

```bash
docker build -t agent-hangar/copilotkit-example .
docker run -p 3000:3000 -e HANGAR_URL=http://host.docker.internal:8090 -e HANGAR_AGENT=my-agent -e HANGAR_KEY=hk_... agent-hangar/copilotkit-example
```

| Variable | What it is |
|---|---|
| `HANGAR_URL` | The hangar address, as the Next.js server sees it. |
| `HANGAR_AGENT` | The agent's slug. |
| `HANGAR_KEY` | An invoke key for that agent. Server-side only. |
| `HANGAR_GATEWAY` | `/gw` (production, the default) or `/gw-stage` (the stage version). |

Ask, for example, "open order 4471" or "mark the order as paid". The agent sees the app's tools and the current state
on every turn. In the hangar, a chat agent receives the state together with an `app_state_update` tool, and it uses
that tool to change the state.

## The parts that matter

- [`app/api/copilotkit/[...path]/route.ts`](app/api/copilotkit/[...path]/route.ts) is the CopilotKit runtime with
  one `HttpAgent` pointing at `/gw/<agent>/agui`, with the key in an `Authorization` header and `X-Channel:
  copilotkit`, so usage shows up per channel in the hangar.
- [`app/page.tsx`](app/page.tsx) holds `CopilotKit`, `CopilotChat`, `useFrontendTool` (the app tool) and `useAgent`
  (the shared state).

## With a Digital employee

Point `HANGAR_AGENT` at a Digital employee and each conversation becomes a task under its authority. When an action
needs approval, the run ends with an AG-UI interrupt and the request shows on the screen. The app's key cannot
approve it: the decision stays with the person the authority names, in the hangar, Slack or Teams. The app then
resumes where it stopped. See [docs/clients.md](../../docs/clients.md).

## Tested in CI

`tests/e2e/copilotkit.py` builds this app, runs it against a real hangar (mock mode with a fake LLM) and drives it
with Playwright. It checks the streamed answer, the app tool round trip, the state change on screen, that the key never
appears in a browser request, and that usage is recorded under the `copilotkit` channel.
