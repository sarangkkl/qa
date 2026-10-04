/** Talking to Claude Code, which does the thinking on the user's own login.
 *
 * The sessions listed are Claude Code's own for this folder, titles included, so a chat started
 * in the terminal shows up here and one started here resumes there. A turn in flight is drawn
 * from the live stream; once it is saved in the session file, the file takes over - one source
 * at a time, so nothing is ever on screen twice.
 */

import { FileSpreadsheet, MessageSquare, SquarePen } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
import * as api from '../api/client'
import type { Job } from '../api/socket'
import type { ChatDetail, ChatSummary, Connection, LibraryTest, WorkspaceState } from '../api/types'
import { Composer } from '../components/chat/Composer'
import { toItems, Transcript, type Item, type SaveState } from '../components/chat/Transcript'

export interface Turn {
	chat: string
	text: string
}

function ago(iso: string): string {
	const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
	if (!Number.isFinite(minutes)) return ''
	if (minutes < 1) return 'just now'
	if (minutes < 60) return `${minutes}m ago`
	if (minutes < 60 * 24) return `${Math.round(minutes / 60)}h ago`
	return `${Math.round(minutes / 60 / 24)}d ago`
}

/** A turn is in the session file once it finished cleanly or was stopped; a failed one may
 * never have reached it, so it stays drawn from the stream with its error. */
const saved = (job: Job) => job.done && (job.code === 0 || job.cancelled)

