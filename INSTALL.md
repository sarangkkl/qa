# nkqa — install and use it from Claude Code

nkqa turns your coding agent into a QA engineer for your web app. It plans test scenarios,
runs them in a real browser, records video and screenshots, and tells you what broke.
**It uses your existing Claude Code subscription** — nkqa itself never calls a model.

You stay in control: every scenario is approved by you, every risky action (delete, pay,
send) asks you first, and passwords are typed by nkqa inside the browser — the agent never
sees them. Those prompts appear as native dialogs on your screen.

## What you need

- **Claude Code** (any plan).
- **uv** — installs and runs nkqa for you. One line, once:
  `curl -LsSf https://astral.sh/uv/install.sh | sh` (Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`)
- **Google Chrome** on the machine (the browser nkqa drives).
- macOS for the approval dialogs. (Linux/Windows: dialogs are not available yet, so
  approvals and credentials can't be granted there.)

## Install (two commands, inside Claude Code)

```
/plugin marketplace add sarangkkl/nkqa
/plugin install nkqa@nkqa
```

Restart Claude Code. The first time it starts, `uv` downloads nkqa and its dependencies
(about a minute). After that it's instant. Type `/mcp` — you should see **nkqa** connected.

## First use

Open Claude Code **in the folder of the app you want to test** and say:

> set up nkqa for this app, it runs at https://dev.myapp.com

The agent creates a `qa/` workspace-style layout in that folder (`config.yaml`, `appmap/`,
`scenarios/`, `runs/`). Then:

| you say | what happens |
|---|---|
| `/nkqa:plan the login flow` | the agent reads what it knows about your app and writes draft scenarios to `scenarios/` |
| "approve auth/login" | a dialog shows you the full scenario; click **Confirm** |
| `/nkqa:run auth/login` | a browser opens; the agent clicks through the steps, one at a time |
| (a login form appears) | a dialog asks you for the password; the agent only gets a placeholder |
| (something risky is about to happen) | a dialog asks allow once / this session / always / deny |
| (done) | verdict per step + `runs/<name>/` with `results.md`, screenshots, video |
| `/nkqa:explore https://dev.myapp.com` | free exploration with no scenario, to learn the app |

Ask it anything in between — "which scenarios are still drafts?", "why did step 3 fail?" —
it reads the workspace directly.

## Good to know

- **The appmap is the agent's memory** (`appmap/*.md`, plain markdown in git). It gets better
  after every run. Edit it by hand any time.
- **Approval is bound to the file.** Edit an approved scenario and it needs approving again.
- **One workspace per Claude Code session.** Start Claude Code in the project folder, or ask
  "use the workspace at /path" to switch.
- **Nothing runs unapproved.** Drafts, edited-after-approval and deprecated scenarios are refused.
- Credentials can be saved in your OS keychain so you're not asked every run — say
  "save it" when the dialog offers, or ask the agent to explain `vault.yaml`.

## Codex / Cursor

Same server, registered by hand:

```
codex mcp add nkqa -- uvx --from git+https://github.com/sarangkkl/nkqa qa mcp
```

Cursor: add to `.cursor/mcp.json`
```json
{ "mcpServers": { "nkqa": { "command": "uvx", "args": ["--from", "git+https://github.com/sarangkkl/nkqa", "qa", "mcp"] } } }
```

## Troubleshooting

- **nkqa doesn't show in `/mcp`** → `uv` isn't on your PATH. Open a new terminal after
  installing it, then restart Claude Code.
- **"is not approved yet"** → you haven't approved that scenario; say "approve <id>".
- **A dialog never appeared** → check for a window behind others; a dialog nobody answers
  within 15 minutes counts as *deny*, which is safe.
- **The browser didn't open** → make sure Chrome is installed. Runs are headed by default;
  set `headless: true` in `config.yaml` to hide it.
- **Update nkqa** → `uv cache clean nkqa`, then restart Claude Code.

## Uninstall

```
/plugin uninstall nkqa@nkqa
```
