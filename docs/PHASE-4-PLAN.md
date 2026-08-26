# Phase 4 Plan — The App Map (Learning)

Status: agreed plan (revised), not yet started · Written 2026-08-26, revised same day
Read [ARCHITECTURE.md](ARCHITECTURE.md) §5 first. Phases 1–3 are shipped; Phase 3's
live Jira verification is still pending (needs the user's ticket + project key + OAuth).
Self-contained so a future session can execute it cold.

## 0. Context recap

The appmap (`appmap/` in the workspace) is the QA notebook: plain markdown in git, no
vector DB (ARCHITECTURE §5). Today it is a stub that only manual editing fills. The
planner already reads all of it (`nkqa/planner.py: gather_context`), so every learned
fact pays off in `qa plan` with zero planner changes.

**Revision (user decision, 2026-08-26):** the primary onboarding source is an
**annotated document** — a markdown file the user writes, with embedded screenshots and
text describing each screen, roles, flows, and quirks. Rationale: their app sits behind
2FA, and human annotations carry intent screenshots alone cannot. The autonomous crawl
is demoted to an optional enrichment tool (2FA does NOT block it — `ask_credential`
already collects OTPs mid-run — but it is no longer the cold-start path). A bare
screenshot folder is the degraded fallback of the same ingestion, not its own feature.

Writer order (by value now): **doc ingestion** → **learn-from-runs (reflector)** →
**crawl (optional)**. Confluence ingestion still waits for the Jira live verification.

Architect defaults (config-toggleable): auto-reflect after every run (`appmap.auto_reflect`,
default true, failures never change run exit codes); reflector edits auto-committed when
the workspace is a git repo (`appmap: learned from <run>`); crawl budget 15 pages.

## 1. The annotated-doc convention (input to `qa learn`)

One markdown file + an `images/` folder beside it. Loose convention, not a schema:

```markdown
# <App name>
What the app does. Base URL.
## Roles & test accounts
- admin: capabilities, test account, 2FA?
## Flows
- Main: Login -> Dashboard -> New order -> Payment -> Confirmation
## Screens
### Login
![](images/login.png)
Email + password, then OTP. Redirects to Dashboard.
```

Headings are hints, not requirements; unknown structure still ingests. Flows and
quirks in prose are the highest-value content.

## 2. Config additions

```yaml
appmap:
  auto_reflect: true     # reflect after each run (uses the 'reflector' model role)
  crawl_pages: 15        # page budget for the optional qa crawl
```

## 3. New modules & commands

```
nkqa/appmap.py     # read/write helpers: list files, safe write under appmap/ ONLY
                   #   (path sandbox: refuse ../ and absolute), git auto-commit helper
                   #   (no-op outside a git repo). All writers emit one AppmapUpdate
                   #   shape ([{file, content}] full-file create/update, never delete)
                   #   and go through appmap.apply(). One review surface: git diff.
nkqa/ingest.py     # qa learn <doc.md | folder>: parse markdown, resolve relative
                   #   image links, build a MULTIMODAL prompt (text + images) for the
                   #   planner-role model -> AppmapUpdate (overview, pages/*, flows/*)
                   #   + open questions printed for the human (planner-notes pattern).
                   #   Bare image folder = same pipeline, no text. Merges around
                   #   existing appmap content (LLM sees current files; human edits win).
nkqa/reflector.py  # after a run: deterministic facts (urls from history.json) + run
                   #   verdicts + current appmap -> reflector-role LLM -> AppmapUpdate.
nkqa/crawler.py    # OPTIONAL enrichment: qa crawl [--pages N] - browser agent
                   #   (executor role, HITL tools incl. OTP, read-only rules) explores
                   #   from base_url; structured notes -> AppmapUpdate; evidence under
                   #   runs/crawl--<ts>/.
nkqa/cli.py        # qa learn <path> · qa reflect <run> · qa crawl [--pages N] ·
                   #   qa models (role -> resolved model -> provider -> key present?)
```

⚠ Verify at implementation: browser-use multimodal message shape —
`ContentPartImageParam`/`ImageURL` in `browser_use/llm/messages.py` (base64 data URLs)
inside `UserMessage.content` lists, and that `ainvoke(..., output_format=...)` accepts
image parts for anthropic. Downscale/limit images (count + resolution) to fit context.

## 4. Implementation order (green on ./check.sh each step)

0. `qa models` status command (quick win promised to the user).
1. `appmap.py` + config parsing + tests (path sandbox, apply/commit, no-git no-op).
2. `ingest.py` + `qa learn` + tests (md parsing, image resolution, prompt assembly,
   fallback for bare folders — LLM stubbed; multimodal shape verified live once).
   **Then immediately: live-bootstrap the user's real appmap from their annotated doc.**
3. `reflector.py` + auto-hook in scenario_runner/runner + `qa reflect` + tests
   (run-failure isolation: reflector crash must not change the run's exit code;
   `auto_reflect: false` ⇒ zero writes).
4. `crawler.py` + `qa crawl` + tests (prompt/budget; live crawl only if/when wanted).
5. README/ARCHITECTURE touch-ups; live verification (see DoD). Commit per step.

## 5. Definition of done

- `qa learn` on the user's real annotated doc produces a sensible appmap (overview,
  pages, flows) in their workspace; a subsequent `qa plan` visibly cites knowledge
  only the doc could have provided. Open questions are surfaced, not guessed away.
- After a scenario run, appmap files update automatically as their own git commit;
  `auto_reflect: false` produces zero writes (tests).
- All writers are sandboxed to `appmap/` (tests). Run exit codes untouched by
  reflection failures (tests). `qa models` shows roles/providers/key-presence.

## 6. Out of scope

Confluence/Jira ingestion (after Phase 3 live verification) · vector search ·
selector auto-healing for replay (Phase 5) · appmap size management (revisit when a
real appmap outgrows the planner context) · crawl as a required onboarding step.
