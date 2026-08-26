# Phase 3 Plan — MCP Connectors + Jira Read

Status: agreed plan, not yet started · Written 2026-08-26
Read [ARCHITECTURE.md](ARCHITECTURE.md) §7 first; Phases 1–2 are shipped (see their plan docs).
Self-contained so a future session can execute it cold.

---

## 0. Context recap (for future sessions)

Shipped so far: `nkqa` package with the full plan → approve (hash-bound) → run
(per-step verdicts) loop, plus freeform `qa explore` and LLM-free `qa replay`. The
planner (`nkqa/planner.py`) is a direct LLM call that assembles context from
appmap + existing scenarios + past verdicts; `ticket:` in scenario frontmatter is
free text today. Verified live end-to-end 2026-08-26 (see PHASE-2 plan §5).

Phase 3 makes the product a team citizen: **MCP is the extensibility mechanism**
("give the testing agent new tools" = add a server to config.yaml), and **Jira is the
first connector** — read stories to plan from, file bugs only when the human says so.

Decisions made by the user (2026-08-26):
1. **Jira via the official Atlassian remote MCP server** (`https://mcp.atlassian.com/v1/sse`),
   bridged to stdio with `npx -y mcp-remote <url>` since browser-use's `MCPClient` is
   stdio-only (verified: `browser_use/mcp/client.py` has `_run_stdio_client` only).
   mcp-remote handles the OAuth browser login and caches tokens in `~/.mcp-auth`.
   Requires Node.js on the machine.
2. **Generic MCP wiring ships now**: every server in config.yaml `mcp:` can expose its
   tools to the executor agent — Jira is just the first entry, not a special case.
3. **Live test target exists**: the user has a Jira site + project; get the site URL,
   a sample ticket key, and the project key for bugs at implementation time.

Verified API surface (browser-use 0.13.8, `browser_use/mcp/client.py`):
`MCPClient(server_name, command, args, env)` · `await connect()` / `disconnect()` /
async context manager · `await register_to_tools(registry, prefix?, tool_filter?)`
turns each MCP tool into an agent action. For deterministic (non-agent) calls use the
underlying `client.session.call_tool(name, args)` — confirm the exact session attribute
when implementing. The official Atlassian server's tool names include `getJiraIssue`,
`searchJiraIssuesUsingJql`, `createJiraIssue`, `addCommentToJiraIssue`,
`getVisibleJiraProjects`, `atlassianUserInfo` (same names as the claude.ai connector).

## 1. Config schema additions

```yaml
mcp:
  jira:                      # name is the registry prefix for its tools
    command: npx
    args: ['-y', 'mcp-remote', 'https://mcp.atlassian.com/v1/sse']
    expose_to_executor: false   # SAFETY: the testing agent must NOT get createJiraIssue
  test-data:                 # example customer server: anything goes
    command: node
    args: ['./tools/seed-server.js']
    env:
      SEED_KEY: env:SEED_KEY    # 'env:NAME' reads from the environment/.env at launch;
                                # literal secrets in config.yaml are forbidden (CLAUDE.md)
jira:
  project: PROJ              # default project key for qa file-bug
```

Defaults: `expose_to_executor: true` for generic servers, but the **bundled `jira`
preset defaults to `false`** — bug filing is human-instructed only (architecture §7);
an executor with `createJiraIssue` could file bugs autonomously, which breaks rule 3
of the product's trust story. `qa init` writes the jira block commented out.

## 2. New modules

```
nkqa/
  mcp.py         # config -> ServerSpec list; connect/disconnect lifecycle;
                 #   register_to_executor(registry); call(server, tool, args) for
                 #   deterministic calls; resolve env: indirection
  jira.py        # thin Jira verbs over mcp.call: fetch_issue(key) -> IssueBrief
                 #   (summary, description, acceptance criteria, status, url);
                 #   create_bug(project, summary, description) -> issue key
  execution/     # (edit) scenario_runner + runner: register exposed MCP tools
  planner.py     # (edit) --ticket: prepend '### Ticket <key>' section to context,
                 #   stamp ticket: <key> into drafted frontmatter
  cli.py         # (edit) qa plan --ticket KEY · qa file-bug <run> [--step N]
```

