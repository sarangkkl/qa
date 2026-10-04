# Releasing Kiwame

Kiwame ships as a desktop app for macOS (Apple Silicon and Intel), Windows and Linux. You never
build those by hand: GitHub Actions builds all four, on four machines, from a version tag.
The recipe is [.github/workflows/release.yml](.github/workflows/release.yml).

## How it works

```
you: bump the version, commit, push a tag v0.2.0
          │
          ▼
GitHub Actions ── checks: lint, types, tests (Linux) ── stops here if anything fails
          │
          ├── macOS Apple Silicon ─┐
          ├── macOS Intel ─────────┤  each one: build the Python server (PyInstaller),
          ├── Windows x64 ─────────┤  smoke-test it, then build the app around it (Tauri)
          └── Linux x64 ───────────┘
          │
          ▼
a DRAFT release "Kiwame v0.2.0" with every installer attached
          │
          ▼
you: check it, write the notes, press Publish
```

Why four machines: the Python server is turned into a native program by PyInstaller, which can
only build for the operating system it runs on. So the Windows build happens on Windows, and so on.

The draft step is deliberate. Nothing reaches anyone until you press **Publish**.

## Your first release (one time, step by step)

Everything is already committed and pushed on `desktop-claude`. The first run is a **test**: a
throwaway `-rc1` tag, so any Windows/Linux surprises show up before a real version number is used.

**Step 1 - start a test build.** In the repo folder:

    git checkout desktop-claude
    git pull
    git tag v0.1.0-rc1
    git push origin v0.1.0-rc1

Pushing the tag is what starts the build. (Any tag starting with `v` does.)

**Step 2 - watch it.** Open https://github.com/sarangkkl/qa/actions, click the newest
"Release" run. Or in Terminal: `gh run watch`. You will see:

- *Checks* first (2-4 min). If it fails, nothing else runs.
- then four builds side by side: *macOS Apple Silicon*, *macOS Intel*, *Windows x64*, *Linux x64*
  (20-40 min). Green tick = that installer is ready.

**Step 3 - if something is red.** Click the red job, then the red step, and copy the error.
Send it to Claude (or fix it), commit, then re-run the test with a new tag:

    git tag v0.1.0-rc2
    git push origin v0.1.0-rc2

Expect this on the first try: the tests have only ever run on macOS, and Windows and Linux have
never been built.

**Step 4 - try the installers.** When all four are green, open
https://github.com/sarangkkl/qa/releases. There is a **Draft** release "Kiwame v0.1.0-rc1"
(only you can see drafts). Download each installer, install it, open a workspace, send one chat
message. Unsigned builds warn on first open - see "What users see today" below.

**Step 5 - throw the test away.** On the draft release: **Delete**. Then remove the tag:

    git push --delete origin v0.1.0-rc1
    git tag -d v0.1.0-rc1

**Step 6 - the real release.** When an rc build works everywhere:

    git tag v0.1.0
    git push origin v0.1.0

Wait for the four green builds, open the draft "Kiwame v0.1.0", write a few lines of notes,
press **Publish release**. That is the moment people can download it.

(Releases are built from whatever commit the tag points at, so this works from `desktop-claude`.
When you merge it into `master`, tag on `master` from then on.)

## Releasing a new version

1. **Bump the version** (it lives in three files; this sets all of them):

       python3 scripts/bump_version.py 0.2.0

2. **Commit and tag** (the script prints these too):

       git commit -am "Release v0.2.0"
       git tag v0.2.0
       git push origin HEAD v0.2.0

3. **Watch it**: GitHub → the `qa` repo → **Actions** → "Release" (or `gh run watch`). About 25-40 minutes; the Mac and
   Windows builds are the slow ones. If one platform fails, the others still finish (open the red
   job to see why).

4. **Publish**: GitHub → **Releases** → the draft "Kiwame v0.2.0". Check it has:

   | File | For |
   |---|---|
   | `Kiwame_0.2.0_aarch64.dmg` | Macs with Apple Silicon (M1-M4) |
   | `Kiwame_0.2.0_x64.dmg` | Intel Macs |
   | `Kiwame_0.2.0_x64-setup.exe` / `Kiwame_0.2.0_x64_en-US.msi` | Windows (either one) |
   | `Kiwame_0.2.0_amd64.AppImage` / `kiwame_0.2.0_amd64.deb` | Linux (AppImage runs anywhere; .deb for Ubuntu/Debian) |

   Write a few lines of release notes, then **Publish release**.

