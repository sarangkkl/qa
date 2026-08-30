# Project detail tabs (beyond Project Information)

Each is its own route: `/projects/{projectNumber}/{tab}`

## `msa-review` — MSA Review
"Manage MSA review process for project {number}". On a Prospect-stage project it renders
an error state: **"MSA Review data unavailable — The review details or project information
could not be loaded."** with a **Go Back** button. Whether that is correct-for-stage or a
defect is **unconfirmed** — worth asking before writing an assertion.

## `risk-profile` — Risk Profile
Sections include Billing Client. Feeds the Risk Approvals stage / `/approve-risks` queue.

## `reviews` — Reviews (project-scoped)
The project's own view of document reviews; the global equivalent is `/reviews`.

## `project-people` — Project People
Team assignment for the project.

## `project-location` — Project Location
Project Location (e.g. Ireland), **Travel Required** toggle, Billing Location (optional)
with saved addresses, **Select an SLR office**, yes/no options, **Save**.

## `commercials` — Commercials
Fee and commercial detail for the project.

## `invoicing-plan` — Invoicing Plan
Project Director, Invoice Type, **Save**, **Set-up Invoicing Plan**. Table columns:
S No, Invoice Instance Name, Billing Client, Billing Address, Billing Client Contact,
Purchase Order No., Labour, Expense, 3rd Party Expense.

## `delivery-plan` — Delivery Plan
"Manage your project structure, phases, and tasks." Table columns: DRAG, PHASE NUMBER,
PHASE NAME, PROJECT MANAGER, LINKED TO, PROFIT CENTRE, PRACTICE, FEE BASIS, LABOUR,
EXPENSE, 3RD PARTY, ACTIONS. Empty state "No phases defined" with **Add First Phase**
(single / multiple, Cancel / Confirm). Rows are drag-reorderable.
**Quirk:** shows "Loading Phases…" for several seconds — slower than other tabs.

## `win-info` — Win Info
Sections: Project Win Details / Contract Details, Billing Invoices, Project Setup,
Preferences. Fields: Formal Instruction Type (Contract Agreement, Email, Letter of Intent,
Memorandum of Agreement, …), billing contact, invoicing basis, payment terms, **Add Document**.

## `contracts` — Contracts
**Create Contract from Project.** Carries an explicit warning:
> Once contracts get added they cannot be deleted. The Work Breakdown Structure (WBS)
> will be submitted to **VantagePoint** and locked from modification.

⚠️ **Irreversible + external system.** Any automated test touching this must go through
`request_permission`, and should normally stay out of scope.

## `document-hub` — Document Hub
Tabs All / General Documents / Win-Info Documents, **Add document**, table of Name,
Description, Category, Type, Last Modified, Actions. Empty: "No documents found."
