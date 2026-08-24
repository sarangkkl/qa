# 03 — BrowserSession & the Event Bus: The Nervous System

Package: [browser_use/browser/](../../browser_use/browser/) · Main files: [session.py](../../browser_use/browser/session.py), [events.py](../../browser_use/browser/events.py), [watchdogs/](../../browser_use/browser/watchdogs/)

This is the layer that actually talks to Chrome. It's also where the codebase's most distinctive design choice lives: **almost nothing calls anything directly — everything goes through an event bus.**

## The event bus pattern, from scratch

The library used is [`bubus`](https://github.com/browser-use/bubus). The idea:

1. Someone creates an *event object* (a pydantic model describing a request: "click this node") and **dispatches** it onto the bus.
2. The bus finds every **handler** subscribed to that event type and runs them.
3. The handler's return value becomes the **event result**, which the dispatcher can await.

The idiom you'll see all over the codebase:

```python
event = browser_session.event_bus.dispatch(ClickElementEvent(node=node))
await event                                        # wait for handlers to run
result = await event.event_result(raise_if_any=True)  # get the handler's return value
```

So an event is really an *async function call with extra steps*. Why take the extra steps?

- **Decoupling** — the tools layer posts `ClickElementEvent` without knowing (or importing) the code that performs clicks. You can swap or test either side alone.
- **Observability** — every browser interaction is a typed object flowing through one place. Easy to log, record, or replay.
- **Uniform timeouts** — every event class declares a timeout (overridable via env vars like `TIMEOUT_ClickElementEvent=30`).

## BrowserSession

[`BrowserSession`](../../browser_use/browser/session.py#L134) is a pydantic model that owns four things:

1. **The CDP connection** — the WebSocket to Chrome.
2. **The event bus** — one per session.
3. **The watchdogs** — attached at startup.
4. **Shared state** — current tabs, which tab the agent is focused on, and the cached `selector_map` (element index → DOM node, from chapter 04). The rule (stated in [watchdog_base.py](../../browser_use/browser/watchdog_base.py)): shared state lives on the session, never on a watchdog.

### Lifecycle

Even the session's own public API uses the event idiom. [`start()`](../../browser_use/browser/session.py#L721) is essentially one line — `dispatch(BrowserStartEvent())` — and the real work happens in its handler [`on_BrowserStartEvent()`](../../browser_use/browser/session.py#L778):

1. Attach all watchdogs (so they're listening before anything happens).
2. Get a browser: if you passed a `cdp_url`, connect to that existing Chrome; otherwise dispatch `BrowserLaunchEvent`, which `LocalBrowserWatchdog` handles by spawning a local Chrome subprocess and returning its CDP URL. (There's also a cloud-browser path.)
3. [`connect()`](../../browser_use/browser/session.py#L1831) — open the actual CDP WebSocket.
4. Announce success by dispatching `BrowserConnectedEvent`.

Shutdown: `stop()` disconnects but leaves the browser process alive; `kill()` also terminates the process. If the WebSocket drops mid-run, the session auto-reconnects.

### Targets and sessions (CDP jargon, one more time)

- A [`Target`](../../browser_use/browser/session.py#L73) is one browsing context: a tab, an iframe, or a worker. Identified by `target_id`.
- A [`CDPSession`](../../browser_use/browser/session.py#L88) is an open channel *to* one target — target = phone number, session = active call. Nearly every CDP command takes a `session_id`.

`SessionManager` ([session_manager.py](../../browser_use/browser/session_manager.py)) keeps the registry of targets/sessions up to date by listening to Chrome's own attach/detach notifications, and recovers gracefully when the tab the agent was focused on gets closed. The everyday accessor is [`get_or_create_cdp_session()`](../../browser_use/browser/session.py#L1472); [`cdp_client_for_node()`](../../browser_use/browser/session.py#L3954) gives you the session for *the frame a DOM node lives in* — which is what makes clicking inside iframes work.

## Watchdogs

A **watchdog** is a small service class that subscribes to events and owns one concern. The magic is a naming convention: any method named `on_<EventName>` is auto-registered as a handler for `<EventName>` — see [`BaseWatchdog.attach_handler_to_session()`](../../browser_use/browser/watchdog_base.py#L56). This makes the codebase greppable: to find who handles `ClickElementEvent`, grep for `on_ClickElementEvent`.

All watchdogs are attached in [`attach_all_watchdogs()`](../../browser_use/browser/session.py#L1680). The roster:

| Watchdog | Purpose |
|---|---|
| **`DefaultActionWatchdog`** | The workhorse (3700 lines): performs clicks, typing, scrolling, key presses, uploads, dropdowns, back/forward — all via raw CDP. |
| **`DOMWatchdog`** | The other big one: answers `BrowserStateRequestEvent` by building the DOM snapshot + screenshot (chapter 04). |
| `LocalBrowserWatchdog` | Spawns and kills the local Chrome subprocess. |
| `DownloadsWatchdog` | Watches CDP download events, saves files, auto-downloads PDFs. |
| `SecurityWatchdog` | Enforces `allowed_domains` / `prohibited_domains` on every navigation. |
| `PopupsWatchdog` | Auto-accepts JS `alert`/`confirm`/`prompt` dialogs so they can't freeze the agent. |
| `StorageStateWatchdog` | Persists/restores cookies + localStorage (only attached when configured). |
| `PermissionsWatchdog` | Grants configured browser permissions (clipboard, geolocation, …) on connect. |
| `ScreenshotWatchdog` | Handles `ScreenshotEvent` via CDP `Page.captureScreenshot`. |
| `AboutBlankWatchdog` | Keeps one `about:blank` tab alive (with a DVD-screensaver animation, no less) so the browser never ends up with zero tabs. |
| `RecordingWatchdog` / `HarRecordingWatchdog` | Optional video recording / HAR network capture. |
| `CaptchaWatchdog` | Pauses the agent while a (cloud) CAPTCHA solver works. |
| `CrashWatchdog` | Crash/network-timeout detection — currently disabled in `attach_all_watchdogs()`. |

One robustness detail: every handler is wrapped in a circuit breaker ([watchdog_base.py](../../browser_use/browser/watchdog_base.py#L56)) — if the CDP connection is down, non-lifecycle handlers are skipped or wait for reconnection instead of exploding.

## The event vocabulary

All events live in [browser/events.py](../../browser_use/browser/events.py) and come in two grammatical flavors:

**Commands (imperative — "do this")**, dispatched by tools/agent, each with a timeout:
`NavigateToUrlEvent`, [`ClickElementEvent`](../../browser_use/browser/events.py#L125), `TypeTextEvent`, `ScrollEvent`, `SwitchTabEvent`, `CloseTabEvent`, `ScreenshotEvent`, `BrowserStateRequestEvent`, `GoBackEvent`, `SendKeysEvent`, `UploadFileEvent`, `GetDropdownOptionsEvent`, `SelectDropdownOptionEvent`, …

**Notifications (past tense — "this happened")**, dispatched by watchdogs/session for whoever cares:
`BrowserConnectedEvent`, `TabCreatedEvent`, `TabClosedEvent`, `AgentFocusChangedEvent`, `NavigationCompleteEvent`, `FileDownloadedEvent`, `DialogOpenedEvent`, `BrowserErrorEvent`, …

The tense tells you the direction: commands flow *down* toward Chrome; notifications flow *up and sideways*.

## BrowserProfile — the settings object

[`BrowserProfile`](../../browser_use/browser/profile.py#L574) is one pydantic model holding every knob, grouped roughly into:

- **Where the browser lives**: launch a local Chrome (`executable_path`, `headless`), connect to an existing one (`cdp_url`), or use a cloud browser.
- **Identity & persistence**: `user_data_dir` (a persistent Chrome profile on disk), `storage_state` (cookies/localStorage seed), `user_agent`.
- **Security policy**: `allowed_domains`, `prohibited_domains`, proxy settings.
- **Window & viewport**: `window_size`, `viewport`, device scale.
- **Behavior tuning**: page-load wait times, `wait_between_actions`, iframe depth limits.
- **Extras**: default extensions (uBlock Origin, cookie-banner blocker), video recording, PDF auto-download, element highlighting.

```python
from browser_use import Agent, BrowserProfile

agent = Agent(
	task='...',
	llm=llm,
	browser_profile=BrowserProfile(headless=False, allowed_domains=['*.github.com']),
)
```

The same file also contains the long curated list of Chrome command-line flags the library launches with (largely inherited from Playwright's battle-tested set).

**Next:** [04 — DOM to text](04-dom-to-text.md) — how `DOMWatchdog` turns a webpage into something an LLM can read.
