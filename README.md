# NKQA

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
qa init                          # create a QA workspace in the current directory
qa learn appdoc.md               # build the appmap from an annotated doc (screenshots + text)
qa plan "the checkout flow"      # planner drafts scenarios from app knowledge (no browser)
qa plan --ticket PROJ-123        # plan from a Jira story's acceptance criteria (via MCP)
qa scenarios                     # list scenarios: status + last verdict
qa approve checkout/coupon       # review + approve (hash-bound: edits invalidate it)
qa run checkout/coupon           # execute in a real browser -> per-step verdicts + results.md
qa explore https://your-app.com "checkout flow"   # freeform AI-driven testing, no scenario
qa list                          # all recorded runs
qa replay <run-name>             # rerun WITHOUT the LLM - free and repeatable
qa replay --all                  # replay every recording (no LLM, no browser decisions)
qa suite [--tag regression]      # run every APPROVED scenario -> one report, CI exit codes
qa compare                       # what changed between the two newest suite runs
qa vault                         # credentials this project needs: set? granted? bound to what?
qa connect jira [--project KEY]  # set Jira up in config.yaml (then: qa auth jira)
qa ticket PNY-3689               # read a ticket (a browse URL works too) - writes nothing
qa auth jira                     # OAuth sign-in for a configured connector
qa file-bug <run> [--step N]     # file a Jira bug from a failed run - always human-confirmed
qa reflect <run>                 # update the appmap from a past run (automatic after runs)
qa crawl [--refresh]             # explore the live app read-only; skips pages already mapped
qa correct "<what is true>"      # fix what the app map gets wrong, in your own words
qa revise <id> "<how>"           # rewrite a scenario (invalidates its approval)
qa models                        # roles -> models -> providers -> are the API keys set?
qa set-model --provider openai   # switch provider (or --smart/--fast for one tier)
```

### Suites and CI

`qa suite` runs every **approved** scenario, writes one report, and exits the way CI wants
(0 all passed · 1 something failed · 2 nothing ran). The suite *is* the approved set: a
draft was never approved, so it is reported as "not in the suite" rather than failed on -
and `--strict` fails on that too, which is what you want on a build server. A scenario
edited after approval goes STALE and leaves the suite until someone re-approves it.

Every suite is compared against the previous one, so the report leads with the line that
matters: **newly failing**. `qa compare` does the same for any two suite runs.

Credentials come from the vault, which is what makes an unattended run possible at all -
otherwise the first login prompt stops the build. See `.github/workflows/qa-suite.yml.example`.

### Credentials

```
qa vault                    # what this project needs · what is set · what is granted
qa vault set <name>         # store one in the OS keychain (prompts; never an argument)
qa vault grant <name> [--scenario ID]  ·  qa vault revoke <name>
```

`vault.yaml` is committed and declares the *names*, descriptions and origins - never
values. Clone the repo, run `qa vault`, and you are told exactly what to supply. Values
live in your OS keychain, or in `NKQA_SECRET_<NAME>` for CI.

Each credential is bound to an `origin`, and that binding is enforced twice: the value is
not read out of the keychain while the browser is elsewhere, and browser-use will not type
it on a non-matching page. A run that gets redirected cannot leak production credentials
into someone else's form. There is deliberately no `qa vault get`.

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
It's seeded by `qa learn` from a document you write - screenshots with a line or two
about each screen, roles, and flows - then grows automatically after every run
(git-committed as `appmap: learned from <run>`; disable with `appmap.auto_reflect: false`).
The planner reads all of it, so everything the map knows shows up in better scenarios.

`qa crawl` fills it in from the live app, **writing each screen as it finishes with it** -
one file, one commit, before it moves on. Stop a crawl and you keep every page it had
already understood. It also skips what is already documented, so a second crawl spends its
budget on new ground and never overwrites a page you wrote by hand (`--refresh` when the app
really has changed).

Ask the chat agent about the app and it answers from the map - and tells you when the map
does not cover something instead of guessing. When it has something wrong, say so:

    qa correct "client rows are clickable, they open /clients/<id>"

It shows the diff and commits it as `appmap: corrected by you`, so `git revert` undoes a
correction that came out wrong. Your edit wins over anything the agent learned on its own.

**Setting Jira up:** `qa connect jira --project PROJ` writes the server into `config.yaml`, then
`qa auth jira` does the OAuth. Both are in the desktop's Settings page too — a **Connect Jira**
button and **Sign in / check**. Until a connector named exactly `jira` exists, planning from a
ticket and filing a bug are hidden rather than present-and-broken.

Once it is connected, paste a ticket or its URL into the chat and the agent **reads it before it
writes anything** — what the ticket asks for, which screens in the app map it touches, and the
questions a QA would ask about what is ambiguous. Scenarios get drafted once you have answered.
`qa ticket PNY-3689` is the same read on its own, and writes nothing either way.

Extra tools for the agent come from MCP servers listed in `config.yaml` (`mcp:` section):
any server's tools can be exposed to the executor (test-data seeders, OTP readers, ...).
Jira is just the first connector - and it is *not* exposed to the executor, so bugs are
only filed when you run `qa file-bug`. Jira needs Node.js (`npx mcp-remote` bridges
Atlassian's remote MCP; first use opens an OAuth login in your browser).

The gate: `qa run` refuses drafts, deprecated scenarios, and scenarios edited after
approval (stale hash). A human approval is always in the loop before a browser moves.

## Workspace

```
config.yaml     # app URL, model roles (planner/executor/reflector/fallback), aliases
vault.yaml      # credential names, descriptions, origins - never values
appmap/         # what the agent knows about your app (grows over time)
scenarios/      # one markdown file per scenario; approval bound to a content hash
chats/          # conversations, committed: the reasoning behind a scenario is reviewable
runs/<name>/    # evidence per run: results.md, history.json, videos/, gif, conversation/
runs/suite--*/  # suite.md + suite.json: one report per suite run, for CI to read
.nkqa/          # gitignored, machine-local
```

## Development

```bash
./check.sh       # ruff + pyright strict + pytest - the definition of "passing"
./check.sh fix   # auto-format first
```

Conventions live in [CLAUDE.md](CLAUDE.md).
