/** nkqa thinks with the user's own Claude Code. Until that works, this is the chat. */

import { CreditCard, Download, LogIn, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import * as api from '../api/client'
import type { ClaudeStatus, Connection } from '../api/types'

export function ClaudeGate({
	connection,
	claude,
	onRecheck,
}: {
	connection: Connection
	claude: ClaudeStatus
	onRecheck: () => Promise<void>
}) {
	const [checking, setChecking] = useState(false)
	const [note, setNote] = useState('')

	const recheck = () => {
		setChecking(true)
		setNote('')
		onRecheck().finally(() => setChecking(false))
	}
	const open = (which: 'install' | 'upgrade') => api.openPage(connection, which).catch((e: Error) => setNote(e.message))

	const [title, body, action] = !claude.path
		? [
				'Install Claude Code',
				'nkqa runs on Claude Code: Claude does the planning and drives the browser, on your own Claude account. Install it, then come back here.',
				<button className="primary" onClick={() => void open('install')}>
					<Download size={16} /> Install Claude Code
				</button>,
			]
		: !claude.logged_in
			? [
					'Sign in to Claude Code',
					'Claude Code is installed but not signed in. Sign in with your Claude account to continue.',
					<button
						className="primary"
						onClick={() =>
							api
								.claudeLogin(connection)
								.then(() => setNote('Finish signing in in your browser, then press Check again.'))
								.catch((e: Error) => setNote(e.message))
						}
					>
						<LogIn size={16} /> Sign in
					</button>,
				]
			: [
					'A Claude subscription is needed',
					'Claude Code needs a Claude Pro or Max plan (or API billing). Your account is signed in but has no plan that includes Claude Code.',
					<button className="primary" onClick={() => void open('upgrade')}>
						<CreditCard size={16} /> Get a subscription
					</button>,
				]

	return (
		<div className="gate">
			<div className="gate-card">
				<h1>{title}</h1>
				<p>{body}</p>
				<div className="gate-actions">
					{action}
					<button onClick={recheck} disabled={checking}>
						<RefreshCw size={16} className={checking ? 'spin' : ''} /> Check again
					</button>
				</div>
				{note && <p className="row-sub">{note}</p>}
				<p className="row-sub">
					Prefer a terminal? Run <code>claude</code> and type <code>/login</code>, then press Check again.
				</p>
				<p className="row-sub">Scenarios, the app map and past runs stay browsable in the meantime.</p>
			</div>
		</div>
	)
}
