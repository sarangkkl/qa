# Flow: log in (dev)

1. Navigate to **https://dev.sustain.slrconsulting.com/login** (not the root URL).
2. Enter the QA account email: `test.account.dev.001@slrconsulting.com`.
3. Continue.

**Expect:** lands on `/projects` as the signed-in user; the left navigation is visible.

**Notes**
- On **dev**, `/login` bypasses 2FA - this is what makes unattended suite runs possible.
  Starting at the root URL instead triggers the corporate 2FA path.
- Production behaviour is assumed to be different; do not assume this shortcut anywhere
  but dev. A dev-only auth shortcut is worth confirming never ships to production.
