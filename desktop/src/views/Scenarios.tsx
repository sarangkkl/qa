/** Scenarios, and the approve gate.
 *
 * Approving is a human keystroke and it stays one: the button opens the real `approve`
 * command, which asks over the socket with the whole scenario in the ask body, and the
 * modal makes you read it and confirm. No one-click chip. That gate is the product.
 */

import { useEffect, useRef, useState } from 'react'
import * as api from '../api/client'
import type { Connection, RunDetail, ScenarioDetail, ScenarioSummary } from '../api/types'
import type { Session } from '../api/socket'
import { Evidence } from '../components/Evidence'
import { Markdown, stripFrontmatter } from '../components/Markdown'
import { Report } from '../components/Report'

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
	const [view, setView] = useState<'spec' | 'result'>('spec')
	const [report, setReport] = useState('')
	const [run, setRun] = useState<RunDetail | null>(null)
	// What this pane last settled on. The scenario id is half of it on purpose: without it, a
	// scenario's first ever run is indistinguishable from opening one that had already run.
	const seen = useRef({ id: '', run: '' })

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

	// Picking a different scenario always lands on its spec.
	useEffect(() => setView('spec'), [selected])

	// latest_run_dir only names a run that wrote a results.md, so this path always exists.
	// The run detail comes too: a verdict you cannot inspect is just a claim, so the video, the
	// gif, the step screenshots and the transcript belong next to the report, not a tab away.
	const lastRun = detail?.last_run ?? ''
	useEffect(() => {
		setReport('')
		setRun(null)
		if (!lastRun) return
		let live = true
		api
			.text(connection, `/artifacts/runs/${encodeURIComponent(lastRun)}/results.md`)
			.then((text) => live && setReport(text))
			.catch(() => undefined)
		api
			.run(connection, lastRun)
			.then((d) => live && setRun(d))
			.catch(() => undefined)
		return () => {
			live = false
		}
	}, [connection, lastRun])

	// A run finishing refreshes the workspace, which re-fetches this detail, which lands here as
	// a new last_run. Showing it is the whole point of having run: you should not go looking.
	useEffect(() => {
		if (!detail) return
		const before = seen.current
		if (before.id === detail.id && lastRun && lastRun !== before.run) setView('result')
		seen.current = { id: detail.id, run: lastRun }
	}, [detail, lastRun])

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

						<div className="detail-tabs">
							<button className={view === 'spec' ? 'tab-on' : ''} onClick={() => setView('spec')}>
								Spec
							</button>
							<button
								className={view === 'result' ? 'tab-on' : ''}
								disabled={!lastRun}
								title={lastRun ? lastRun : 'this scenario has not been run yet'}
								onClick={() => setView('result')}
							>
								Result
								{detail.last_verdict && (
									<span className={`verdict verdict-${detail.last_verdict.toLowerCase()}`}>
										{detail.last_verdict}
									</span>
								)}
							</button>
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

						{view === 'spec' && <Markdown source={stripFrontmatter(detail.body)} />}
						{view === 'result' && (report ? <Report source={report} /> : <p className="empty">Loading the report…</p>)}
						{view === 'result' && run && <Evidence connection={connection} detail={run} />}
					</>
				)}
			</div>
		</div>
	)
}
