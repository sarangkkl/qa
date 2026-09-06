/** MCP servers: what is configured, and signing in to the ones that need it.
 *
 * Jira is the one that matters today. Note `expose_to_executor: false` on it — the
 * testing agent cannot reach Jira's tools at all, so a bug is only ever filed when a
 * human presses the button in Runs. That default is a safety property, not a setting to
 * flip casually.
 */

import type { Job, Session } from '../api/socket'
import type { ConnectorInfo } from '../api/types'

export function Connectors({
	connectors,
	session,
	jobs,
	busy,
}: {
	connectors: ConnectorInfo[]
	session: Session
	jobs: Job[]
	busy: boolean
}) {
	const lastAuth = [...jobs].reverse().find((j) => j.name === 'auth')

	if (connectors.length === 0) {
		return (
			<>
				<h3>Connectors</h3>
				<p className="empty">
					None configured. Add one under <code>mcp:</code> in <code>config.yaml</code> — the file has a commented
					example. Any server listed there becomes tools the testing agent can use.
				</p>
			</>
		)
	}

	return (
		<>
			<h3>Connectors (MCP)</h3>
			<table className="grid">
				<thead>
					<tr>
						<th>Server</th>
						<th>Command</th>
						<th>Agent may use it</th>
						<th />
					</tr>
				</thead>
				<tbody>
					{connectors.map((c) => (
						<tr key={c.name}>
							<td>
								<strong>{c.name}</strong>
								{c.project && <span className="row-sub"> · project {c.project}</span>}
								{c.needs_env.length > 0 && <div className="row-sub">needs {c.needs_env.join(', ')}</div>}
							</td>
							<td>
								<code>{c.command}</code>
							</td>
							<td>
								{c.expose_to_executor ? (
									<span className="good">yes</span>
								) : (
									<span className="row-sub" title="the testing agent cannot reach this server's tools">
										no — human only
									</span>
								)}
							</td>
							<td>
								<button disabled={busy} onClick={() => session.command('auth', { server: c.name })}>
									Sign in / check
								</button>
							</td>
						</tr>
					))}
				</tbody>
			</table>

			{lastAuth && (
				<>
					<p className="row-sub">
						Signing in opens a browser window for OAuth. Watch here for the URL and the result.
					</p>
					<pre className="console">{lastAuth.events.map((e) => e.text).join('\n') || 'working…'}</pre>
				</>
			)}
		</>
	)
}