`jira.py` contains no HTTP code — only MCP tool calls + response shaping. If a tool
name differs on some server, the names are overridable under `mcp.jira` config keys
(`issue_tool`, `create_tool`) with the official names as defaults.

## 3. Behaviors

**`qa plan --ticket PROJ-123 ["extra ask"]`** — connect the jira server, fetch the
issue, inject a `### Ticket PROJ-123` section (summary, description, acceptance
criteria) into the planner context, and stamp `ticket: PROJ-123` in every drafted
scenario. The ask argument becomes optional when --ticket is given (default ask:
"verify this ticket's acceptance criteria"). Clear error + exit 2 if the server is
unreachable or the key doesn't resolve (surface the OAuth hint: first use opens a
browser login via mcp-remote).

**`qa file-bug <run-name> [--step N] [--project KEY]`** — human-instructed only:
1. Load the run's results.md + its scenario; pick the failed/blocked step(s)
   (--step to choose when several failed; refuse politely if the run was all-pass).
2. Compose the bug: summary `[scenario-id] step N: <step action> failed`;
   description with repro steps (steps 1..N), expected (the step's Expect) vs actual
   (the verdict note), scenario id + approved hash, run dir path, app base URL,
   and the source ticket link if the scenario has one.
3. Show the full preview, confirm y/N (same pattern as qa approve).
4. Create via MCP; print the new issue key; append `Filed: <KEY> <date>` to the
   run's results.md for traceability.
Attachments: the official server has no attachment-upload tool — evidence stays
local and the bug references it. Revisit when the server grows attachment support.

**Executor tool exposure** — in `scenario_runner` and `run_freeform`: connect every
config'd server with `expose_to_executor: true`, `register_to_tools` into the agent's
registry (tool names prefixed with the server name), disconnect in a finally. Zero
servers configured = exactly today's behavior. The QA_RULES system message gains one
line: external tools are for test setup/verification, and request_permission still
gates anything destructive — including MCP tool calls.

## 4. Implementation order (each step green on ./check.sh)

1. Config: `mcp:` + `jira:` parsing, env: indirection, expose flag + tests.
2. `nkqa/mcp.py` lifecycle + deterministic call. Test against a tiny stdio MCP
   fixture server written with the `mcp` python SDK (already a browser-use dep) —
   real client, no network, no mocks.
3. `jira.py` (fetch_issue/create_bug shaping) + tests with a fake call function.
4. `qa plan --ticket` + tests (planner context injection, frontmatter stamping).
5. Executor wiring + tests (fixture server tool appears in registry; jira excluded).
6. `qa file-bug` + tests (composition from a fake failed run, confirm flow, refusal
   on all-pass runs).
7. Live verification with the user's Jira (needs their site URL + ticket + project
   key, Node.js present, one OAuth login): `qa plan --ticket` on a real story, then
   `qa file-bug` into their project. README + ARCHITECTURE §7 updates. Commit per step.

## 5. Definition of done

- A workspace with no `mcp:` config behaves exactly as Phase 2 (tests prove it).
- Fixture MCP server's tools appear as executor actions when exposed, and never
  appear for `expose_to_executor: false` (tests).
- Live: `qa plan --ticket <real key>` drafts scenarios that cite the ticket;
  `qa file-bug` files a real bug from a failed run with a correct repro description,
  and the run's results.md records the filed key. (User-run: needs OAuth + their site.)
- `./check.sh` green throughout; no new Python dependencies (mcp SDK ships with
  browser-use; mcp-remote arrives via npx at runtime).

## 6. Out of scope for Phase 3

Autonomous bug filing (never) · Jira attachments (server has no upload tool yet) ·
Xray/Zephyr test-cycle management (non-goal, ARCHITECTURE §10) · Confluence/doc
ingestion (Phase 4) · ticket-status transitions or comments on stories (later, with
reflection) · SSE/HTTP MCP transport of our own (mcp-remote bridges it).
