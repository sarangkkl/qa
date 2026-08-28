# Create Opportunity

**Route:** `/projects/new` (from the Create Opportunity button on `/projects`)

Long single-page form: "All fields are mandatory, unless marked optional".

## Sections and fields
- **Proposal Details** - Project Name (Short), Project Name (Long),
  Confidentiality (radio: Normal / Confidential / Secret, default Normal),
  Notes (textarea, **minimum 50 characters**)
- **Client Details** - Client (searchable select) + Edit Client;
  "Has SLR Done Business With This Client?" (Yes/No, default No);
  "Is this a Competitive Bid?" (select) with options:
  Competitive: RFP or Tender (Public Bid) · Competitive: Direct Invite to Tender by Client ·
  Competitive: Existing MSA / Framework / Mini-Bid · Competitive: RFQ (Price shopping) ·
  Competitive: Information Selection (Partner Vetting, EOI - No RFP) ·
  Non-Competitive: Sole Sourced / Direct Award
- **Project Details** - End Client Sub-Sector (select), date fields (Start Date, End Date,
  a "Select date" picker), checkbox(es)
- **Commercials Information** - numeric amount fields (placeholder `0.00`)

## Navigation
In: `/projects` → Create Opportunity. Out: Back to Projects.

## Not verified
Submission was never attempted (mapping was read-only), so validation messages, the
success destination, and the resulting project number are unknown.
