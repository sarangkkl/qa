# Your turn

Everything buildable without you is built and green. This is what is actually blocked on
you, in the order I would do it. Tick things off here; it is a working checklist, not a
document.

Written 2026-09-04, after the desktop track landed.

---

## 1. Verify what exists · ~30 minutes · do this first

Nothing in this repo has ever run on your machine. Every test, every build, every
screenshot happened in a Linux container against a `browser-use` I pinned by hand. If any
of it is red on macOS, everything built on top inherits the problem — so this comes before
new work.

- [ ] **`./check.sh`** — 168 tests, ruff, pyright strict.
      If `pytest` fails in `tests/test_mcp.py`, it is the dependency float below.
- [ ] **Decide whether to pin `browser-use`.** `pyproject.toml` says `>=0.13.8`; 0.13.10
      pulls `mcp` 2.x, which breaks your `echo_mcp_server` fixture (`FastMCP` was renamed).
      I developed against `==0.13.8`. Pin it, or fix the fixture for 2.x — but pick one.
- [ ] **Try the CLI end to end** on a scratch workspace: `qa init`, `qa vault`, `qa suite`.

## 2. Prove the desktop app on macOS · ~1 hour

I compiled and ran the Tauri app under Xvfb on Linux. macOS uses WKWebView instead of
WebKitGTK, so the shell is genuinely unproven there.

- [ ] **Build the macOS sidecar.** The binary in `desktop/src-tauri/binaries/` is a Linux
      build (and gitignored, so it should not even reach your checkout). The exact
      PyInstaller command — and why it is `--onefile`, and why it needs all those
      `--collect-all` flags — is in `desktop/src-tauri/binaries/README.md`.
- [ ] **`cd desktop && npm install && npm run dev:tauri`.** Expect something to go wrong
      that did not on Linux; paste it at me.
- [ ] **Sanity-check the UI against a real workspace** — your Sustain one, not the demo.

## 3. Things I cannot test without your credentials

Each of these is a path I have written and exercised, but never with the real service.

- [ ] **`qa vault set qa_user`** on macOS. My container has no keychain, so only the
      environment-variable fallback has ever run. I want to know two things: that the real
      Keychain stores and reads back, and whether the first read after a reboot prompts for
      the login keychain (which would stall an unattended suite).
- [ ] **`qa auth jira`** — complete the OAuth. Needs Node for `npx mcp-remote`.
- [ ] **Fill in the blanks in `OPEN-QUESTIONS.md` §2.3**: a ticket key to plan from, and the
      project key for filing bugs. Without these the two Jira features in the desktop are
      untested against the real thing.
- [ ] **Set `ANTHROPIC_API_KEY`** and do one real `qa run`. This is the only way to prove
      the **live pane** — the backend is tested and the rendering is written, but nobody has
      ever watched a real run stream into that window.

## 4. Decisions only you can make

- [ ] **The name.** You said keep `nkqa` for now, and the bundle id is
      `com.gauravsah.nkqa`. Fine for an internal tool. Worth one more thought before anyone
      installs it — renaming after that is a different kind of cost than a find-replace.
- [ ] **Apple Developer account / signing identity.** Blocks notarization, which is the
      last mile of D6 and historically the part that eats a day. Worth a dry run early.
- [ ] **Windows: yes or no?** `tauri.conf.json` targets `msi` today on the assumption of
      yes. If it is macOS-only, that simplifies the release workflow.
- [ ] **`OPEN-QUESTIONS.md` §3.1** — is this just you, or other QA engineers? It changes
      whether the workspace should live in its own repo that people clone and PR against.
- [ ] **§3.2** — where should evidence live long-term? `runs/` accumulates video. The Runs
      view design changes if it is meant to be pushed somewhere shared.

## 5. The one that actually matters most

**The product has never been pointed at Sustain in anger.** The loop works, the desktop
works, the suite works — but the 🔴 questions in `OPEN-QUESTIONS.md` have been open since
2026-08-28, the appmap was never bootstrapped from a real document, and there is not one
scenario written against a Sustain flow that matters.

I have been building capability. None of it is worth anything until it finds a real bug.

- [ ] **Answer the 🔴 questions** (§1.1–1.4): the MSA Review error, who can do what at each
      stage, the test-data policy on dev, and whether dev talks to a real VantagePoint.
      These are the difference between scenarios that are plausible and scenarios that are
      right.
- [ ] **Write the annotated doc** for `qa learn` — screenshots of the main screens with a
      line or two each, the roles, the flows. This is the single highest-leverage input to
      the whole system: everything the appmap knows shows up in every future plan.
- [ ] **Pick two or three areas** (§1.7) that break most often, and let the planner draft
      scenarios for them.

---

## What is not on you

For the record, so nothing here is ambiguous: D1 (channel), D2 (sidecar), D2b (vault),
chats, the screencast backend, Phase 6 (suites + comparison), D3/D4 (the desktop app), and
the MCP/Jira surfaces are all built, tested, and in the repo. Status per milestone is in
[DESKTOP-PROGRESS.md](DESKTOP-PROGRESS.md).

Still unbuilt by choice: **stuck-escalation** and **selector auto-healing**. Both need a
signal that only real runs can provide — which loops back to §5.
