# Phase 4 Plan — The App Map (Learning)

Status: agreed plan, not yet started · Written 2026-08-26
Read [ARCHITECTURE.md](ARCHITECTURE.md) §5 first. Phases 1–3 are shipped; Phase 3's
live Jira verification is still pending (needs the user's ticket + project key + OAuth).
Self-contained so a future session can execute it cold.

## 0. Context recap

The appmap (`appmap/` in the workspace) is the QA notebook: plain markdown in git, no
vector DB (ARCHITECTURE §5). Today it is a stub that only manual editing fills. The
planner already reads all of it (`nkqa/planner.py: gather_context`). Phase 4 adds the
three automatic writers, cheapest first: **learn-from-runs (reflector)** →
**onboarding crawl** → **local docs ingestion**. Confluence ingestion waits until the
Jira MCP connection is verified live.

Architect decisions (defaults, all config-toggleable — user asked for brief):
- Reflection runs **automatically after every scenario/explore run** (`appmap.auto_reflect`,
  default true) and never fails the run (errors are warnings). `qa reflect <run>` backfills.
- Reflector edits are **git-committed automatically** when the workspace is a git repo
  (`appmap: learned from <run>` messages) so `git log appmap/` shows what it learned;
  non-git workspaces just get the file writes.
- Crawl budget default 15 pages (`appmap.crawl_pages`), read-only rules, HITL creds.

## 1. Config additions

```yaml
appmap:
  auto_reflect: true     # reflect after each run (uses the 'reflector' model role)
  crawl_pages: 15        # page budget for qa crawl
```

## 2. New modules & commands

```
nkqa/appmap.py     # read/write helpers: list files, safe write under appmap/ only,
                   #   git auto-commit helper (no-op outside a git repo)
nkqa/reflector.py  # after a run: deterministic facts (urls visited from history.json)
                   #   + run verdicts + current appmap -> LLM (reflector role) ->
                   #   structured AppmapUpdate: [{file, content}] full-file writes,
                   #   create/update only, never delete, paths forced under appmap/
nkqa/crawler.py    # qa crawl: browser agent (executor role, HITL tools, QA_RULES +
                   #   read-only instruction) explores from base_url within the page
                   #   budget; structured output -> overview.md + pages/*.md drafts;
                   #   evidence under runs/crawl--<ts>/ like any run
nkqa/ingest.py     # qa learn <path>: local .md/.txt docs -> planner-role LLM
                   #   distills into appmap files (same AppmapUpdate shape)
```

CLI: `qa reflect <run>` · `qa crawl [--pages N]` · `qa learn <path>`.
Hook: `scenario_runner` and `runner` call reflection after `write_results`/save when
`auto_reflect` is on.

Shared shape — all three writers emit the same `AppmapUpdate` model and go through
`appmap.apply(update)` (path-sandboxed to `appmap/`, then auto-commit). One reviewer
surface: `git diff appmap/`. Human edits always win: writers must merge around
existing content (the LLM gets the current file and returns the updated whole file).

## 3. Implementation order (green on ./check.sh each step)

1. `appmap.py` + config parsing + tests (path sandboxing! `../` and absolute paths refused).
2. `reflector.py` + auto-hook + `qa reflect` + tests (stubbed LLM; run-failure isolation:
   a reflector crash must not change the run's exit code).
3. `crawler.py` + `qa crawl` + tests (task/prompt building, budget; live crawl later).
4. `ingest.py` + `qa learn` + tests.
5. README/ARCHITECTURE touch-ups; live verification: crawl a real site, run a scenario
   and watch `git log appmap/` gain a learned commit; planner visibly uses the new map.

## 4. Definition of done

- After a scenario run, appmap files update automatically and appear as their own git
  commit; `auto_reflect: false` produces zero writes (tests).
- Reflector/crawler/ingest can only touch files under `appmap/` (tests).
- Live: `qa crawl` on a real app produces a sensible overview + page notes; a
  subsequent `qa plan` cites knowledge that only the crawl could have provided.
- Run exit codes are untouched by reflection failures (tests).

## 5. Out of scope

Confluence/Jira-attachment ingestion (after Phase 3 live verification) · vector search ·
selector auto-healing for replay (Phase 5, will read learned facts) · appmap size
management/summarization (revisit when a real appmap outgrows the planner context).
