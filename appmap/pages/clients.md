# Clients

**Route:** `/clients`

## Key elements
- **Create Client** button
- Tabs: **My Clients (12)**, **Client Discovery**
- Table: Client, Client Code, Parent Company, Client Priority
- Priorities seen: `NA`, `Key Account`
- Pagination: "1-10 of 12 results"

## Data
Client codes are country-prefixed (`IR.110381`, `ZA.170090`, `AU.187070`). Parent company
is often "-", sometimes a quoted name (e.g. "NEOM Green Hydrogen Company Limited").

## Create Client (modal)
**Create Client** (top right) opens an **Add Client** modal — "All fields are mandatory,
unless marked optional":
Client Name · Company Registration No. (Optional) · Parent Company (Optional, select) ·
Sector (select) · SLR Client Owner (select, defaults to the signed-in user) · Website URL ·
Client Type (Optional, select) · more fields below the fold · **Cancel** / **Save**.

## Toolbar
Search, sort and filter icons sit above the table on the right.

## Not verified
Client rows are not clickable links — no client detail page was reachable by clicking a
row. The **Client Discovery** tab did not switch views on click during mapping; treat both
as unknown rather than broken.
