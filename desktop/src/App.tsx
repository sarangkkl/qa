import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from './api/client'
import { connect, forceStop, inTauri, type InitOptions } from './api/connection'
import { Session, type Job } from './api/socket'
import type { AskFrame, Autonomy, Connection, Health, ImageFrame, WorkspaceState } from './api/types'
import { AskModal } from './components/AskModal'
import { AppMap } from './views/AppMap'
import { Chat } from './views/Chat'
import { Credentials } from './views/Credentials'
import { LivePane } from './views/LivePane'
import { Runs } from './views/Runs'
import { Scenarios } from './views/Scenarios'
import { WorkspacePicker } from './views/WorkspacePicker'

type Tab = 'chat' | 'scenarios' | 'appmap' | 'flows' | 'runs' | 'settings'
const TABS: { id: Tab; label: string }[] = [
	{ id: 'chat', label: 'Chat' },
	{ id: 'scenarios', label: 'Scenarios' },
	{ id: 'appmap', label: 'App map' },
	{ id: 'flows', label: 'Flows' },
	{ id: 'runs', label: 'Runs' },
	{ id: 'settings', label: 'Settings' },
]

export default function App() {
	const [connection, setConnection] = useState<Connection | null>(null)
	const [openError, setOpenError] = useState('')
	// The sidecar takes 20-30s to unpack and import on a cold start, so an open that gives no
	// sign of life is indistinguishable from a dead button.
	const [opening, setOpening] = useState(false)
	const [state, setState] = useState<WorkspaceState | null>(null)
	const [health, setHealth] = useState<Health | null>(null)
	const [tab, setTab] = useState<Tab>('chat')
	const [jobs, setJobs] = useState<Job[]>([])
	const [ask, setAsk] = useState<AskFrame | null>(null)
	const [frame, setFrame] = useState<ImageFrame | null>(null)
	const [chatId, setChatId] = useState('')
	const [online, setOnline] = useState(false)
	const [status, setStatus] = useState('')
	// Session-scoped, exactly like the server's own copy: a new socket is a new session with
	// a fresh HumanInTheLoop, so a dropped connection genuinely resets this to 'ask'.
	const [autonomy, setAutonomy] = useState<Autonomy>('ask')
	const [stopping, setStopping] = useState('')
	const session = useRef<Session | null>(null)
	// A job id is opaque; the view that started a job owns it. This lives here, not in Chat,
	// because Chat unmounts on every tab switch and a running job must not vanish with it.
	const mine = useRef(new Set<string>())

	// Reopen the last workspace on launch; in a browser the handshake may already be in
	// the query string. A failure here has to be visible - silently landing on the picker
	// with no reason given is the worst version of this.
	useEffect(() => {
		if (!inTauri() && !window.location.search.includes('port=')) return
		setOpening(true)
		connect()
			.then(setConnection)
			.catch((e: Error) => setOpenError(e.message))
			.finally(() => setOpening(false))
	}, [])

	const refresh = useCallback(() => {
		if (!connection) return
		api.workspace(connection).then(setState).catch((e: Error) => setStatus(e.message))
		api.health(connection).then(setHealth).catch(() => undefined)
	}, [connection])

	useEffect(() => {
		if (!connection) return
		const live = new Session(connection, {
			onJob: (job) => {
				setJobs((all) => [...all.filter((j) => j.id !== job.id), job])
				if (!job.done) return
				setStopping((id) => (id === job.id ? '' : id))
				// A cancelled run leaves its dialog on screen otherwise, and answering it then
				// goes nowhere: the server no longer holds that ask.
				setAsk((open) => (open && open.job === job.id ? null : open))
			},
			onAsk: setAsk,
			onFrame: setFrame,
			onChat: (id) => setChatId(id),
			onCancelled: (_jobId, ok) => {
				if (ok) return
				setStopping('')
				setStatus('nothing to stop - that job had already finished')
			},
			onStatus: (connected, detail) => {
				setOnline(connected)
				setStatus(detail)
				// The server's default is a constant, so this is correct by construction rather
				// than by asking: never show Auto for a session that is no longer Auto.
				if (!connected) setAutonomy('ask')
			},
			onError: setStatus,
		})
		session.current = live
		refresh()
		return () => live.close()
	}, [connection, refresh])

	// The workspace changes as jobs finish: a plan writes scenarios, a run writes evidence.
	const finished = jobs.filter((j) => j.done).length
	useEffect(refresh, [finished, refresh])

	// The newest unfinished job, not the first in array order: onJob re-appends, so a stale
	// never-finished job used to sit at the head and Stop would send its id.
	const running = useMemo(() => [...jobs].reverse().find((j) => !j.done) ?? null, [jobs])
	const busy = running !== null

	const stop = useCallback((id: string) => {
		setStopping(id)
		session.current?.cancel(id)
	}, [])

	const force = useCallback(() => {
		if (!connection) return
		setStopping('')
		forceStop(connection.workspace)
			.then(() => {
				setStatus('force-stopped - reopening the workspace…')
				setJobs([])
				setConnection(null)
				return connect(connection.workspace)
			})
			.then((fresh) => fresh && setConnection(fresh))
			.catch((e: Error) => setStatus(e.message))
	}, [connection])

	// One global stop, because the run you want to end is not always on the tab you are on -
	// and while an ask modal is up its backdrop covers every button in the window.
	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if (e.key === '.' && (e.metaKey || e.ctrlKey) && running) {
				e.preventDefault()
				stop(running.id)
			}
		}
		window.addEventListener('keydown', onKey)
		return () => window.removeEventListener('keydown', onKey)
	}, [running, stop])
	// Jira drives three surfaces (plan from a ticket, file a bug, sign in). If it is not
	// configured, those controls are absent rather than present-and-broken.
	const hasJira = useMemo(() => (state?.connectors ?? []).some((c) => c.name === 'jira'), [state])

	const open = (workspace?: string, init?: InitOptions) => {
		setOpenError('')
		setOpening(true)
		connect(workspace, init)
			.then(setConnection)
			.catch((e: Error) => setOpenError(e.message))
			.finally(() => setOpening(false))
	}

	// `connection && !state` is the gap after the handshake while /workspace is still loading:
	// still starting, as far as anyone looking at the window is concerned.
	if (!connection || !state) {
		return <WorkspacePicker onOpen={open} error={openError || status} busy={opening || !!connection} />
	}

	const answer = (value: string) => {
		if (ask) session.current?.answer(ask.id, value)
		setAsk(null)
	}

	return (
		<div className="app">
			<nav className="sidebar">
				<div className="brand">
					<strong>{state.app_name}</strong>
					<span className="row-sub">{state.base_url || state.root}</span>
				</div>
				{TABS.map((t) => (
					<button key={t.id} className={`tab ${tab === t.id ? 'tab-on' : ''}`} onClick={() => setTab(t.id)}>
						{t.label}
					</button>
				))}
				<div className="sidebar-foot">
					<label className="autonomy">
						<span className="row-sub">Autonomy</span>
						<select
							value={autonomy}
							disabled={!online}
							title={
								'How the agent answers its own permission requests, for this session only.\n' +
								'It governs actions the agent declares risky. It is not a sandbox.'
							}
							onChange={(e) => {
								const value = e.target.value as Autonomy
								setAutonomy(value)
								session.current?.command('mode', { value })
							}}
						>
							<option value="refuse">Careful — refuse risky actions</option>
							<option value="ask">Ask every time</option>
							<option value="allow">Auto — allow risky actions</option>
						</select>
					</label>
					{autonomy !== 'ask' && (
						<span className="warn">
							{autonomy === 'allow' ? 'granting without asking' : 'refusing without asking'}
						</span>
					)}
					<span className={online ? 'good' : 'bad'}>{online ? '● connected' : '○ ' + (status || 'offline')}</span>
					{health && <span className="row-sub">nkqa {health.nkqa}</span>}
					{health && !health.models_ok && <span className="bad">a model key is missing</span>}
				</div>
			</nav>

			<main className="main">
				{tab === 'chat' && session.current && (
					<Chat
						connection={connection}
						session={session.current}
						jobs={jobs}
						chatId={chatId}
						commands={state.commands}
						mine={mine}
						stopping={stopping}
						onStop={stop}
						onForce={force}
						onChat={setChatId}
					/>
				)}
				{tab === 'scenarios' && session.current && (
					<Scenarios
						connection={connection}
						session={session.current}
						scenarios={state.scenarios}
						busy={busy}
						hasJira={hasJira}
						onChanged={refresh}
					/>
				)}
				{tab === 'appmap' && <AppMap connection={connection} files={state.appmap} flowsOnly={false} />}
				{tab === 'flows' && <AppMap connection={connection} files={state.appmap} flowsOnly={true} />}
				{tab === 'runs' && session.current && (
					<Runs
						connection={connection}
						runs={state.runs}
						session={session.current}
						busy={busy}
						hasJira={hasJira}
					/>
				)}
				{tab === 'settings' && session.current && (
					<Credentials
						session={session.current}
						jobs={jobs}
						health={health}
						state={state}
						connectors={state.connectors}
						busy={busy}
					/>
				)}
			</main>

			<LivePane frame={frame} jobs={jobs} running={running} stopping={stopping} onStop={stop} onForce={force} />

			{ask && (
				<AskModal
					ask={ask}
					onAnswer={answer}
					stopping={stopping}
					onStop={() => stop(ask.job)}
					onForce={force}
				/>
			)}
		</div>
	)
}
