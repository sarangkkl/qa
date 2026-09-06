/** The vault, read-only plus the human-only actions.
 *
 * There is no reveal button and there will not be one: it is the single feature that turns
 * a leak into a catastrophe, and nothing here needs it.
 */

import type { Job, Session } from '../api/socket'
import type { ConnectorInfo, Health, WorkspaceState } from '../api/types'
import { ModelPicker } from '../components/ModelPicker'
import { Connectors } from './Connectors'

export function Credentials({
	session,
	jobs,
	health,
	state,
	connectors,
	busy,
}: {
	session: Session
	jobs: Job[]
	health: Health | null
	state: WorkspaceState
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

			<ModelPicker
				session={session}
				jobs={jobs}
				providers={state.providers}
				aliases={state.aliases}
				roles={state.models}
				health={health}
				root={state.root}
			/>

			{health && (
				<table className="grid">
					<thead>
						<tr>
							<th>Role</th>
							<th>Model</th>
							<th>Provider</th>
							<th>Keys</th>
						</tr>
					</thead>
					<tbody>
						{Object.entries(health.roles).map(([role, info]) => (
							<tr key={role}>
								<td>{role}</td>
								<td>{info.model}</td>
								<td>{info.provider}</td>
								<td>{info.missing_keys.length ? <span className="bad">missing {info.missing_keys.join(', ')}</span> : <span className="good">ok</span>}</td>
							</tr>
						))}
					</tbody>
				</table>
			)}

			<p className="modal-note">
				Storing, granting and revoking a credential is a human keystroke, like approving — run{' '}
				<code>qa vault set &lt;name&gt;</code> in a terminal.
			</p>

			<Connectors connectors={connectors} session={session} jobs={jobs} busy={busy} />
		</div>
	)
}
