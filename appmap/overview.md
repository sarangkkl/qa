# Sustain (SLR Consulting)

Project and proposal lifecycle management for SLR Consulting: opportunities are created,
staged through proposal, risk approval and document review, submitted to the client, and
then won/lost. Clients, project teams, commercials and documents hang off each project.

- **Base URL (dev):** https://dev.sustain.slrconsulting.com
- **Login:** `/login`, corporate sign-in with 2FA. Sessions persist in the browser
  profile, so an authenticated profile lands straight on `/projects`.
- **Mapped:** 2026-08-28, by read-only navigation of the dev environment as Gaurav Sah.

## Roles & accounts

- Signed-in user during mapping: **Gaurav Sah (GS)** - can create opportunities, edit
  project fields, and is Proposal Manager on many projects.
- Other proposal managers visible in data: Ajay Kumar, Jaymin Patel, testaccount dev001.
- Approval/review permissions exist (Approve Risks, Reviews with "Manage delegations"),
  but which role grants them is **not yet known**.

## Areas (left nav)

| Area | Route | Purpose |
|---|---|---|
| Projects | `/projects` | list of all opportunities/projects; entry to everything |
| Approve Risks | `/approve-risks` | risk approval and MSA review queues |
| Reviews | `/reviews` | technical and proposal-director document reviews |
| Clients | `/clients` | client records and client discovery |
| PM Hub | `/pm-hub` | delivery view: contract value, invoiced, WIP, budget remaining |
| Notifications | panel + `/notifications` | unread/read notification feed |
| Delegations | `/delegations` | approver responsibility management (from Approve Risks) |
| Sustain Bot | (in-app) | AI assistant - **out of testing scope** |
| Halo Support | (in-app) | support entry point - **out of testing scope** |

## Project lifecycle (stage bar on every project)

`Prospect → Proposal In Progress → Risk Approvals → Proposal Doc Reviews → Submit to Client → Won/Lost/Not Going Ahead`

Stage is also editable directly from a control in the project header ("Stage: Prospect · In Progress").
Statuses seen alongside stages: `InProgress`, `ProposalInReview`, `Won`, `Active`, and `Error`.

## Data conventions

- **Project number:** `501.D71289.00001` (profit-centre.project.sequence), copyable from the header.
- **Client code:** `AU.187070`, `IR.110381`, `ZA.170090` - country prefix + number.
- **Profit centre:** `CSA:3120:501 - CSA - ESIA Dublin`; Business Unit e.g. `Planning & Assessment`; Practice e.g. `ESIA`.
- **Billing rate card:** e.g. `EU_2026_Standard_Rates_USD`.
- Currencies vary per project (GBP, USD, AUD, CLF) - do not assume one currency.

## Known quirks

- A toast tip ("You Can Adjust Font Sizes Using Your Browser Zoom") appears on load and
  must be dismissed or ignored; it overlaps the top-right controls.
- Some projects show a red **Error** badge next to their stage (e.g. Hubspot-created
  projects) - an integration failure state worth testing around.
- PM Hub renders "Loading projects…" before data arrives; assert after load, not on paint.
- The projects list is large (442 results in dev) and paginates 10 per page.

## Irreversible actions (must be permission-gated in tests)

- **Contracts → Create Contract from Project**: contracts cannot be deleted once added, and
  the Work Breakdown Structure is submitted to **VantagePoint** and locked from modification.
- Any stage transition, and any Save on a project tab, writes real data in dev.

## Not yet mapped

The GS profile menu; whether clients have a detail page (rows are not links); the
**Client Discovery** tab; delegation mechanics; stage transitions; and everything behind a
form submission - mapping was strictly read-only. `/settings` and `/profile` do not exist
(both fall back to the projects list).

**Out of scope for testing (per the team):** Sustain Bot and Halo Support.
