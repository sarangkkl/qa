/** Watching the agent work.
 *
 * Two sources, in order of preference: the CDP screencast (~20 fps) when the sidecar is
 * streaming, and the per-step screenshots otherwise. Frames are dropped when we fall
 * behind, by design, so gaps are expected and not a bug to paper over.
 *
 * Below the picture, the terminal feed - because most of the time there is no picture. Only
 * a browser run screencasts; a chat turn, a plan or a correction have nothing to show, and
 * for those the log is the only progress there is.
 */

import { useEffect, useState } from 'react'
import { artifactUrl } from '../api/connection'
import { StopButton } from '../components/StopButton'
import { TerminalFeed } from '../components/TerminalFeed'
import type { Job } from '../api/socket'
import type { Connection, ImageFrame } from '../api/types'

export function LivePane({
	connection,
	frame,
	jobs,
	running,
	stopping,
	onStop,
	onForce,
}: {
	connection: Connection
	frame: ImageFrame | null
	jobs: Job[]
	running: Job | null
	stopping: string
	onStop: (id: string) => void
	onForce: () => void
}) {
	const last = jobs[jobs.length - 1]
	const current = running ?? last
	const steps = current?.events.filter((e) => e.kind === 'step') ?? []
	const [pinned, setPinned] = useState<number | null>(null)

	useEffect(() => setPinned(null), [running?.id])

	// The server sends an artifact path (`/artifacts/runs/…`); it has to be signed like every
	// other image in the app. It used to send a raw filesystem path in a temp directory, which
	// no <img> could ever load - that broken image on a black stage was "the live view is
	// not working". A step with nothing usable omits the key, so this stays null.
	const shot = (index: number): string | null => {
		const value = steps[index]?.data['screenshot']
		return typeof value === 'string' && value ? artifactUrl(connection, value) : null
	}
	const chosen = pinned ?? (steps.length ? steps.length - 1 : null)
	const still = chosen === null ? null : shot(chosen)
	const showLive = frame !== null && pinned === null

	// Announced once by the sidecar and otherwise invisible: LivePane filters to `step` events,
	// so a screencast that could not start left a black rectangle and no reason for it.
	const unavailable = current?.events.find((e) => e.kind === 'progress' && e.data['screencast'] === false)

	return (
		<aside className="live">
			<div className="live-head">
				<h3>Live</h3>
				{running ? (
					<StopButton
						jobId={running.id}
						stopping={stopping === running.id}
						onStop={onStop}
						onForce={onForce}
						compact
					/>
				) : (
					<span className="row-sub">{last?.cancelled ? '⏹ stopped' : 'idle'}</span>
				)}
			</div>

			<div className="live-stage">
				{showLive && <img alt="live browser" src={`data:image/${frame.format};base64,${frame.image}`} />}
				{!showLive && still && <img alt={`step ${chosen! + 1}`} src={still} />}
				{!showLive && !still && (
					<p className="empty">{unavailable ? unavailable.text : 'Start a run to watch it here.'}</p>
				)}
			</div>

			{steps.length > 0 && (
				<div className="filmstrip">
					{steps.map((s, i) => (
						<button
							key={i}
							className={`thumb ${chosen === i ? 'thumb-on' : ''}`}
							title={s.text}
							onClick={() => setPinned(pinned === i ? null : i)}
						>
							{shot(i) ? <img alt="" src={shot(i)!} /> : <span>{i + 1}</span>}
						</button>
					))}
				</div>
			)}

			{steps.length > 0 && chosen !== null && <p className="live-caption">{steps[chosen]?.text}</p>}

			<TerminalFeed jobs={jobs} running={running} />
		</aside>
	)
}
