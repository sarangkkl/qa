/** Plain English in, work out.
 *
 * Two ways in, both ending at the same registry: prose goes to `say` and is routed by the
 * agent; a line starting with `/` is sent verbatim and parsed server-side by the terminal's
 * own parser. The palette is built from the commands `GET /workspace` served, never from a
 * hardcoded list, so a new command shows up here the day it is added.
 */

import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { StopButton } from '../components/StopButton'
import * as api from '../api/client'
import type { Session, Job } from '../api/socket'
import type { ChatDetail, ChatSummary, CommandInfo, Connection } from '../api/types'

/** "/run <id> [--model]" - the same shape /help prints in the terminal. */
function usage(cmd: CommandInfo): string {
	const parts = cmd.params.map((p) => (p.required ? `<${p.name}>` : p.flag ? `[--${p.name}]` : `[${p.name}]`))
	return `/${cmd.name}${parts.length ? ' ' + parts.join(' ') : ''}`
}

export function Chat({
	connection,
	session,
	jobs,
	chatId,
	commands,
	mine,
	stopping,
	onStop,
	onForce,
	onChat,
}: {
	connection: Connection
	session: Session
	jobs: Job[]
	chatId: string
	commands: CommandInfo[]
	mine: RefObject<Set<string>>
	stopping: string
	onStop: (id: string) => void
	onForce: () => void
	onChat: (id: string) => void
}) {
	const [text, setText] = useState('')
	const [history, setHistory] = useState<ChatDetail | null>(null)
	const [list, setList] = useState<ChatSummary[]>([])
	const [help, setHelp] = useState<string[]>([])
	const bottom = useRef<HTMLDivElement>(null)

	useEffect(() => {
		api.chats(connection).then((r) => setList(r.chats)).catch(() => setList([]))
	}, [connection, jobs.length])

	// Refetch when a job FINISHES, not only when one starts. Keying on `jobs.length` left the
	// transcript a whole message behind, so a reply only turned up in it once you sent the next
	// thing - and then sat alongside its own live bubble.
	const finished = jobs.filter((j) => j.done).length

	useEffect(() => {
		if (!chatId) return setHistory(null)
		api.chat(connection, chatId).then(setHistory).catch(() => setHistory(null))
	}, [connection, chatId, jobs.length, finished])

	useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth' }), [jobs, history])

	// `/help` and `/exit` are terminal meta commands the server refuses; answer them here
	// rather than sending a frame that can only come back as a usage error.
	const runnable = useMemo(() => commands.filter((c) => !c.shell_only), [commands])
	const suggestions = useMemo(() => {
		if (!text.startsWith('/') || text.includes(' ')) return []
		const typed = text.slice(1).toLowerCase()
		return runnable.filter((c) => c.name.startsWith(typed))
	}, [text, runnable])

	const send = () => {
		const message = text.trim()
		if (!message) return
		setHelp([])
		if (message === '/help' || message === '/?') {
			setHelp(runnable.map((c) => `${usage(c).padEnd(34)} ${c.help}`))
			setText('')
			return
		}
		if (message === '/exit') {
			setHelp(['Close the window to end the session.'])
			setText('')
			return
		}
		if (message.startsWith('/')) mine.current.add(session.commandLine(message, chatId))
		else mine.current.add(session.say(message, chatId || undefined))
		setText('')
	}

	// The agent's reply is written to two places - streamed as a job event AND saved as a turn -
	// so once the transcript catches up it is on screen twice, which reads as the agent
	// repeating itself after every message. Drop the bubble only when the transcript demonstrably
	// carries everything it said.
	//
	// Line by line rather than "it is a finished say", because a `say` that ran a command also
	// streamed that command's output, and the transcript keeps only the command name and its
	// exit code. Dropping those bubbles would silently swallow a /correct diff. If any line is
	// missing from the transcript, the bubble stays.
	const spoken = useMemo(() => {
		const said = new Set((history?.turns ?? []).map((t) => t.text.trim()).filter(Boolean))
		return (job: Job) => job.done && job.events.every((e) => !e.text.trim() || said.has(e.text.trim()))
	}, [history])
	const live = jobs.filter((j) => mine.current.has(j.id) && !spoken(j))

	return (
		<div className="split">
			<div className="list">
				<div className="list-head">
					<h2>Chats</h2>
					<button onClick={() => onChat('')}>New</button>
				</div>
				{list.map((c) => (
					<button key={c.id} className={`row ${chatId === c.id ? 'row-selected' : ''}`} onClick={() => onChat(c.id)}>
						<span className="row-title">{c.title || '(untitled)'}</span>
						<span className="row-sub">
							{c.turns} turns · {c.updated.replace('T', ' ')}
						</span>
					</button>
				))}
				{list.length === 0 && <p className="empty">No conversations yet.</p>}
			</div>

			<div className="detail chat">
				<div className="chat-log">
					{history?.turns.map((t, i) => (
						<div key={i} className={`bubble bubble-${t.role}`}>
							<p>{t.text}</p>
							{t.command && (
								<p className="bubble-meta">
									ran <code>{t.command}</code> → exit {t.exit}
								</p>
							)}
						</div>
					))}

					{help.length > 0 && (
						<div className="bubble bubble-run">
							{help.map((row, i) => (
								<p key={i} className="line line-log">
									{row}
								</p>
							))}
						</div>
					)}

					{live.map((job) => (
						<div key={job.id} className="bubble bubble-run">
							{job.events.map((e, i) => (
								<p key={i} className={`line line-${e.kind}`}>
									{e.text}
								</p>
							))}
							{!job.done && (
								<p className="line line-progress">
									<span>working…</span>
									<StopButton
										jobId={job.id}
										stopping={stopping === job.id}
										onStop={onStop}
										onForce={onForce}
										compact
									/>
								</p>
							)}
							{job.cancelled && <p className="line line-log">⏹ stopped — evidence saved.</p>}
							{job.error && <p className="line line-error">{job.error}</p>}
						</div>
					))}
					<div ref={bottom} />
				</div>

				{suggestions.length > 0 && (
					<div className="palette">
						{suggestions.map((c) => (
							<button key={c.name} className="palette-row" onClick={() => setText(`/${c.name} `)}>
								<code>{usage(c)}</code>
								<span className="row-sub">
									{c.help}
									{c.human_only ? ' · you confirm' : ''}
								</span>
							</button>
						))}
					</div>
				)}

				<form
					className="composer"
					onSubmit={(e) => {
						e.preventDefault()
						send()
					}}
				>
					<input
						value={text}
						onChange={(e) => setText(e.target.value)}
						onKeyDown={(e) => {
							const first = suggestions[0]
							if (e.key === 'Tab' && first) {
								e.preventDefault()
								setText(`/${first.name} `)
							}
							if (e.key === 'Escape') setText('')
						}}
						placeholder='e.g. "plan scenarios for the login flow", or /crawl --pages 5'
					/>
					<button type="submit" className="primary">
						Send
					</button>
				</form>
			</div>
		</div>
	)
}
