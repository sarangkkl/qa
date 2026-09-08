/** First screen: which project. Outside Tauri there is no picker, so it explains itself.
 *
 * Picking a folder that is not a workspace is not an error - it is the other half of the
 * job. The form offers to create one; the sidecar does the creating, because the layout
 * lives in `nkqa/workspace.py` and nowhere else.
 */

import { useEffect, useRef, useState } from 'react'
import { inTauri, pickWorkspace, recentWorkspaces, type InitOptions } from '../api/connection'

/** "my-shop" / "my_shop" -> "My Shop". A guess the human can correct in the field. */
function nameFromPath(path: string): string {
	const base = path.split('/').filter(Boolean).pop() ?? ''
	return base
		.replace(/[-_]+/g, ' ')
		.replace(/\s+/g, ' ')
		.trim()
		.replace(/\b\w/g, (c) => c.toUpperCase())
}

export function WorkspacePicker({
	onOpen,
	autoPick,
	error,
	busy,
}: {
	onOpen: (workspace: string, init?: InitOptions) => void
	autoPick: number
	error: string
	busy: boolean
}) {
	const [recent, setRecent] = useState<string[]>([])
	const [pickError, setPickError] = useState('')
	const [setup, setSetup] = useState<{ path: string; appName: string; baseUrl: string } | null>(null)

	useEffect(() => {
		recentWorkspaces().then(setRecent).catch(() => setRecent([]))
	}, [])

	const pick = () => {
		setPickError('')
		pickWorkspace()
			.then((picked) => {
				if (!picked) return
				if (picked.is_workspace) return onOpen(picked.path)
				setSetup({ path: picked.path, appName: nameFromPath(picked.path), baseUrl: 'https://' })
			})
			.catch((e: Error) => setPickError(e.message))
	}

	// A window the File menu opened to choose a folder should not make you click Open again.
	// The ref is not paranoia: StrictMode double-invokes this effect, and without it macOS
	// stacks two folder dialogs on top of each other.
	const picked = useRef(0)
	useEffect(() => {
		if (autoPick > 0 && picked.current !== autoPick) {
			picked.current = autoPick
			pick()
		}
	}, [autoPick])

	const create = () => {
		if (!setup) return
		onOpen(setup.path, { app_name: setup.appName.trim(), base_url: setup.baseUrl.trim() })
	}

	// Every browser verb needs a base URL, and an empty one fails three screens later with
	// something far less obvious than this.
	const urlOk = /^https?:\/\/.+/.test(setup?.baseUrl.trim() ?? '')

	if (setup) {
		return (
			<div className="picker">
				<h1>Set up this folder</h1>
				<p className="row-sub">
					No QA workspace here yet. This writes <code>config.yaml</code>, <code>vault.yaml</code> and the{' '}
					<code>appmap/</code>, <code>scenarios/</code>, <code>runs/</code> folders. Nothing existing is touched.
				</p>
				<p className="row-sub">{setup.path}</p>
				{(error || pickError) && <p className="error">{error || pickError}</p>}

				<label className="field">
					<span>App name</span>
					<input
						value={setup.appName}
						disabled={busy}
						onChange={(e) => setSetup({ ...setup, appName: e.target.value })}
						placeholder="Sustain"
					/>
				</label>
				<label className="field">
					<span>Base URL</span>
					<input
						value={setup.baseUrl}
						disabled={busy}
						onChange={(e) => setSetup({ ...setup, baseUrl: e.target.value })}
						placeholder="https://dev.example.com"
					/>
				</label>
				<p className="row-sub">
					Model API keys are not set here — check Settings once it opens. (A key typed into this form would
					cross the app as a command-line argument, which other processes can read.)
				</p>

				<div className="row-actions">
					<button className="primary big" disabled={busy || !urlOk} onClick={create}>
						{busy ? 'Creating…' : 'Create workspace'}
					</button>
					<button disabled={busy} onClick={() => setSetup(null)}>
						Cancel
					</button>
				</div>
			</div>
		)
	}

	return (
		<div className="picker">
			<h1>nkqa</h1>
			<p className="row-sub">An AI QA teammate. Open a workspace to begin.</p>
			{(error || pickError) && <p className="error">{error || pickError}</p>}
			{busy && <p className="row-sub">Starting the sidecar — first launch unpacks it, so give it up to a minute…</p>}

			{inTauri() ? (
				<>
					<button className="primary big" disabled={busy} onClick={pick}>
						{busy ? 'Opening…' : 'Open a workspace…'}
					</button>
					{recent.length > 0 && (
						<>
							<h3>Recent</h3>
							{recent.map((path) => (
								<button key={path} className="row" disabled={busy} onClick={() => onOpen(path)}>
									<span className="row-title">{path.split('/').pop()}</span>
									<span className="row-sub">{path}</span>
								</button>
							))}
						</>
					)}
				</>
			) : (
				<div className="detail">
					<p>
						Running in a browser, so there is no sidecar to spawn. Start one and reload with its handshake:
					</p>
					<pre className="console">
						{'nkqa-server --workspace /path/to/qa-workspace\n'}
						{'→ {"ready": true, "port": 51734, "token": "…"}\n\n'}
						{'open http://127.0.0.1:1420/?port=51734&token=…'}
					</pre>
				</div>
			)}
		</div>
	)
}
