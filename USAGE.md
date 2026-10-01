# nkqa — what it does and how to use it

nkqa is a QA teammate for a web app. It keeps a **notebook** about your app, writes **test
scenarios** you approve, runs them in a **real browser**, and keeps the **evidence** — verdict per
step, screenshots, video.

Three ways to use it, all the same core:

| surface | what it is |
|---|---|
| **MCP plugin** | your coding agent (Claude Code / Codex / Cursor) drives it. nkqa calls no model — the thinking runs on your existing subscription. |
| **CLI** | `qa <verb>` in a terminal. Uses the models in `config.yaml` (needs an API key). |
| **Interactive session** | `qa` alone: slash commands plus plain English. Same models. |
| **Desktop app** | a window over the same server. |

---

## 1. The ideas behind it

**The appmap is the memory.** `appmap/*.md`, plain markdown in git — pages, flows, roles, the
sign-in procedure, quirks. Everything else reads it. It grows after every run and you can edit it
by hand; your edits win.

**A scenario is a file, and approval is bound to its content.** `scenarios/<area>/<slug>.md` holds
a title, preconditions, numbered steps with expectations, and out-of-scope items. Approving stores
a hash. Edit the file and the approval goes **stale** — it must be approved again.

| state | meaning |
|---|---|
| `draft` | written, never approved. Will not run. |
| `ok` | approved and unchanged. The only runnable state. |
| `stale` | edited after approval. Will not run. |
| `deprecated` | retired. Will not run. |

**A run leaves evidence.** `runs/<name>/` gets `results.md` and `results.json` (verdict per step),
`steps/*.png`, a video, and either `history.json` (browser-use drove it) or `steps.json` (your
agent drove it, via MCP).

**Verdicts are deterministic.** Any `fail` → the run fails. Any `blocked`, or a step with no
verdict → `blocked`. Never a silent pass. Exit codes: `0` pass, `1` fail, `2` usage/not-found.

**Every expectation is proven by a check.** While it runs a scenario, the agent records a `check`
for each EXPECT: text on the page, the URL, the title, an element or its text. A step cannot be
marked `pass` without one. Names of things a test creates carry `{{unique}}` (e.g.
`QA project {{unique}}`), which becomes a fresh stamp on every run, so reruns never collide.

**The Library is the regression suite.** A run that passed can be **saved to the Library**, which
is a human's call. It is kept only if its recording also replays once by itself. From then on it
**replays with no model**: every recorded step is repeated, each element found again by its
fingerprint (not its old position), and every check re-evaluated. A replay stops at the first
thing that does not hold, and what to do about it (file a bug, re-record, remove) is the QA's
call; nothing heals itself. The recording sits next to the scenario
(`scenarios/<id>.recording.json`) and is bound to its approval: edit the scenario and it leaves
the Library until it is recorded again. Folders are the scenario ids (`auth/login` is in `auth`).

**Three gates that stay human.**

1. **Approval** — no agent can approve a scenario, in any mode.
2. **Permission** — anything irreversible (delete, pay, send, change settings) asks first.
   Answers: allow once / this session / always / deny. Denied is not an error: the step is
   recorded "not tested — permission denied" and testing continues.
3. **Credentials** — the agent asks for a credential *by name* and gets back a placeholder,
   `<secret>password</secret>`. The real value is substituted inside the browser. It never
   reaches the agent, the logs, the run history or the report. Values live in your OS keychain;
   `vault.yaml` holds only names, descriptions and origins.

Under MCP these three appear as **native dialogs on your screen**. In the terminal they are
prompts. Anything unanswered — dialog closed, timed out, client disconnected — counts as **deny**.

**It is not a sandbox.** The permission gate covers actions the agent *declares* risky. It is a
discipline, not a cage. Point it at a dev environment.

---

## 2. Using it from Claude Code (MCP)

Install: see [INSTALL.md](INSTALL.md). Then talk normally, or use the three slash commands.

### Slash commands

| command | what it does |
|---|---|
| `/nkqa:plan <what to test>` | reads the appmap and drafts scenarios. Optional `ticket`, `area`. |
| `/nkqa:run <scenario id>` | executes an approved scenario, step by step, with evidence. |
| `/nkqa:explore <url> <focus>` | freeform testing, no scenario. Read-only unless you grant otherwise. |

