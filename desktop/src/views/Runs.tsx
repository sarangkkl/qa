/** Evidence. A verdict nobody can inspect is just a claim. */

import { useEffect, useState } from 'react'
import * as api from '../api/client'
import type { Session } from '../api/socket'
import type { Connection, RunDetail } from '../api/types'
import { Evidence } from '../components/Evidence'
import { Report } from '../components/Report'

export function Runs({
	connection,
	runs,
	session,
	busy,
	hasJira,
}: {
	connection: Connection
	runs: string[]
	session: Session
	busy: boolean
	hasJira: boolean
}) {
	const [selected, setSelected] = useState('')
	const [detail, setDetail] = useState<RunDetail | null>(null)
	const [report, setReport] = useState('')
	const [step, setStep] = useState(0)

	const current = selected && runs.includes(selected) ? selected : (runs[0] ?? '')

	useEffect(() => {
		if (!current) return
		setReport('')
		api
			.run(connection, current)
			.then((d) => {
				setDetail(d)
				const path = d.artifacts.report ?? (current.startsWith('suite--') ? `/artifacts/runs/${current}/suite.md` : '')
				return path ? api.text(connection, path) : ''
			})
			.then(setReport)
			.catch(() => setReport(''))
	}, [connection, current])

	const failures = detail?.result?.result?.steps.filter((s) => s.verdict !== 'pass') ?? []

	return (
		<div className="split">
			<div className="list">
				<div className="list-head">
					<h2>Runs</h2>
				</div>
				{runs.length === 0 && <p className="empty">No runs yet.</p>}
				{runs.map((name) => (
					<button key={name} className={`row ${current === name ? 'row-selected' : ''}`} onClick={() => setSelected(name)}>
						<span className="row-title">{name.startsWith('suite--') ? `🧪 ${name}` : name}</span>
					</button>
				))}
			</div>

			<div className="detail">
				{!detail && <p className="empty">Pick a run.</p>}
				{detail && (
					<>
						<div className="detail-head">
							<div>
								<h2>{detail.name}</h2>
								<p className="row-sub">{detail.steps} steps</p>
							</div>
							{hasJira && failures.length > 0 && (
								<div className="detail-actions">
									{failures.length > 1 && (
										<select value={step} onChange={(e) => setStep(Number(e.target.value))}>
											<option value={0}>pick a failed step…</option>
											{failures.map((f) => (
												<option key={f.step} value={f.step}>
													step {f.step} · {f.verdict}
												</option>
											))}
										</select>
									)}
									<button
										disabled={busy || (failures.length > 1 && step === 0)}
										title="composes the bug and shows it for confirmation before filing"
										onClick={() =>
											session.command('file-bug', {
												run: detail.name,
												...(failures.length > 1 ? { step } : {}),
											})
										}
									>
										File a bug…
									</button>
								</div>
							)}
						</div>

						{report && <Report source={report} />}
						<Evidence connection={connection} detail={detail} />
					</>
				)}
			</div>
		</div>
	)
}
