# Reviews

**Route:** `/reviews`

Document review queues for proposals.

## Key elements
- Tabs with counts: **Technical Review (0)**, **Proposal Director Review (1)**
- Status filter: **Pending**
- Table: Document Name, Status, Review Due Date, Project Information, Client
- Empty state: "No Items To Review", footer "1-0 of 0 results"

## Notes
The counts in the tab labels are a useful assertion target. A project also has its own
per-project `reviews` tab, which is a different (project-scoped) view.