### A normal session

```
"set up nkqa for this app, it runs at https://dev.myapp.com"
# ^ also writes AGENTS.md and the MCP configs, so the next agent needs no explaining
/nkqa:plan the login flow
"show me what you drafted"
"approve auth/login"            → dialog on your screen; you confirm
/nkqa:run auth/login            → browser opens; you answer the credential dialog
"what failed?"
"file a bug for step 3"         → preview dialog; you confirm
```

### The tools the agent has

*Workspace* — `workspace_status`, `list_workspaces`, `use_workspace(path)`,
`init_workspace(path, app_name, base_url)`

*Knowledge* — `read_appmap`, `update_appmap(files, message)`

*Scenarios* — `list_scenarios`, `read_scenario(id)`, `write_scenario(draft, force, ticket)`,
`approve_scenario(id)` ← opens your dialog

*Running* — `start_run(scenario_id)`, `start_explore(name, url)`, `browser_state(screenshot)`,
`navigate`, `click(index)`, `type_text(index, text, clear)`, `scroll`, `send_keys`, `go_back`,
`list_tabs`, `switch_tab`, `close_tab`, `wait(seconds)`, `request_permission(key, description)`,
`ask_credential(name)`, `finish_run(steps, summary)`, `finish_explore(summary)`, `abort_run`,
`list_runs`, `read_run(name)`

*Jira* — `read_ticket(key)`, `file_bug(run, step)`

Notes that matter: element indices come from the last `browser_state` and go stale after any
action (the tool refuses a stale index). One run at a time. The workspace is per session — start
your agent in the project folder, or ask it to switch.

---

## 3. Using it from the terminal (CLI)

`qa <verb>`. Needs a model API key in the workspace `.env` (see §5) — this path does the thinking
itself.

### Getting started

| command | what it does |
|---|---|
| `qa init [--app-name N] [--base-url U]` | create the workspace layout here |
| `qa learn <doc.md \| folder>` | build the appmap from an annotated doc (text + screenshots) |
| `qa crawl [--pages N] [--refresh]` | read-only exploration that fills the appmap, writing each page as it finishes |
| `qa correct "<what is actually true>"` | fix what the appmap gets wrong; shows a diff, commits it |

### Scenarios

| command | what it does |
|---|---|
| `qa plan "<ask>" [--ticket KEY] [--area A] [--force]` | draft scenarios (no browser) |
| `qa scenarios` | ids, state, last verdict |
| `qa approve <id>` | review and approve — human only |
| `qa revise <id> "<how>"` | rewrite a scenario; invalidates its approval |

### Running

| command | what it does |
|---|---|
| `qa run <id> [--model M]` | execute an approved scenario |
| `qa explore [url] [focus] [--name N]` | freeform testing, no scenario |
| `qa replay <test \| folder \| run> [--all]` | replay a Library test or folder, or an old recorded run — no model, no cost |
| `qa library` | list the Library, with each test's last result |
| `qa library-replay [test \| folder]` | replay Library tests with no model; all of them when empty |
| `qa library-save <run>` | keep a passing run as a Library test (human only; it must replay once first) |
| `qa suite [tag] [--strict]` | run every approved scenario; one report for CI |
| `qa compare [a] [b]` | what changed between two suite runs |
| `qa list` | recorded runs |
| `qa reflect <run>` | update the appmap from a past run (automatic after each run) |

### Credentials

| command | what it does |
|---|---|
| `qa vault` | what this project needs, what is set, what is granted |
| `qa vault-set <name>` | store one in the OS keychain (prompts; never an argument) |
| `qa vault-rm <name>` | remove it and its grant |
| `qa vault-grant <name> [--scenario S]` | let it be used without asking each time |
| `qa vault-revoke <name>` | withdraw that |

### Jira

| command | what it does |
|---|---|
| `qa connect jira --project KEY` | write the connector into `config.yaml` |
| `qa auth jira [--reset]` | browser sign-in (needs Node.js) |
| `qa ticket <KEY>` | read a ticket; writes nothing |
| `qa file-bug <run> [--step N]` | compose a bug from a failed step; preview, then confirm |

After `qa connect`, restart your MCP client — the session caches `config.yaml` at startup.

### Models and session

