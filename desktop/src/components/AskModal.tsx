/** The human-in-the-loop prompt. This is the product's trust story rendered as a dialog.
 *
 * Three rules it must not break:
 * - a secret input is write-only: the value is sent and the field cleared, never stored
 *   in component state that survives, never logged, never echoed;
 * - a confirm shows its `body` in full - approving a scenario means seeing the scenario;
 * - dismissing is denying, because a closed dialog resolves the ask with '' server-side.
 */

import { useEffect, useRef, useState } from 'react'
import { StopButton } from './StopButton'
import type { AskFrame } from '../api/types'

const CHOICE_LABELS: Record<string, string> = {
	y: 'Allow once',
	s: 'Allow this session',
	a: 'Allow always',
	n: 'Deny',
}

export function AskModal({
	ask,
	onAnswer,
	stopping,
	onStop,
	onForce,
}: {
	ask: AskFrame
	onAnswer: (value: string) => void
	stopping: string
	onStop: () => void
	onForce: () => void
}) {
	const [value, setValue] = useState('')
	const input = useRef<HTMLInputElement>(null)

	useEffect(() => {
		setValue('')
		input.current?.focus()
	}, [ask.id])

	// Escape denies this one question - the documented "dismissing is denying". Stopping the
	// whole run is the button below, deliberately not the same gesture.
	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if (e.key === 'Escape') onAnswer('')
		}
		window.addEventListener('keydown', onKey)
		return () => window.removeEventListener('keydown', onKey)
	}, [onAnswer])

	const submit = (answer: string) => {
		setValue('') // clear before anything else can read it back
		onAnswer(answer)
	}

	return (
		<div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={ask.prompt}>
			<div className={`modal ${ask.interrupt ? 'modal-interrupt' : ''}`}>
				{ask.interrupt && (
					<div className="modal-head">
						<span className="modal-tag">the run is waiting for you</span>
						{/* This backdrop covers the Live pane, so without a stop here a parked run is
						    a dead end: answering the question is the only way out, and it just asks
						    again. Deny answers the question; this ends the run. */}
						<StopButton
							jobId={ask.job}
							stopping={stopping === ask.job}
							onStop={onStop}
							onForce={onForce}
							compact
						/>
					</div>
				)}
				<p className="modal-prompt">{ask.prompt.trim()}</p>

				{ask.body && <pre className="modal-body">{ask.body.trim()}</pre>}

				{ask.kind === 'choice' && (
					<div className="modal-actions">
						{ask.options.map((option) => (
							<button
								key={option}
								className={option === 'n' ? 'danger' : option === 'a' ? 'primary' : ''}
								onClick={() => submit(option)}
							>
								{CHOICE_LABELS[option] ?? option}
							</button>
						))}
					</div>
				)}

				{ask.kind === 'confirm' && (
					<div className="modal-actions">
						<button className="danger" onClick={() => submit('n')}>
							No
						</button>
						<button className="primary" onClick={() => submit('y')}>
							Yes
						</button>
					</div>
				)}

				{(ask.kind === 'text' || ask.kind === 'secret') && (
					<form
						onSubmit={(e) => {
							e.preventDefault()
							submit(value)
						}}
					>
						<input
							ref={input}
							className="modal-input"
							type={ask.kind === 'secret' ? 'password' : 'text'}
							autoComplete={ask.kind === 'secret' ? 'off' : undefined}
							spellCheck={false}
							value={value}
							onChange={(e) => setValue(e.target.value)}
							placeholder={ask.kind === 'secret' ? `value for "${ask.key}"` : 'your answer'}
						/>
						{ask.kind === 'secret' && (
							<p className="modal-note">
								Sent straight to the browser under test. Never stored here, never written to disk, never
								shown to the model.
							</p>
						)}
						<div className="modal-actions">
							<button type="button" onClick={() => submit('')}>
								Skip
							</button>
							<button type="submit" className="primary">
								Send
							</button>
						</div>
					</form>
				)}
			</div>
		</div>
	)
}
