import { listen } from '@tauri-apps/api/event'
import {
	Library as LibraryIcon,
	Map as MapIcon,
	MessageSquare,
	Monitor,
	Moon,
	PanelRight,
	PlayCircle,
	Settings,
	Sun,
	Workflow,
	type LucideIcon,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from './api/client'
import {
	connect,
	forceStop,
	inTauri,
	newWindow,
	queryConnection,
	windowIntent,
	type InitOptions,
} from './api/connection'
import { Session, type Job } from './api/socket'
import type { AskFrame, Autonomy, Connection, Health, ImageFrame, LibraryTest, WorkspaceState } from './api/types'
import { AskModal } from './components/AskModal'
import { THEMES, useTheme, type Theme } from './theme'
import { AppMap } from './views/AppMap'
import { Chat, type Turn } from './views/Chat'
import { ClaudeGate } from './views/ClaudeGate'
import { Credentials } from './views/Credentials'
import { Library } from './views/Library'
import { LivePane } from './views/LivePane'
import { Runs } from './views/Runs'
import { WorkspacePicker } from './views/WorkspacePicker'

type Tab = 'chat' | 'library' | 'appmap' | 'flows' | 'runs' | 'settings'
const TABS: { id: Tab; label: string; icon: LucideIcon }[] = [
	{ id: 'chat', label: 'Chat', icon: MessageSquare },
	{ id: 'library', label: 'Library', icon: LibraryIcon },
	{ id: 'appmap', label: 'App map', icon: MapIcon },
	{ id: 'flows', label: 'Flows', icon: Workflow },
	{ id: 'runs', label: 'Runs', icon: PlayCircle },
]
const THEME_ICON: Record<Theme, LucideIcon> = { system: Monitor, light: Sun, dark: Moon }
const SIDE_KEY = 'nkqa.side'

function remembered(key: string, fallback: boolean): boolean {
	try {
		const value = localStorage.getItem(key)
		return value === null ? fallback : value === '1'
	} catch {
		return fallback
	}
}

function remember(key: string, value: boolean): void {
	try {
		localStorage.setItem(key, value ? '1' : '0')
	} catch {
		// unavailable storage only costs the preference, not the layout
	}
}

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
	// Bumped when Claude names a chat, so the list and the header pick the title up.
	const [titleTick, setTitleTick] = useState(0)
	const [online, setOnline] = useState(false)
	const [status, setStatus] = useState('')
	// Session-scoped, exactly like the server's own copy: a new socket is a new session with
	// a fresh HumanInTheLoop, so a dropped connection genuinely resets this to 'ask'.
	const [autonomy, setAutonomy] = useState<Autonomy>('ask')
	const [stopping, setStopping] = useState('')
	// A window the File menu opened to choose a folder should not make you click Open again.
	const [autoPick, setAutoPick] = useState(0)
	const [sideOpen, setSideOpen] = useState(() => remembered(SIDE_KEY, true))
	const [liveOpen, setLiveOpen] = useState(false)
	const [library, setLibrary] = useState<LibraryTest[]>([])
	// What the Library or Runs view should show when a chat card says "Open" or "Evidence".
	const [focus, setFocus] = useState('')
	const [runFocus, setRunFocus] = useState('')
	const [theme, setTheme] = useTheme()
	const session = useRef<Session | null>(null)
	// Each chat turn's chat and text, by job id. Lives here, not in Chat, because Chat unmounts
	// on every tab switch and a turn in flight must not vanish with it.
	const turns = useRef(new Map<string, Turn>())
	// run name -> its library-save job, so a card still knows it is saving after a tab switch.
	const saves = useRef(new Map<string, string>())
	// The live pane opened itself for a run, so it may close itself after; one you opened stays.
	const autoLive = useRef(false)

	const open = (workspace: string, init?: InitOptions) => {
		setOpenError('')
		setOpening(true)
		connect(workspace, init)
			.then(setConnection)
			.catch((e: Error) => setOpenError(e.message))
			.finally(() => setOpening(false))
	}

	// Launch always lands on the picker: a workspace opens because someone chose it, never
	// because it was open last time - reopening it silently spawns a 30s sidecar and a real
	// browser for a project nobody asked about. A window the File menu opened carries its
	// intent in its own URL; a browser carries the whole handshake there.
	useEffect(() => {
		const preset = queryConnection()
		if (preset) {
			setConnection(preset)
			return
		}
		const intent = windowIntent()
		if (intent && 'open' in intent) open(intent.open)
		else if (intent) setAutoPick(1)
	}, [])

	const refresh = useCallback(() => {
		if (!connection) return
		api.workspace(connection).then(setState).catch((e: Error) => setStatus(e.message))
		api.health(connection).then(setHealth).catch(() => undefined)
		api
			.library(connection)
			.then((r) => setLibrary(r.tests))
			.catch(() => setLibrary([]))
	}, [connection])

	const recheck = useCallback(async () => {
		if (!connection) return
		await api.health(connection, true).then(setHealth).catch(() => undefined)
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
				// Without this the last JPEG of a finished run stays on the stage forever,
				// looking live while the header correctly says idle.
				setFrame(null)
				// A turn that failed may have failed on sign-in or plan: look again, and the gate
				// takes over the chat if that is what happened.
				if (job.name === 'say' && job.code !== 0 && !job.cancelled) {
					void api.health(connection, true).then(setHealth).catch(() => undefined)
				}
			},
			onAsk: setAsk,
			onFrame: setFrame,
			onChat: (id, title) => {
				if (!title) {
					// A new chat: the turn that opened it was sent with no chat id.
					for (const turn of turns.current.values()) if (!turn.chat) turn.chat = id
					setChatId(id)
				}
				setTitleTick((n) => n + 1)
			},
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
	const ready = health?.claude?.ready ?? true

	useEffect(() => {
		if (frame && !liveOpen) {
			autoLive.current = true
			setLiveOpen(true)
		}
	}, [frame, liveOpen])

	useEffect(() => {
		if (!running && autoLive.current) {
			autoLive.current = false
			setLiveOpen(false)
		}
	}, [running])

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

	const say = useCallback(
		(text: string) => {
			const s = session.current
			if (!s) return
			setTab('chat')
			const id = s.say(text, chatId || undefined)
			turns.current.set(id, { chat: chatId, text })
		},
		[chatId],
	)

	const approve = useCallback((id: string) => session.current?.command('approve', { id }), [])

	const openScenario = useCallback((id: string) => {
		setFocus(id)
		setTab('library')
	}, [])

	const openRun = useCallback((run: string) => {
		setRunFocus(run)
		setTab('runs')
	}, [])

	const saveToLibrary = useCallback((run: string) => {
		const id = session.current?.command('library-save', { run })
		if (id) saves.current.set(run, id)
	}, [])

	const replay = useCallback((target: string) => session.current?.command('library-replay', { target }), [])

	const toggleSide = useCallback(() => {
		setSideOpen((open) => {
			remember(SIDE_KEY, !open)
			return !open
		})
	}, [])

	const toggleLive = useCallback(() => {
		autoLive.current = false
		setLiveOpen((open) => !open)
	}, [])

	// ⌘. stops whatever runs, from any tab - the run you want to end is not always on the tab
	// you are on, and an ask modal's backdrop covers every button in the window.
	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return
			const key = e.key.toLowerCase()
			if (key === '.' && running) stop(running.id)
			else if (key === 'b') toggleSide()
			else if (key === 'j') toggleLive()
			else if (key === 'n') {
				setTab('chat')
				setChatId('')
			} else return
			e.preventDefault()
		}
		window.addEventListener('keydown', onKey)
		return () => window.removeEventListener('keydown', onKey)
	}, [running, stop, toggleSide, toggleLive])

	// The menu is app-wide, so each window decides for itself what a File verb means: a window
	// still on the picker opens in place, a window that already has a workspace hands the job
	// to a new one. Two workspaces in one window is exactly what this design refuses to build.
	useEffect(() => {
		if (!inTauri()) return
		const subs = Promise.all([
			listen<string>('menu:open-recent', (e) => {
				if (connection) void newWindow(e.payload)
				else open(e.payload)
			}),
			// A new window in pick mode rather than a dialog here: a folder that turns out not
			// to be a workspace needs the setup form, and that has nowhere to render in a
			// window already showing one.
			listen('menu:open-workspace', () => {
				if (connection) void newWindow(undefined, true)
				else setAutoPick((n) => n + 1)
			}),
		])
		return () => void subs.then((offs) => offs.forEach((off) => off()))
	}, [connection])

	// Jira drives two surfaces (file a bug, sign in). If it is not configured, those controls
	// are absent rather than present-and-broken.
	const hasJira = useMemo(() => (state?.connectors ?? []).some((c) => c.name === 'jira'), [state])

	// `connection && !state` is the gap after the handshake while /workspace is still loading:
	// still starting, as far as anyone looking at the window is concerned.
	if (!connection || !state) {
		return (
			<WorkspacePicker
				onOpen={open}
				autoPick={autoPick}
				error={openError || status}
				busy={opening || !!connection}
			/>
		)
	}

	const answer = (value: string) => {
		if (ask) session.current?.answer(ask.id, value)
		setAsk(null)
	}

	// Clicking the tab you are on folds its list away, as in VS Code.
	const pick = (id: Tab) => {
		if (id === tab) toggleSide()
		else {
			setTab(id)
			if (!sideOpen) toggleSide()
		}
	}

	const ThemeIcon = THEME_ICON[theme]
	const nextTheme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length] ?? 'system'

	return (
		<div className={`app ${sideOpen ? '' : 'side-hidden'} ${liveOpen ? 'live-shown' : ''}`}>
			<nav className="activity">
				<div className="activity-brand" title={`${state.app_name}\n${state.base_url || state.root}`}>
					{(state.app_name || 'QA').slice(0, 2).toUpperCase()}
				</div>
				{TABS.map(({ id, label, icon: Icon }) => (
					<button
						key={id}
						className={`activity-btn ${tab === id ? 'activity-on' : ''}`}
						title={label}
						aria-label={label}
						onClick={() => pick(id)}
					>
						<Icon size={22} strokeWidth={1.6} />
					</button>
				))}
				<button
					className={`activity-btn activity-foot ${tab === 'settings' ? 'activity-on' : ''}`}
					title="Settings"
					aria-label="Settings"
					onClick={() => setTab('settings')}
				>
					<Settings size={22} strokeWidth={1.6} />
				</button>
			</nav>

			<main className="main">
				{tab === 'chat' && health && !ready && (
					<ClaudeGate connection={connection} claude={health.claude} onRecheck={recheck} />
				)}
				{tab === 'chat' && ready && (
					<Chat
						connection={connection}
						state={state}
						jobs={jobs}
						chatId={chatId}
						turns={turns}
						titleTick={titleTick}
						busy={busy}
						running={running}
						canRun={ready}
						onChat={setChatId}
						onSay={say}
						onStop={stop}
						onApprove={approve}
						onOpenScenario={openScenario}
						library={library}
						saves={saves}
						onSave={saveToLibrary}
						onOpenRun={openRun}
					/>
				)}
				{tab === 'library' && (
					<Library connection={connection} tests={library} busy={busy} focus={focus} onReplay={replay} />
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
						focus={runFocus}
					/>
				)}
				{tab === 'settings' && session.current && (
					<Credentials session={session.current} jobs={jobs} connectors={state.connectors} busy={busy} />
				)}
			</main>

			{liveOpen && (
				<LivePane
					connection={connection}
					frame={frame}
					jobs={jobs}
					running={running}
					stopping={stopping}
					onStop={stop}
					onForce={force}
					onClose={toggleLive}
				/>
			)}

			<footer className="statusbar">
				<span className={online ? 'good' : 'bad'}>{online ? '● connected' : `○ ${status || 'offline'}`}</span>
				<span className="status-clip" title={state.root}>
					{state.app_name || 'workspace'}
				</span>
				{running && <span className="status-run">● {running.name === 'say' ? 'Claude is working' : running.name}</span>}
				<span className="status-grow" />
				{health?.claude?.path && (
					<span title={health.claude.path}>
						Claude Code {health.claude.version}
						{health.claude.plan ? ` · ${health.claude.plan}` : ''}
					</span>
				)}
				<label
					className="status-autonomy"
					title={
						'How risky actions are answered, for this session only.\n' +
						'It governs actions the agent declares risky. It is not a sandbox.'
					}
				>
					<select
						value={autonomy}
						disabled={!online}
						onChange={(e) => {
							const value = e.target.value as Autonomy
							setAutonomy(value)
							session.current?.command('mode', { value })
						}}
					>
						<option value="refuse">Careful — refuse risky actions</option>
						<option value="ask">Ask before risky actions</option>
						<option value="allow">Auto — allow risky actions</option>
					</select>
				</label>
				<button className="status-btn" title={`Theme: ${theme} (click for ${nextTheme})`} onClick={() => setTheme(nextTheme)}>
					<ThemeIcon size={14} />
				</button>
				<button className={`status-btn ${liveOpen ? 'status-on' : ''}`} title="Live browser (⌘J)" onClick={toggleLive}>
					<PanelRight size={14} />
				</button>
			</footer>

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
