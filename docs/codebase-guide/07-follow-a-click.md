# 07 — Follow a Click: One Action, End to End

You've now met every layer. Time to watch them work together. The LLM has just looked at a login page and returned:

```json
{
  "thinking": "The sign-in button is element 5...",
  "next_goal": "Click the sign-in button",
  "action": [{"click": {"index": 5}}]
}
```

Here is everything that happens before the button is actually pressed.

```mermaid
sequenceDiagram
    participant L as LLM
    participant A as Agent<br/>(agent/service.py)
    participant T as Tools<br/>(tools/service.py)
    participant R as Registry<br/>(tools/registry/)
    participant Bus as Event Bus
    participant W as DefaultActionWatchdog
    participant C as Chrome (CDP)

    L->>A: AgentOutput{action: [click(index=5)]}
    A->>T: multi_act → tools.act(action, browser_session, ...)
    T->>R: execute_action('click', {index: 5})
    R->>R: validate params, inject browser_session
    R->>T: _click_by_index(index=5, ...)
    T->>T: selector_map[5] → EnhancedDOMTreeNode
    T->>Bus: dispatch(ClickElementEvent(node))
    Bus->>W: on_ClickElementEvent(event)
    W->>C: DOM.getContentQuads (where is it?)
    W->>C: Input.dispatchMouseEvent ×3<br/>(mouseMoved, mousePressed, mouseReleased)
    C-->>W: click metadata
    W-->>Bus: return value = event result
    Bus-->>T: await event.event_result()
    T-->>A: ActionResult("Clicked element 5...")
    A->>A: record in history
    Note over A,L: next step: result appears in &lt;agent_history&gt;
```

## The trace, step by step

**1. The agent hands the action to the tools layer.**
`multi_act()` loops over the action list and calls [`tools.act(action, browser_session, ...)`](../../browser_use/agent/service.py#L2785) — passing along everything the action might need (browser session, file system, sensitive data). Chapter 02.

**2. Tools finds the action and applies a timeout.**
[`Tools.act()`](../../browser_use/tools/service.py#L2168) unpacks `{"click": {"index": 5}}` into name + params and wraps everything in a wall-clock timeout. Chapter 05.

**3. The registry validates and injects.**
[`Registry.execute_action()`](../../browser_use/tools/registry/service.py#L331) validates `{index: 5}` into the click action's pydantic param model, substitutes any `<secret>...</secret>` placeholders, and injects the special parameters (here: `browser_session`) before calling the registered function. Chapter 05.

**4. The action resolves the index to a real element.**
[`_click_by_index()`](../../browser_use/tools/service.py#L704) calls [`get_element_by_index(5)`](../../browser_use/browser/session.py#L2480), which reads the cached `selector_map` built during the last DOM serialization — index 5 becomes an `EnhancedDOMTreeNode` with tag, attributes, frame, and absolute position. If the map is stale (page changed), this returns `None` and the LLM gets a "page may have changed" error instead. Chapter 04.

It also snapshots the current tab list (to detect "the click opened a new tab" afterward) and fires a highlight animation for anyone watching a headful browser.

**5. The click becomes an event.**
```python
event = browser_session.event_bus.dispatch(ClickElementEvent(node=node))
await event
click_metadata = await event.event_result(raise_if_any=True)
```
The tools layer neither knows nor cares how clicking works. Chapter 03.

**6. The watchdog picks it up.**
The bus routes to [`DefaultActionWatchdog.on_ClickElementEvent()`](../../browser_use/browser/watchdogs/default_action_watchdog.py#L337) (found by the `on_<EventName>` convention, wrapped in the CDP circuit breaker). It refuses file inputs (uploads have their own action), special-cases print buttons, and wraps the click in download detection — if the click starts a file download, that's awaited and reported.

**7. Raw CDP does the deed.**
The implementation (`_click_element_node_impl`, same file):

- `cdp_client_for_node(node)` — get the CDP session for **the frame the element lives in** (this is why iframe clicks work).
- `DOM.getContentQuads` — ask Chrome for the element's on-screen quadrilaterals; pick a visible one; compute its center; clamp to the viewport.
- **Occlusion check** — is another element covering that point? If so, fall back to a JavaScript `element.click()` via `Runtime.callFunctionOn` instead of a mouse event.
- Otherwise, the real thing — three `Input.dispatchMouseEvent` calls: `mouseMoved`, then `mousePressed`, then `mouseReleased`, exactly like a human's click. Response timeouts here are *tolerated*, because a click that opens a modal dialog legitimately blocks Chrome's reply.
- For checkboxes/radios: verify the `checked` state actually toggled; retry via JS if not.

**8. The result bubbles all the way back.**
The watchdog's return value becomes the event result → `_click_by_index` builds an `ActionResult(extracted_content='Clicked element 5 ...')` (noting a new tab if one opened) → `multi_act` collects it → `_finalize` records it in `agent.history` → next step, the `MessageManager` renders it as a line inside `<agent_history>`, and the LLM — seeing a fresh DOM where the login form is gone — concludes the click worked.

That's the entire machine: **text in, JSON out, events down, results up.** Every other action (`input`, `scroll`, `extract`, …) follows the same path with a different watchdog handler at the bottom.

## Where to go next

- **Watch it live**: run any example with `BrowserProfile(headless=False)` and follow the log lines — you'll now recognize every phase.
- **[examples/getting_started/](../../examples/getting_started/)** 01→05, then [examples/custom-functions/](../../examples/custom-functions/) to build your own actions.
- **[kt/00-INDEX.md](../../kt/00-INDEX.md)** — the senior-engineer architecture notes. After this guide, you're ready for them; [kt/08-lifecycle-trace.md](../../kt/08-lifecycle-trace.md) is a denser version of this very chapter.
- **The tests** — [tests/ci/](../../tests/ci/) shows how every feature is exercised against a local test server; reading a test is often the fastest way to learn an API's contract.