| command | what it does |
|---|---|
| `qa models` | roles, providers, which API keys are present |
| `qa set-model --provider anthropic\|openai [--smart ID] [--fast ID]` | point the tiers at a provider |
| `qa mcp [--workspace DIR]` | serve all of this to Claude Code / Codex / Cursor |
| `qa version` | versions |

---

## 4. The interactive session

`qa` with no arguments. It greets you with the state of the workspace, then takes either slash
commands (`/plan`, `/run`, `/scenarios`, …) or plain English ("which scenarios are still drafts?",
"make step 3 stricter"). Every CLI verb above is a slash command here.

Session-only extras:

| command | what it does |
|---|---|
| `/mode ask\|allow\|refuse` | how the agent answers its own permission requests, this session only |
| `/forget` | clear credentials and grants held in memory |
| `/help`, `/exit` | — |

`/mode` never touches credentials or approval, is never saved, and resets to `ask` on every
reconnect. Ctrl+C cancels the running job; Ctrl+D leaves.

---

## 5. The workspace

```
AGENTS.md         the QA role, read by any agent that opens this folder
CLAUDE.md         a pointer to AGENTS.md
.mcp.json         registers the nkqa tools for Claude Code
.cursor/mcp.json  the same, for Cursor
config.yaml       app name, base URL, model roles, connectors
vault.yaml        credential names, descriptions, origins — never values
.env              API keys (gitignored)
appmap/           what the agent knows about your app
scenarios/        one file per scenario
runs/<name>/      results.md · results.json · steps/*.png · videos/ · history.json or steps.json
runs/suite--*/    suite.md + suite.json for CI
chats/            conversations, committed — the reasoning behind a scenario stays reviewable
.nkqa/            machine-local, gitignored
```

The first four are written when the workspace is created — never overwritten, since the folder
is usually the app's own repo. Commit them and a teammate who clones it gets the same setup with
nothing to install.

`config.yaml` in brief:

```yaml
app:
  name: My Shop
  base_url: https://dev.myshop.com

models:                 # five roles, each pointed at one of two tiers
  planner: smart        # drafting and revising scenarios, ingesting docs
  executor: fast        # driving the browser
  reflector: fast       # learning from runs into the appmap
  chat: fast            # the interactive session
  fallback: fast        # cross-provider failover mid-run

aliases:                # change these two and everything moves
  smart: anthropic_claude_sonnet_5
  fast: anthropic_claude_haiku_4_5
  # mixing providers is fine:  smart: openai:gpt-5.1

run:
  max_steps: 30
  headless: false

appmap:
  auto_reflect: true    # learn from every run
  crawl_pages: 15       # page budget for `qa crawl`

mcp:                    # extra tools; jira is written here by `qa connect jira`
  jira:
    command: npx
    args: ['-y', 'mcp-remote', 'https://mcp.atlassian.com/v1/sse']
```

Model names take two forms: browser-use style (`anthropic_claude_haiku_4_5`) or
`provider:exact-id` (`openai:gpt-5.1`), which is passed through verbatim — so a model released
after this build is still reachable.

Keys come from `.env` at startup: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`,
`AZURE_OPENAI_KEY`. `qa models` tells you which are missing. **None of this is needed on the MCP
path** — there your agent does the thinking.

---

## 6. CI

```
qa suite --strict
```
Runs every approved scenario, writes `runs/suite--<stamp>/suite.{md,json}`, exits `0` or `1`.
`qa compare` diffs the two newest suites, so a regression is visible as a change, not a wall of
output. `qa library-replay` replays every Library test with no model at all — free and
repeatable, which is what a release regression should be.

---

## 7. Limits worth knowing

- The approval dialogs need a dialog tool: osascript (macOS), PowerShell (Windows) or `zenity`
  (Linux — install it). Without one, nkqa falls back to Tk, and failing that denies every gate,
  which leaves it read-only.
- One run at a time per workspace; one workspace per agent session.
- An agent-driven run replays only once it is in the Library, since the Library is where its
  recording is kept. A heavily dynamic page (virtualised lists, generated ids) can defeat the
  element fingerprint; that shows up as a clear replay failure to re-record.
- Jira needs Node.js, and the connector must be named exactly `jira`.
- The testing agent can never file bugs on its own: Jira is not exposed to it during runs.
