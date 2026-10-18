"use client";
// One screen of "your app" with an Agent Hangar agent inside it, over AG-UI:
//  - the chat streams the agent's answer;
//  - `open_order` is a tool of this app: the agent asks for it, the browser runs it and returns the result;
//  - the order is state shared with the agent: the agent changes it (STATE_DELTA, JSON Patch) and the screen follows.
import { CopilotChat, CopilotKit, useAgent, useFrontendTool } from "@copilotkit/react-core/v2";
import { useEffect, useState } from "react";
import { z } from "zod";

type Order = { id: string; item: string; status: string };
type AppState = { order?: Order };

const AGENT = "hangar";
const FIRST: AppState = { order: { id: "4471", item: "T-shirt · $64", status: "open" } };

function Orders() {
  const { agent } = useAgent({ agentId: AGENT });
  const [opened, setOpened] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);

  useEffect(() => {
    if (!agent.state || !(agent.state as AppState).order) agent.setState(FIRST);
  }, [agent]);

  useFrontendTool({
    name: "open_order",
    description: "Opens an order on the user's screen, by id.",
    parameters: z.object({ id: z.string().describe("the order id") }),
    handler: async ({ id }) => {
      setOpened(id);
      setLog((l) => [...l, `open_order(${id})`]);
      return `order ${id} opened on the screen`;
    },
  });

  const order = (agent.state as AppState)?.order ?? FIRST.order!;
  return (
    <main className="orders">
      <h1>Orders</h1>
      <p className="hint">Try: “open order 4471” or “mark the order as paid”.</p>
      <section className={`card${opened === order.id ? " open" : ""}`} data-testid="order">
        <div className="label">ORDER #{order.id}</div>
        <div className="title">{order.item}</div>
        Status: <span className={`pill ${order.status}`} data-testid="status">{order.status}</span>
      </section>
      <div className="log" data-testid="tool-log">{log.join(" · ")}</div>
    </main>
  );
}

export default function Page() {
  return (
    <CopilotKit runtimeUrl="/api/copilotkit">
      <div className="layout">
        <Orders />
        <aside className="chat">
          <CopilotChat agentId={AGENT} />
        </aside>
      </div>
    </CopilotKit>
  );
}