export function Chat({
	connection,
	state,
	jobs,
	chatId,
	turns,
	titleTick,
	busy,
	running,
	canRun,
	onChat,
	onSay,
	onStop,
	onApprove,
	onOpenScenario,
	library,
	saves,
	onSave,
	onOpenRun,
}: {
	connection: Connection
	state: WorkspaceState
	jobs: Job[]
	chatId: string
	turns: RefObject<Map<string, Turn>>
	titleTick: number
	busy: boolean
	running: Job | null
	canRun: boolean
	onChat: (id: string) => void
	onSay: (text: string) => void
	onStop: (id: string) => void
	onApprove: (id: string) => void
	onOpenScenario: (id: string) => void
	library: LibraryTest[]
	/** run name -> the library-save job for it, kept above so it survives tab switches */
	saves: RefObject<Map<string, string>>
	onSave: (run: string) => void
	onOpenRun: (run: string) => void
}) {
	const [list, setList] = useState<ChatSummary[]>([])
	const [history, setHistory] = useState<ChatDetail | null>(null)
	const [absorbed, setAbsorbed] = useState<Set<string>>(new Set())
	const log = useRef<HTMLDivElement>(null)
	// A file dropped anywhere on the chat goes to the composer to upload.
	const [dropped, setDropped] = useState<File | null>(null)
	const [dragging, setDragging] = useState(false)
	const stick = useRef(true)

	const finished = jobs.filter((j) => j.done).length

	useEffect(() => {
		api
			.chats(connection)
			.then((r) => setList(r.chats))
			.catch(() => setList([]))
	}, [connection, finished, titleTick, chatId])

	useEffect(() => {
		if (!chatId) return setHistory(null)
		let live = true
		const done = jobs.filter(saved).map((j) => j.id)
		api
			.chat(connection, chatId)
			.then((h) => {
				if (!live) return
				setHistory(h)
				setAbsorbed(new Set(done))
			})
			.catch(() => live && setHistory(null))
		return () => {
			live = false
		}
		// `jobs` is read for its finished ids at fetch time; refetching on every streamed line is not wanted.
	}, [connection, chatId, finished])

	const liveJobs = jobs.filter((j) => turns.current.get(j.id)?.chat === chatId && !(saved(j) && absorbed.has(j.id)))

	const items = useMemo(() => {
		const out: Item[] = history?.id === chatId ? toItems(history.messages) : []
		for (const job of liveJobs) {
			out.push({ kind: 'user', text: turns.current.get(job.id)?.text ?? '' })
			out.push(...toItems(job.messages))
			const said = job.events.map((e) => e.text).join('\n').trim()
			if (job.done && !job.cancelled && job.code !== 0) out.push({ kind: 'error', text: job.error || said || 'That turn failed.' })
		}
		return out
	}, [history, chatId, jobs, absorbed])

	const inLibrary = useMemo(() => new Set(library.map((t) => t.id)), [library])
	const save = (run: string, scenarioId: string): SaveState => {
		if (library.some((t) => t.id === scenarioId && t.recorded_from === run)) return { kind: 'saved' }
		const job = jobs.find((j) => j.id === saves.current.get(run))
		if (job && !job.done) return { kind: 'saving' }
		if (job && job.code !== 0) {
			const said = job.events.map((e) => e.text).find((t) => t.startsWith('Not saved')) ?? ''
			return { kind: 'failed', reason: said || job.error || 'Not saved.' }
		}
		return inLibrary.has(scenarioId) ? { kind: 'other' } : { kind: 'idle' }
	}

	const mine = running && turns.current.get(running.id)?.chat === chatId ? running : null
	const thinking = mine !== null

	// Follow the conversation only while you are at the bottom of it: scrolling up to read
	// something must not be yanked away by the next streamed line.
	useLayoutEffect(() => {
		const el = log.current
		if (el && stick.current) el.scrollTop = el.scrollHeight
	}, [items, thinking])

	useEffect(() => {
		stick.current = true
	}, [chatId])

	const title = list.find((c) => c.id === chatId)?.title || history?.title || (chatId ? 'Untitled chat' : 'New chat')
	const empty = items.length === 0 && !thinking
	const suggestions = [
		'What do you know about this app so far?',
		'Plan scenarios for the login flow',
		state.base_url ? `Explore ${state.base_url} and learn the main pages` : 'Explore the app and learn the main pages',
	]

	return (
		<div className="split">
			<div className="list">
				<div className="list-head">
					<h2>Chats</h2>
					<button className="icon-btn" title="New chat (⌘N)" onClick={() => onChat('')}>
						<SquarePen size={16} />
					</button>
				</div>
				{list.map((c) => (
					<button key={c.id} className={`row ${chatId === c.id ? 'row-selected' : ''}`} onClick={() => onChat(c.id)}>
						<span className="row-title row-clip">{c.title || 'Untitled chat'}</span>
						<span className="row-sub">{ago(c.updated)}</span>
					</button>
				))}
				{list.length === 0 && <p className="empty">No conversations yet.</p>}
			</div>

			<div
				className="detail chat"
				onDragOver={(e) => {
					if (!e.dataTransfer.types.includes('Files')) return
					e.preventDefault()
					setDragging(true)
				}}
				onDragLeave={(e) => {
					if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false)
				}}
				onDrop={(e) => {
					e.preventDefault()
					setDragging(false)
					const file = e.dataTransfer.files[0]
					if (file && canRun) setDropped(file)
				}}
			>
				{dragging && (
					<div className="drop-overlay">
						<FileSpreadsheet size={28} />
						<strong>Drop to attach test cases</strong>
						<span className="row-sub">.xlsx, .csv or .tsv — Kiwame imports them as scenarios</span>
					</div>
				)}
				<header className="chat-head">
					<MessageSquare size={16} />
					<h2 className="row-clip">{title}</h2>
				</header>

				<div
					className="chat-log"
					ref={log}
					onScroll={(e) => {
						const el = e.currentTarget
						stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
					}}
				>
					<div className="chat-column">
						{empty && (
							<div className="chat-empty">
								<h2>What should we test?</h2>
								<p className="row-sub">
									Kiwame reads the app map, drafts scenarios for you to approve, and runs them in a real browser.
								</p>
								{suggestions.map((s) => (
									<button key={s} className="suggestion" disabled={busy || !canRun} onClick={() => onSay(s)}>
										{s}
									</button>
								))}
							</div>
						)}
						<Transcript
							connection={connection}
							items={items}
							live={thinking}
							scenarios={state.scenarios}
							busy={busy}
							canRun={canRun}
							onApprove={onApprove}
							onRun={(ids) =>
								onSay(
									ids.length === 1
										? `Run the approved scenario ${ids[0]}.`
										: `Run these approved scenarios in order, one run each: ${ids.join(', ')}.`,
								)
							}
							onOpen={onOpenScenario}
							save={save}
							onSave={onSave}
							onOpenRun={onOpenRun}
							library={inLibrary}
						/>
						{thinking && (
							<div className="thinking">
								<span />
								<span />
								<span />
							</div>
						)}
					</div>
				</div>

				<div className="chat-column">
					<Composer
						connection={connection}
						incoming={dropped}
						onTaken={() => setDropped(null)}
						onSend={onSay}
						onStop={() => mine && onStop(mine.id)}
						running={thinking}
						disabled={busy || !canRun}
						hint={busy && !thinking ? 'Waiting for the current job to finish…' : 'Ask Kiwame to plan, run or explore…'}
					/>
				</div>
			</div>
		</div>
	)
}
