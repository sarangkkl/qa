# D0 spikes (throwaway)

Answers, not product code. Results are recorded in [../docs/DESKTOP-PROGRESS.md](../docs/DESKTOP-PROGRESS.md).

- `spike_screencast.py` — S1: is `Page.startScreencast` reachable through browser-use's CDP session?
- `spike_sidecar_entry.py` — S2: does a PyInstaller onedir bundle of nkqa + browser-use launch a browser?

Both expect a page to look at:

```bash
mkdir -p /tmp/spikeweb && echo '<h1>spike</h1>' > /tmp/spikeweb/index.html
(cd /tmp/spikeweb && python3 -m http.server 8765 &)
venv/bin/python spikes/spike_screencast.py
```

`SPIKE_CHROME` overrides the browser binary; leave it unset on a normal machine where
browser-use finds its own. S2 also needs `pyinstaller`:

```bash
venv/bin/pyinstaller --noconfirm --onedir --name nkqa-server \
  --collect-all browser_use --collect-all cdp_use --collect-all nkqa \
  spikes/spike_sidecar_entry.py && ./dist/nkqa-server/nkqa-server
```