Made a mistake? Delete the draft release and the tag (`git push --delete origin v0.2.0`), fix,
and tag again.

## Trying a build without releasing

GitHub → **Actions** → "Release" → **Run workflow** (pick the branch). Same four builds, but the
installers come back as downloadable *artifacts* at the bottom of the run page, and no release
is created. Use this to test a branch on a Windows or Linux machine before you tag.

## Before a release, try it on each platform

CI proves every installer builds and that its server starts. It cannot click through the
window. For a real release, install each one once (a VM is fine for Windows and Linux), open a
workspace and send one chat message.

## What users see today: the builds are unsigned

Until the builds are signed, operating systems warn about them:

- **macOS**: "Kiwame can't be opened because Apple cannot check it for malicious software" (or
  "is damaged"). Right-click the app → **Open** → **Open**, once. Or in Terminal:
  `xattr -dr com.apple.quarantine /Applications/Kiwame.app`.
- **Windows**: SmartScreen says "Windows protected your PC". Click **More info** → **Run anyway**.
- **Linux**: no warning. Make the AppImage executable (`chmod +x`) and run it.

The first launch on a new machine is also slower: macOS (and Windows Defender) check every
library once. The app starts its server in the background as soon as it opens, so most of that
happens while you are still on the start screen.

## Signing (to do when the accounts exist)

**macOS** - after the Apple Developer enrollment is approved:
1. Create a *Developer ID Application* certificate, export it as a `.p12` with a password.
2. Add repo secrets (GitHub → Settings → Secrets and variables → Actions):
   `APPLE_CERTIFICATE` (the .p12, base64: `base64 -i cert.p12 | pbcopy`), `APPLE_CERTIFICATE_PASSWORD`,
   `APPLE_SIGNING_IDENTITY` (e.g. `Developer ID Application: Gaurav Sah (TEAMID)`), `APPLE_ID`,
   `APPLE_PASSWORD` (an app-specific password from appleid.apple.com), `APPLE_TEAM_ID`.
3. Pass them as `env:` to the tauri-action steps in the workflow; Tauri then signs and notarizes.
4. One extra step for us: every library inside the bundled server (`server/_internal/**/*.dylib`,
   `*.so`) must be signed with the same identity and hardened runtime before Tauri signs the app.
   [desktop/src-tauri/entitlements.plist](desktop/src-tauri/entitlements.plist) already has the
   entitlements Python needs.

**Windows** - an Authenticode certificate removes the SmartScreen warning. The cheapest route is
Azure Trusted Signing (~$10/month); Tauri supports it via `bundle.windows.signCommand`.

**Linux** - nothing to sign.

## Costs

The `qa` repo is public, so GitHub Actions minutes are free, Mac and Windows included. If you make
the repo private, they count against the free 2,000 minutes a month (macOS 10×, Windows 2×) -
roughly 300-500 minutes per release.

## Who can download

The `qa` repo is **public**, so a published release (and the source code) is visible to anyone;
drafts are visible only to you. To keep the code private but the downloads public: make `qa`
private, create a fine-grained token with *Contents: write* on the public `sarangkkl/nkqa`, save
it as the secret `RELEASE_TOKEN`, and give the release step `owner: sarangkkl`, `repo: nkqa` and
`GITHUB_TOKEN: ${{ secrets.RELEASE_TOKEN }}`.

## Building locally (Mac only)

    venv/bin/pyinstaller --noconfirm nkqa-server.spec
    rm -rf desktop/src-tauri/binaries/nkqa-server && cp -RL dist/nkqa-server desktop/src-tauri/binaries/nkqa-server
    python3 scripts/smoke_server.py dist/nkqa-server/nkqa-server
    cd desktop && npm run build:tauri     # -> src-tauri/target/release/bundle/{macos,dmg}/

Details and measurements: [desktop/src-tauri/binaries/README.md](desktop/src-tauri/binaries/README.md).
