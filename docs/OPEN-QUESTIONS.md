# Open questions

Written 2026-08-28, after mapping Sustain read-only.
**How to use this:** answer inline under each `Answer:` line — short is fine. Answers to
§1 get folded into `appmap/` (which makes every future `qa plan` sharper); answers to §2–3
change how we set the product up.

Priority: 🔴 blocking good scenarios · 🟡 needed soon · ⚪ background

---

## 1. About Sustain (feeds the appmap)

### 🔴 1.1 Is the MSA Review error expected?
On project `501.D71289.00001` (stage: Prospect), `/msa-review` renders:
> "MSA Review data unavailable — The review details or project information could not be loaded." + a **Go Back** button

If it's correct-for-stage, the appmap should say so and tests will assert it. If not, it's a
real defect and our first bug to file. Recorded as *unconfirmed* rather than guessed.

**Answer:**

### 🔴 1.2 Who can do what, and what gates each stage?
The lifecycle is `Prospect → Proposal In Progress → Risk Approvals → Proposal Doc Reviews →
Submit to Client → Won/Lost/Not Going Ahead`. Unknown:
- Which role may move a project between stages, and what must be complete first?
- Who can approve in `/approve-risks` (Risk Approval vs MSA Review tabs)?
- Who does Technical Review vs Proposal Director Review in `/reviews`?
- How do items actually arrive in those queues — automatically on stage change, or by hand?

This is the highest-value flow to test and currently the least understood.

**Answer:**

### 🔴 1.3 What is the test-data policy on dev?
Dev holds 442 projects, many obviously scratch data. Needed:
- May scenarios **create** projects/clients freely? Should they clean up afterwards?
- Is any data off-limits — records a test must never modify or delete?
- Is there a naming convention for test-created records (e.g. a `QA_` prefix)?

**Answer:**

### 🔴 1.4 Is dev wired to a real VantagePoint?
`Contracts → Create Contract from Project` warns that contracts cannot be deleted and the
WBS is submitted to **VantagePoint** and locked. If dev talks to a real sandbox this stays
permanently out of scope; if it's stubbed, it becomes testable.

**Answer:**

### 🟡 1.5 What does the red "Error" badge mean?
Some projects (e.g. "Project Hubspot creation 01XC", "gaurav deal testing") show a red
**Error** badge beside their stage. An integration failure? Recoverable? Worth a test?

**Answer:**

### 🟡 1.6 Do clients have a detail page?
Client rows on `/clients` are not links and did not navigate on click, and the
**Client Discovery** tab did not switch views. Is that by design, permission-dependent, or broken?

**Answer:**

### ⚪ 1.7 Which areas matter most for QA?
Sustain Bot and Halo Support are already out of scope. Of what remains — Projects,
Create Opportunity, the 12 project tabs, Approve Risks, Reviews, Clients, PM Hub,
Notifications, Delegations — which two or three break most often, or hurt most when broken?
That decides where scenarios get written first.

**Answer:**

---

## 2. Access

### 🔴 2.1 Can we get a dedicated QA account?
Mapping ran as **Gaurav Sah**. Runs attribute actions to the signed-in user, so agent-created
projects would show you as Proposal Manager, and test runs would pollute your notifications
and delegations. A dedicated account (ideally with the same permissions) keeps evidence clean.

**Answer:**

### 🟡 2.2 Why did 2FA not trigger?
A brand-new Chrome profile went straight to `/projects` fully authenticated — no login, no
2FA. Expected SSO behaviour on dev, or a session-handling gap worth investigating?

**Answer:**

### 🟡 2.3 Jira details
To finish Phase 3: complete the `qa auth jira` OAuth login, then provide
- a **ticket key** to plan from (a story with acceptance criteria): ______
- the **project key** for filing bugs: ______

**Answer:**

---

## 3. Product setup

### 🟡 3.1 Who else will use this?
Just you, or other QA engineers too? If it's a team, the workspace (scenarios + appmap)
should probably live in its own repo that people clone and PR against, rather than staying
local — approval-by-PR is already supported by the design.

**Answer:**

### ⚪ 3.2 Where should runs and evidence live long-term?
`runs/` accumulates videos and transcripts and is currently gitignored. Keep it local,
commit the reports only, or push evidence somewhere shared?

**Answer:**

### ⚪ 3.3 What is the product actually called?
Still working-title `nkqa` with the `qa` command. Renaming is a find-replace, and it's
cheaper to do before anyone else installs it.

**Answer:**
