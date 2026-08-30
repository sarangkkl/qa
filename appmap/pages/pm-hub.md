# PM Hub

**Route:** `/pm-hub`

Delivery/commercial view of projects for project managers.

## Key elements
- Tabs with counts: **Active Projects(5)**, **All Projects(5)**
- Inline **Edit project end date** and **Project duration details** controls; paginated (10/page)
- Table: Project Details, Client, Project People, Project Duration, Contract Value,
  Invoiced (Excluding VAT), WIP, Budget Remaining

## Quirks
Renders "Loading projects…" while data loads - wait for rows before asserting.
