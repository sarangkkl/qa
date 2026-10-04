<img src="brand/kiwame-logo.svg" alt="Kiwame" height="64">

**The AI QA engineer that knows your app end to end.** *Kiwame* (極め) is the Japanese word for an
expert's final verdict: the certificate a master appraiser wrote to prove a sword genuine.

The command is `kiwame`; `qa` still works as a short alias.

AI QA teammate, CLI-first. It records AI-driven browser tests with a human in the loop
(asks when unsure, collects credentials without seeing them, requests permission before
anything dangerous), saves full evidence (video, gif, transcript, structured history),
and replays recordings deterministically without an LLM.

Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Current phase: [docs/PHASE-1-PLAN.md](docs/PHASE-1-PLAN.md)

## Install (dev)

```bash
python -m venv venv && venv/bin/pip install -e .
```

Put API keys in `.env` (e.g. `ANTHROPIC_API_KEY=...`).

## Quickstart

```bash
qa                               # interactive session (the main way to use it)
kiwame init                          # create a QA workspace in the current directory
kiwame learn appdoc.md               # build the appmap from an annotated doc (screenshots + text)
kiwame plan "the checkout flow"      # planner drafts scenarios from app knowledge (no browser)
kiwame plan --ticket PROJ-123        # plan from a Jira story's acceptance criteria (via MCP)
kiwame scenarios                     # list scenarios: status + last verdict
kiwame approve checkout/coupon       # review + approve (hash-bound: edits invalidate it)
kiwame run checkout/coupon           # execute in a real browser -> per-step verdicts + results.md
kiwame explore https://your-app.com "checkout flow"   # freeform AI-driven testing, no scenario
kiwame list                          # all recorded runs
kiwame replay <run-name>             # rerun WITHOUT the LLM - free and repeatable
kiwame replay --all                  # replay every recording (no LLM, no browser decisions)
kiwame suite [--tag regression]      # run every APPROVED scenario -> one report, CI exit codes
kiwame compare                       # what changed between the two newest suite runs
kiwame vault                         # credentials this project needs: set? granted? bound to what?
kiwame connect jira [--project KEY]  # set Jira up in config.yaml (then: kiwame auth jira)
kiwame ticket PNY-3689               # read a ticket (a browse URL works too) - writes nothing
kiwame auth jira                     # OAuth sign-in for a configured connector
kiwame file-bug <run> [--step N]     # file a Jira bug from a failed run - always human-confirmed
kiwame reflect <run>                 # update the appmap from a past run (automatic after runs)
kiwame crawl [--refresh]             # explore the live app read-only; skips pages already mapped
kiwame correct "<what is true>"      # fix what the app map gets wrong, in your own words
kiwame revise <id> "<how>"           # rewrite a scenario (invalidates its approval)
kiwame models                        # roles -> models -> providers -> are the API keys set?
kiwame set-model --provider openai   # switch provider (or --smart/--fast for one tier)
```

### Suites and CI

`kiwame suite` runs every **approved** scenario, writes one report, and exits the way CI wants
(0 all passed · 1 something failed · 2 nothing ran). The suite *is* the approved set: a
draft was never approved, so it is reported as "not in the suite" rather than failed on -
and `--strict` fails on that too, which is what you want on a build server. A scenario
edited after approval goes STALE and leaves the suite until someone re-approves it.

Every suite is compared against the previous one, so the report leads with the line that
matters: **newly failing**. `kiwame compare` does the same for any two suite runs.

Credentials come from the vault, which is what makes an unattended run possible at all -
otherwise the first login prompt stops the build. See `.github/workflows/qa-suite.yml.example`.

### Credentials

```
kiwame vault                    # what this project needs · what is set · what is granted
kiwame vault set <name>         # store one in the OS keychain (prompts; never an argument)
kiwame vault grant <name> [--scenario ID]  ·  kiwame vault revoke <name>
```

`vault.yaml` is committed and declares the *names*, descriptions and origins - never
values. Clone the repo, run `kiwame vault`, and you are told exactly what to supply. Values
live in your OS keychain, or in `NKQA_SECRET_<NAME>` for CI.

Each credential is bound to an `origin`, and that binding is enforced twice: the value is
not read out of the keychain while the browser is elsewhere, and browser-use will not type
it on a non-matching page. A run that gets redirected cannot leak production credentials
into someone else's form. There is deliberately no `kiwame vault get`.

### The interactive session

