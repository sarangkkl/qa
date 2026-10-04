/** The vault, read-only plus the human-only actions.
 *
 * There is no reveal button and there will not be one: it is the single feature that turns
 * a leak into a catastrophe, and nothing here needs it.
 */

import type { Job, Session } from '../api/socket'
import type { ConnectorInfo } from '../api/types'
import { Connectors } from './Connectors'

export function Credentials({
	session,
	jobs,
	connectors,
	busy,
}: {
	session: Session
	jobs: Job[]
	connectors: ConnectorInfo[]
	busy: boolean
}) {
	const latest = [...jobs].reverse().find((j) => j.name === 'vault')

	return (
		<div className="detail wide">
			<div className="detail-head">
				<h2>Credentials</h2>
				<button onClick={() => session.command('vault', {})}>Refresh</button>
			</div>

			<p className="row-sub">
				Values live in your OS keychain, never in the workspace. <code>vault.yaml</code> declares the names and the
				origin each one is bound to — a credential is not released while the browser is somewhere else.
			</p>

			{latest ? (
				<pre className="console">{latest.events.map((e) => e.text).join('\n')}</pre>
			) : (
				<p className="empty">Press Refresh to read the vault.</p>
			)}

			<Connectors connectors={connectors} session={session} jobs={jobs} busy={busy} />

			<p className="modal-note">
				Storing, granting and revoking a credential is a human keystroke, like approving — run{' '}
				<code>qa vault set &lt;name&gt;</code> in a terminal.
			</p>

		</div>
	)
}
