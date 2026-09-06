/** Watching the agent work.
 *
 * Two sources, in order of preference: the CDP screencast (~20 fps) when the sidecar is
 * streaming, and the per-step screenshots otherwise. Frames are dropped when we fall
 * behind, by design, so gaps are expected and not a bug to paper over.
 */

import { useEffect, useState } from 'react'
import { StopButton } from '../components/StopButton'
import type { Job } from '../api/socket'
import type { ImageFrame } from '../api/types'

export function LivePane({
	frame,
	jobs,
	running,
	stopping,
	onStop,
	onForce,
}: {
	frame: ImageFrame | null
	jobs: Job[]
	running: Job | null
	stopping: string
	onStop: (id: string) => void
	onForce: () => void
}) {
	const last = jobs[jobs.length - 1]
	const steps = (running ?? last)?.events.filter((e) => e.kind === 'step') ?? []
	const [pinned, setPinned] = useState<number | null>(null)

	useEffect(() => setPinned(null), [running?.id])

	const shot = (index: number): string | null => {
		const value = steps[index]?.data['screenshot']
		return typeof value === 'string' ? value : null
	}
	const chosen = pinned ?? (steps.length ? steps.length - 1 : null)
	const still = chosen === null ? null : shot(chosen)
	const showLive = frame !== null && pinned === null

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
				{!showLive && !still && <p className="empty">Start a run to watch it here.</p>}
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
		</aside>
	)
}