`qa` with no arguments opens a Claude-Code-style session: it greets you with the state
of your QA world (appmap size, scenarios by status, recent runs, active models), then
takes either slash commands (`/plan`, `/run`, `/scenarios`, `/revise`, `/help` - instant,
no LLM cost) or plain English ("which scenarios are still drafts?", "make step 3
stricter"). Credentials and permission prompts appear inline; a credential typed once is
reused for the session, never written to disk, and `/forget` clears it. Ctrl+C cancels
whatever is running and returns to the prompt; Ctrl+D or `/exit` leaves.

**The chat agent cannot approve scenarios.** Ask it to and it hands the keystroke back to
you (`/approve <id>`) - approval is the gate everything else rests on, so it is enforced
structurally, not by asking the model nicely.

The appmap (`appmap/`) is the product's memory: plain markdown in git, no vector DB.
It's seeded by `kiwame learn` from a document you write - screenshots with a line or two
about each screen, roles, and flows - then grows automatically after every run
(git-committed as `appmap: learned from <run>`; disable with `appmap.auto_reflect: false`).
The planner reads all of it, so everything the map knows shows up in better scenarios.

`kiwame crawl` fills it in from the live app, **writing each screen as it finishes with it** -
one file, one commit, before it moves on. Stop a crawl and you keep every page it had
already understood. It also skips what is already documented, so a second crawl spends its
budget on new ground and never overwrites a page you wrote by hand (`--refresh` when the app
really has changed).

Ask the chat agent about the app and it answers from the map - and tells you when the map
does not cover something instead of guessing. When it has something wrong, say so:

    kiwame correct "client rows are clickable, they open /clients/<id>"

It shows the diff and commits it as `appmap: corrected by you`, so `git revert` undoes a
correction that came out wrong. Your edit wins over anything the agent learned on its own.

**Setting Jira up:** `kiwame connect jira --project PROJ` writes the server into `config.yaml`, then
`kiwame auth jira` does the OAuth. Both are in the desktop's Settings page too — a **Connect Jira**
button and **Sign in / check**. Until a connector named exactly `jira` exists, planning from a
ticket and filing a bug are hidden rather than present-and-broken.

Once it is connected, paste a ticket or its URL into the chat and the agent **reads it before it
writes anything** — what the ticket asks for, which screens in the app map it touches, and the
questions a QA would ask about what is ambiguous. Scenarios get drafted once you have answered.
`kiwame ticket PNY-3689` is the same read on its own, and writes nothing either way.

Extra tools for the agent come from MCP servers listed in `config.yaml` (`mcp:` section):
any server's tools can be exposed to the executor (test-data seeders, OTP readers, ...).
Jira is just the first connector - and it is *not* exposed to the executor, so bugs are
only filed when you run `kiwame file-bug`. Jira needs Node.js (`npx mcp-remote` bridges
Atlassian's remote MCP; first use opens an OAuth login in your browser).

The gate: `kiwame run` refuses drafts, deprecated scenarios, and scenarios edited after
approval (stale hash). A human approval is always in the loop before a browser moves.

### Drive it from your coding agent (MCP)

If you already pay for Claude Code, Codex or Cursor, that agent can be the QA engineer and
Kiwame the hands. `qa mcp` serves the workspace over MCP; **Kiwame makes zero model calls on this
path** - the agent reads the appmap, writes the scenarios, drives the browser one action at a
time and reports the verdicts. Register it once:

    claude mcp add nkqa -- /path/to/venv/bin/qa mcp        # Claude Code
    codex mcp add nkqa -- /path/to/venv/bin/qa mcp         # Codex
    # Cursor: .cursor/mcp.json -> {"mcpServers": {"nkqa": {"command": "/path/to/venv/bin/qa", "args": ["mcp"]}}}

Then, inside a project that has a workspace (or after `use_workspace`), `/nkqa:plan the checkout
flow` drafts scenarios and `/nkqa:run checkout/coupon` executes one. The gates stay yours:
**approvals, permission requests and credentials open as native dialogs on your screen**, never
in the agent's chat, so the agent cannot approve its own scenario, grant itself a risky action,
or ever see a password (it types the `<secret>name</secret>` placeholder; the value is filled in
inside the browser). Evidence lands under `runs/<name>/` as `steps.json`, `steps/*.png` and the
video, and the desktop app shows it like any other run.

## Workspace

```
config.yaml     # app URL, model roles (planner/executor/reflector/fallback), aliases
vault.yaml      # credential names, descriptions, origins - never values
appmap/         # what the agent knows about your app (grows over time)
scenarios/      # one markdown file per scenario; approval bound to a content hash
chats/          # conversations, committed: the reasoning behind a scenario is reviewable
runs/<name>/    # evidence per run: results.md, history.json (or steps.json + steps/ from `qa mcp`), videos/, gif
runs/suite--*/  # suite.md + suite.json: one report per suite run, for CI to read
.nkqa/          # gitignored, machine-local
```

## Development

```bash
./check.sh       # ruff + pyright strict + pytest - the definition of "passing"
./check.sh fix   # auto-format first
```

Conventions live in [CLAUDE.md](CLAUDE.md).
