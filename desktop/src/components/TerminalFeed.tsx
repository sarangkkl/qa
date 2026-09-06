/** What the agent is doing, right now.
 *
 * `working…` was the whole progress story, so a slow job and a stuck one looked identical and
 * the only way to see anything was to run from a terminal instead. This is that terminal:
 * every event from every job, browser-use's own narration included.
 *
 * Collapsed it is one row, and that row is the point — an elapsed timer and the latest line
 * answer "is it stuck?" without a click. The timer is client-side, so it keeps ticking through
 * the long silences the server genuinely has nothing to say during: a chat turn is one blocking
 * LLM call that emits nothing until it returns.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { Job } from '../api/socket'

/** A long crawl streams thousands of lines; only the tail is worth putting in the DOM. */
const MAX_LINES = 500

function elapsed(since: number, now: number): string {
	const total = Math.max(0, Math.floor((now - since) / 1000))
	return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export function TerminalFeed({ jobs, running }: { jobs: Job[]; running: Job | null }) {
	const [open, setOpen] = useState(false)
	const [now, setNow] = useState(() => Date.now())
	const startedAt = useRef<number | null>(null)
	const bottom = useRef<HTMLDivElement>(null)

	// One interval, alive only while something is running - an idle window should not tick.
	useEffect(() => {
		if (!running) {
			startedAt.current = null
			return
		}
		startedAt.current ??= Date.now()
		setNow(Date.now())
		const timer = setInterval(() => setNow(Date.now()), 1000)
		return () => clearInterval(timer)
	}, [running])

	const lines = useMemo(() => {
		const all = jobs.flatMap((job) => job.events.map((e) => ({ kind: e.kind, text: e.text, id: job.id })))
		return all.filter((l) => l.text.trim()).slice(-MAX_LINES)
	}, [jobs])

	useEffect(() => {
		if (open) bottom.current?.scrollIntoView({ block: 'nearest' })
	}, [lines, open])

	const latest = lines[lines.length - 1]?.text ?? ''
	const ticking = running !== null && startedAt.current !== null

	return (
		<div className={`terminal ${open ? 'terminal-open' : ''}`}>
			<button className="terminal-head" onClick={() => setOpen(!open)} aria-expanded={open}>
				<span className="terminal-caret">{open ? '▾' : '▸'}</span>
				<span className="terminal-title">Terminal</span>
				{ticking && <span className="terminal-clock">● {elapsed(startedAt.current!, now)}</span>}
				{!open && <span className="terminal-latest">{latest || (ticking ? 'starting…' : 'idle')}</span>}
			</button>

			{open && (
				<div className="terminal-body">
					{lines.length === 0 && <p className="empty">Nothing yet. Run something and it shows up here.</p>}
					{lines.map((line, i) => (
						<p key={i} className={`line line-${line.kind}`}>
							{line.text}
						</p>
					))}
					<div ref={bottom} />
				</div>
			)}
		</div>
	)
}
