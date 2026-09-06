/** Scenarios, and the approve gate.
 *
 * Approving is a human keystroke and it stays one: the button opens the real `approve`
 * command, which asks over the socket with the whole scenario in the ask body, and the
 * modal makes you read it and confirm. No one-click chip. That gate is the product.
 */

import { useEffect, useState } from 'react'
import * as api from '../api/client'
import type { Connection, ScenarioDetail, ScenarioSummary } from '../api/types'
import type { Session } from '../api/socket'
import { Markdown, stripFrontmatter } from '../components/Markdown'

const STATE_LABEL: Record<string, string> = {
	ok: 'approved',
	draft: 'draft',
	stale: 'STALE',
	deprecated: 'deprecated',
}

export function Scenarios({
	connection,
	session,
	scenarios,
	busy,
	hasJira,
	onChanged,
}: {
	connection: Connection
	session: Session
	scenarios: ScenarioSummary[]
	busy: boolean
	hasJira: boolean
	onChanged: () => void
}) {
	const [selected, setSelected] = useState<string>('')
	const [ticket, setTicket] = useState('')
	const [detail, setDetail] = useState<ScenarioDetail | null>(null)
	const [error, setError] = useState('')

	useEffect(() => {
		if (!selected) return setDetail(null)
		let live = true
		api
			.scenario(connection, selected)
			.then((d) => live && setDetail(d))
			.catch((e: Error) => live && setError(e.message))
		return () => {
			live = false
		}
	}, [connection, selected, scenarios])

	const runnable = detail?.state === 'ok'

	return (
		<div className="split">
			<div className="list">
				<div className="list-head">
					<h2>Scenarios</h2>
					<button disabled={busy} onClick={() => session.command('suite', {})}>
						Run suite
					</button>
				</div>
				{hasJira && (
					<form
						className="ticket-plan"
						onSubmit={(e) => {
							e.preventDefault()
							const key = ticket.trim().toUpperCase()
							if (!key) return
							session.command('plan', { ticket: key })
							setTicket('')
						}}
					>
						<input
							value={ticket}
							onChange={(e) => setTicket(e.target.value)}
							placeholder="Plan from a ticket, e.g. PROJ-123"
							spellCheck={false}
						/>
						<button type="submit" disabled={busy || !ticket.trim()}>
							Plan
						</button>
					</form>
				)}
				{scenarios.length === 0 && <p className="empty">No scenarios yet. Ask for some in Chat.</p>}
				{scenarios.map((s) => (
					<button
						key={s.id}
						className={`row ${selected === s.id ? 'row-selected' : ''}`}
						onClick={() => setSelected(s.id)}
					>
						<span className={`chip chip-${s.state}`}>{STATE_LABEL[s.state] ?? s.state}</span>
						<span className="row-title">{s.title}</span>
						<span className="row-sub">{s.id}</span>
						{s.last_verdict && <span className={`verdict verdict-${s.last_verdict.toLowerCase()}`}>{s.last_verdict}</span>}
					</button>
				))}
			</div>

			<div className="detail">
				{error && <p className="error">{error}</p>}
				{!detail && <p className="empty">Pick a scenario.</p>}
				{detail && (
					<>
						<div className="detail-head">
							<div>
								<h2>{detail.title}</h2>
								<p className="row-sub">
									{detail.id}
									{detail.ticket && ` · ${detail.ticket}`}
									{detail.approved_by && ` · approved by ${detail.approved_by} ${detail.approved_at}`}
								</p>
							</div>
							<div className="detail-actions">
								<button
									className="primary"
									disabled={busy || !runnable}
									title={runnable ? '' : 'only an approved scenario can run'}
									onClick={() => session.command('run', { id: detail.id })}
								>
									Run
								</button>
								<button
									disabled={busy || detail.state === 'ok'}
									onClick={() => {
										session.command('approve', { id: detail.id })
										setTimeout(onChanged, 400)
									}}
								>
									Review &amp; approve…
								</button>
							</div>
						</div>

						{detail.state === 'stale' && (
							<p className="warn">
								Edited after approval, so it has left the suite. Re-approve it to put it back — the runner
								refuses it until you do.
							</p>
						)}
						{detail.state === 'draft' && (
							<p className="warn">A draft has never been approved. It will not run and is not in the suite.</p>
						)}

						<Markdown source={stripFrontmatter(detail.body)} />
					</>
				)}
			</div>
		</div>
	)
}
