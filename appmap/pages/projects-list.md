# Projects list

**Route:** `/projects` (landing page after login)

The master list of opportunities/projects.

## Key elements
- Tabs: **All**, **Prospect**, **Proposals In Progress**
- **Create Opportunity** button -> `/projects/new`
- Toolbar (top right): search, filter, sort, settings/columns, and two view-density toggles
- **Sort By** chip (e.g. "Project Start Date") with a clear (x), and a **Save View** button
- Table columns: Project Details (name + client), Project Stage & Number, Status,
  Proposal Manager, Proposal Due Date, Project Duration, Estimated Total Contract
- Inline edit pencils on **proposal due date** and **project duration**; an info (i)
  button per row and a "Project duration details" control
- Pagination footer: "1-10 of 442 results", rows-per-page selector (default 10)

## Navigation
- In: login, or "Back to Projects" from any project
- Out: clicking a project row -> `/projects/{projectNumber}/project-details`

## Quirks
- Due dates render as relative text ("Due in 12 days", "Overdue") next to the date.
- Win probability appears inside the contract-value cell ("Win. Prob. 25%").
