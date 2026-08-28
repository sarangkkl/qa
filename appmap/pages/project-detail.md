# Project detail

**Route:** `/projects/{projectNumber}/{tab}` e.g. `/projects/501.D71289.00001/project-details`

## Header
- Back to Projects; project name; client code + client name; project number
  (both codes have copy buttons); Promo Code; Last Updated; proposal due date
- **Win Prediction - NN%** with a BETA badge
- Owner avatar and a **Stage** selector ("Prospect · In Progress")
- Stage progress bar across the top:
  Prospect → Proposal In Progress → Risk Approvals → Proposal Doc Reviews →
  Submit to Client → Won/Lost/Not Going Ahead

## Tabs (each is its own route)
`project-details` · `msa-review` · `risk-profile` · `reviews` · `project-people` ·
`project-location` · `commercials` · `invoicing-plan` · `delivery-plan` · `win-info` ·
`contracts` · `document-hub`

## Sections on `project-details`
Project Information (Profit Centre*, Business Unit, Practice, Billing Rate Card*),
Proposal Details (Project Name Short/Long, Confidentiality: Normal/Confidential/Secret,
Notes), Client Details (Client + Edit Client, client address), Project Details,
Project Team, Fee Information, Submission Details.
(* editable via a pencil icon; Business Unit and Practice render read-only/derived.)

## Document Hub (`/document-hub`)
Tabs All / General Documents / Win-Info Documents; **Add document**; table of
Name, Description, Category, Type, Last Modified, Actions. Empty state: "No documents found."

## Quirks
- Every editable section repeats "All fields are mandatory, unless marked optional".
- Some inline controls surface raw GUIDs as button labels (address/contact ids) -
  cosmetic, but avoid asserting on those strings.
