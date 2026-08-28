# Flow: create an opportunity

1. From `/projects`, click **Create Opportunity** → `/projects/new`.
2. Fill **Proposal Details**: short name, long name, confidentiality (defaults to Normal),
   notes (**at least 50 characters** or the field should complain).
3. Fill **Client Details**: pick a Client, answer "Has SLR Done Business With This Client?",
   choose a "Is this a Competitive Bid?" option.
4. Fill **Project Details** (End Client Sub-Sector, start/end dates) and
   **Commercials Information** (amounts).
5. Submit.

**Expected:** a new project is created at stage **Prospect**, gets a project number of the
form `501.D71289.00001`, and appears at the top of `/projects` (sorted by start date).

**Unverified:** the submit control, validation behaviour, and the post-submit destination
were never exercised - mapping was read-only. Confirm before writing assertions.
