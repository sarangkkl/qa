/** The one stop control, mounted wherever a run can be seen.
 *
 * Three states, because stopping has three honest answers:
 *   Stop              - ask the agent to halt and cancel the task
 *   stopping…         - it was asked; the run is closing the browser and saving evidence
 *   Force stop        - kill the sidecar outright, when asking did not work
 *
 * Force is always offered, never automatic, and takes two clicks. It discards the session's
 * credentials and any pending prompt, and there is no undoing that on a timer.
 */

import { useEffect, useState } from 'react'

export function StopButton({
	jobId,
	stopping,
	onStop,
	onForce,
	compact,
}: {
	jobId: string
	stopping: boolean
	onStop: (id: string) => void
	onForce: () => void
	compact?: boolean
}) {
	const [confirming, setConfirming] = useState(false)

	// A half-pressed Force must not stay armed while you go and do something else.
	useEffect(() => {
		if (!confirming) return
		const timer = setTimeout(() => setConfirming(false), 5000)
		return () => clearTimeout(timer)
	}, [confirming])

	useEffect(() => setConfirming(false), [jobId])

	return (
		<span className={`stop ${compact ? 'stop-compact' : ''}`}>
			<button className="danger" disabled={stopping} onClick={() => onStop(jobId)}>
				{stopping ? 'stopping…' : 'Stop'}
			</button>
			<button
				className={confirming ? 'danger' : ''}
				title="Kills the sidecar. Loses this session's credentials, any pending prompt, and the video."
				onClick={() => (confirming ? onForce() : setConfirming(true))}
			>
				{confirming ? 'Click again to force' : 'Force'}
			</button>
		</span>
	)
}
