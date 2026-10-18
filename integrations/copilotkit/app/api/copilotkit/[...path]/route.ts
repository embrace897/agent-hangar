// The CopilotKit runtime, on the server. It talks AG-UI to one Agent Hangar agent; the invoke key stays here and
// never reaches the browser.
import { HttpAgent } from "@ag-ui/client";
import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";

const base = (process.env.HANGAR_URL ?? "http://localhost:8090").replace(/\/$/, "");
const gateway = process.env.HANGAR_GATEWAY ?? "/gw";
const slug = process.env.HANGAR_AGENT ?? "my-agent";

const runtime = new CopilotRuntime({
  agents: {
    hangar: new HttpAgent({
      url: `${base}${gateway}/${slug}/agui`,
      headers: { Authorization: `Bearer ${process.env.HANGAR_KEY ?? ""}`, "X-Channel": "copilotkit" },
    }),
  },
});

const handler = createCopilotRuntimeHandler({ runtime, basePath: "/api/copilotkit" });

export { handler as GET, handler as POST, handler as PATCH, handler as DELETE };
