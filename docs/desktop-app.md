# Hangar desktop app

[Hangar](https://github.com/valteresj2/hangar) is a desktop app for running and steering Claude Code sessions on a
person's computer, inspired by Claude Desktop. Claude Code ships inside the app. Connected to an Agent Hangar
platform, it:

- signs in with the person's company account;
- can use the token models the admins sync;
- shows the member's workspace (home, their agents, decisions and inbox) with live updates.

The platform side of the contract is in [app-hangar.md](app-hangar.md) (sign-in and devices),
[app-hangar-models.md](app-hangar-models.md) (synced token models) and
[app-hangar-workspace.md](app-hangar-workspace.md) (workspace and events).

## Installing

Download the app from the [Hangar releases](https://github.com/valteresj2/hangar/releases):

- Windows: installer or portable `.exe`;
- macOS: `.dmg`;
- Linux: AppImage or `.deb`.

The builds are not code-signed yet:

- **Windows:** SmartScreen warns about the app; choose **More info → Run anyway**.
- **macOS:** right-click the app in Applications and choose **Open**.

The app's interface is in Portuguese for now.

## Connecting the app

1. In the app, open the settings, enter the platform URL (the same address as the console) and choose **Connect**.
2. The browser opens the platform's sign-in and consent screen. The app asks for the `app` scope (its own API) and
   the `mcp` scope (the platform tools). After consent the app receives an OAuth token on a loopback address and
   registers the device.
3. The app sends a heartbeat while it runs. Revoking the device, revoking the app in *API keys → Connected apps* or
   deactivating the person disconnects the app right away.

Admins can set the platform for everyone with a Windows policy, which takes precedence over the app settings:
`HKLM\Software\Policies\Hangar` (then `HKCU\Software\Policies\Hangar`), values `PlatformUrl` and `Mode`
(`off`, `optional` or `required`). With `required`, the app creates no sessions until it is connected.

The `app` scope opens `/api/app/*` and an explicit list of member routes, always with the person's own access; it
never opens admin, settings, keys or OAuth routes, and `/mcp` needs the `mcp` scope. MCP clients that ask for no
scope (Claude.ai, ChatGPT) keep getting `mcp` tokens, as before.

## In the console

**App Hangar** (admins) has two pages:

- **Overview**: connected people and devices, the platform URL to give people, how to connect, the Windows policy and
  the app versions in use.
- **Devices**: every registered device with its person, version and last heartbeat, and a **Revoke** button (it
  also ends the device's sign-in).

## Synced token models

People can run a session either on their own Claude subscription or on a token model the company pays for. To offer
a model in the app:

1. In **LLM Providers**, open an LLM connection that speaks the Anthropic protocol (the only format Claude Code uses).
2. Choose **Sync token models with Hangar desktop**, then hide any models people shouldn't pick. With the code
   policy set to *approved connections only*, the connection must allow code.

The app calls the models through the central's LLM proxy (`/api/app/llm/{connection}/v1/messages`) with a key bound
to the device. The provider key stays in the platform's vault, and the proxy enforces:

- hidden models;
- unsynced connections;
- the code policy;
- team and company budgets.

Each call is recorded in usage with its tokens (cache included) and cost. A person in several teams is billed to their
first team. Request and response contents are never logged.

## Workspace and live events

The app's home and *My agents* use the same data as the member portal. `GET /api/app/events` streams changes to the
inbox, decisions, agent status and the model list over Server-Sent Events, and the app refreshes when it reconnects.

Behind a reverse proxy, turn buffering off and allow a read timeout of at least 60 seconds on `/api/app/events`
(the central already sends `X-Accel-Buffering: no` for nginx).

## Upgrading

Version 0.53 adds migrations `0052`, `0053` and `0054`, which run on startup. Back up the database first, as for any
upgrade (see [backup.md](backup.md)).
