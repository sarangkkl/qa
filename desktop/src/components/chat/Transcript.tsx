/** Claude's conversation, drawn the way Claude Code draws it.
 *
 * One renderer for both sources - the session file read back and the live stream - because
 * they are the same shape. Tool calls are one quiet row each; the two that matter to a human
 * get cards instead: a drafted scenario (review it, approve it, run it, right here) and a
 * run's verdict.
 */

import {
	BookMarked,
	BookOpen,
	Bug,
	Check,
	ChevronRight,
	CircleAlert,
	CircleCheck,
	Clock,
	Compass,
	Eye,
	FileSearch,
	FileSpreadsheet,
	FileText,
	Flag,
	FolderOpen,
	Globe,
	Info,
	Keyboard,
	KeyRound,
	ListChecks,
	LoaderCircle,
	MousePointerClick,
	MoveVertical,
	OctagonX,
	PanelTop,
	Play,
	ShieldAlert,
	ShieldCheck,
	Ticket,
	Undo2,
	Wrench,
	X,
	type LucideIcon,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import * as api from '../../api/client'
import type {
	ClaudeMessage,
	Connection,
	ScenarioDetail,
	ImageBlock,
	ScenarioSummary,
	TextBlock,
	ToolResultBlock,
	ToolUseBlock,
} from '../../api/types'
import { Markdown } from '../Markdown'

export type Item =
	| { kind: 'user'; text: string }
	| { kind: 'text'; text: string }
	| { kind: 'tool'; use: ToolUseBlock; result: ToolResultBlock | undefined }
	| { kind: 'error'; text: string }

/** Bookkeeping Claude Code does on its own behalf, not work anyone asked for. */
const HIDDEN = new Set(['ToolSearch', 'TodoWrite'])

export function toItems(messages: ClaudeMessage[]): Item[] {
	const results = new Map<string, ToolResultBlock>()
	for (const m of messages) {
		const content = m.message?.content
		if (m.type !== 'user' || !Array.isArray(content)) continue
		for (const b of content) if (b.type === 'tool_result') results.set(b.tool_use_id, b)
	}

	const items: Item[] = []
	for (const m of messages) {
		if (m.type === 'result' && m.is_error) {
			items.push({ kind: 'error', text: m.result || 'Claude stopped with an error.' })
			continue
		}
		if (m.type !== 'user' && m.type !== 'assistant') continue
		const content = m.message?.content
		const said = (text: string) => {
			if (!text.trim()) return
			if (m.type === 'user' && !text.trimStart().startsWith('<')) items.push({ kind: 'user', text })
			if (m.type === 'assistant') items.push({ kind: 'text', text })
		}
		if (typeof content === 'string') {
			said(content)
			continue
		}
		for (const b of content ?? []) {
			if (b.type === 'text') said(b.text)
			else if (b.type === 'tool_use' && !HIDDEN.has(b.name)) items.push({ kind: 'tool', use: b, result: results.get(b.id) })
		}
	}
	return items
}

// --- tool rows -----------------------------------------------------------------------

const str = (v: unknown): string => (typeof v === 'string' || typeof v === 'number' ? String(v) : '')

const TOOLS: Record<string, [LucideIcon, (i: Record<string, unknown>) => string]> = {
	workspace_status: [Info, () => 'Checked the workspace'],
	list_workspaces: [FolderOpen, () => 'Listed workspaces'],
	use_workspace: [FolderOpen, (i) => `Switched to ${str(i.path)}`],
	init_workspace: [FolderOpen, (i) => `Set up a workspace for ${str(i.app_name) || 'the app'}`],
	read_appmap: [BookOpen, () => 'Read the app map'],
	update_appmap: [BookMarked, (i) => `Updated the app map${str(i.message) ? ` — ${str(i.message)}` : ''}`],
	list_scenarios: [ListChecks, () => 'Listed scenarios'],
	read_scenario: [FileText, (i) => `Read ${str(i.id)}`],
	write_scenario: [FileText, (i) => `Draft “${str((i.draft as Record<string, unknown> | undefined)?.title)}”`],
	approve_scenario: [ShieldCheck, (i) => `Asked you to approve ${str(i.id)}`],
	start_run: [Play, (i) => `Started a run of ${str(i.scenario_id)}`],
	start_explore: [Compass, (i) => `Started exploring${str(i.url) ? ` ${str(i.url)}` : ''}`],
	browser_state: [Eye, (i) => (i.screenshot ? 'Looked at the page' : 'Read the page')],
	navigate: [Globe, (i) => `Opened ${str(i.url)}`],
	click: [MousePointerClick, (i) => `Clicked [${str(i.index)}]`],
	type_text: [Keyboard, (i) => `Typed into [${str(i.index)}]`],
	scroll: [MoveVertical, () => 'Scrolled'],
	send_keys: [Keyboard, (i) => `Pressed ${str(i.keys)}`],
	go_back: [Undo2, () => 'Went back'],
	list_tabs: [PanelTop, () => 'Listed tabs'],
	switch_tab: [PanelTop, () => 'Switched tab'],
	close_tab: [PanelTop, () => 'Closed a tab'],
	wait: [Clock, (i) => `Waited ${str(i.seconds)}s`],
	check: [
		CircleCheck,
		(i) => `Checked step ${str(i.step)}: ${str(i.kind).replace('_', ' ')}${str(i.value) ? ` “${str(i.value)}”` : ''}`,
	],
	request_permission: [ShieldAlert, (i) => `Asked permission — ${str(i.description) || str(i.key)}`],
	ask_credential: [KeyRound, (i) => `Asked you for “${str(i.name)}”`],
	finish_explore: [Flag, () => 'Finished exploring'],
	abort_run: [OctagonX, () => 'Stopped the run'],
	list_runs: [ListChecks, () => 'Listed runs'],
	read_run: [FileText, (i) => `Read run ${str(i.name)}`],
	read_ticket: [Ticket, (i) => `Read ticket ${str(i.key)}`],
	file_bug: [Bug, (i) => `Drafted a bug from ${str(i.run)}`],
	Read: [FileSearch, (i) => `Read ${str(i.file_path).split('/').pop() ?? ''}`],
	Glob: [FileSearch, (i) => `Looked for ${str(i.pattern)}`],
	Grep: [FileSearch, (i) => `Searched for “${str(i.pattern)}”`],
}

const NKQA = 'mcp__nkqa__'

function describe(use: ToolUseBlock): [LucideIcon, string] {
	const name = use.name.startsWith(NKQA) ? use.name.slice(NKQA.length) : use.name
	const known = TOOLS[name]
	return known ? [known[0], known[1](use.input)] : [Wrench, name]
}

function resultParts(result: ToolResultBlock | undefined): { text: string; images: ImageBlock[] } {
	if (!result) return { text: '', images: [] }
	if (typeof result.content === 'string') return { text: result.content, images: [] }
	const blocks = result.content ?? []
	return {
		text: blocks
			.filter((b): b is TextBlock => b.type === 'text')
			.map((b) => b.text)
			.join('\n'),
		images: blocks.filter((b): b is ImageBlock => b.type === 'image'),
	}
}

function Status({ result, live }: { result: ToolResultBlock | undefined; live: boolean }) {
	if (!result) return live ? <LoaderCircle size={14} className="spin" /> : <span className="tool-dim">—</span>
	return result.is_error ? <X size={14} className="bad" /> : <Check size={14} className="good" />
}

export function ToolRow({ use, result, live }: { use: ToolUseBlock; result: ToolResultBlock | undefined; live: boolean }) {
	const [open, setOpen] = useState(false)
	const [Icon, label] = describe(use)
	const { text, images } = resultParts(result)
	return (
		<div className={`tool ${open ? 'tool-open' : ''}`}>
			<button className="tool-head" onClick={() => setOpen(!open)} aria-expanded={open}>
				<ChevronRight size={14} className="tool-caret" />
				<Icon size={14} />
				<span className="tool-label">{label}</span>
				<Status result={result} live={live} />
			</button>
			{open && (
				<div className="tool-body">
					{Object.keys(use.input).length > 0 && <pre>{JSON.stringify(use.input, null, 2)}</pre>}
					{images.map((img, i) => (
						<img key={i} alt="" src={`data:${img.source.media_type};base64,${img.source.data}`} />
					))}
					{text && <pre className={result?.is_error ? 'bad' : ''}>{text.slice(0, 6000)}</pre>}
				</div>
			)}
		</div>
	)
}

// --- scenario card -------------------------------------------------------------------

interface Draft {
	area?: string
	slug?: string
	title?: string
	steps?: { action?: string; expect?: string }[]
}

const slugify = (s: string) =>
	s
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-|-$/g, '')

const STATE_LABEL: Record<string, string> = { ok: 'approved', draft: 'draft', stale: 'stale', deprecated: 'deprecated' }
const WRITE = `${NKQA}write_scenario`
const READ = `${NKQA}read_scenario`

/** The scenario a tool call is about, if it produced or showed one. */
function scenarioOf(item: Item): string {
	if (item.kind !== 'tool' || !item.result || item.result.is_error) return ''
	const { text } = resultParts(item.result)
	if (item.use.name === READ) return str(item.use.input.id)
	if (item.use.name !== WRITE) return ''
	const written = /Wrote draft (\S+) \(/.exec(text)?.[1]
	if (written) return written
	const draft = (item.use.input.draft ?? {}) as Draft
	return /already exists/.test(text) ? `${slugify(draft.area ?? '')}/${slugify(draft.slug ?? '')}` : ''
}

/** A scenario as it is on disk now - the card is never a stale copy of what Claude sent. */
export function ScenarioCard({
	connection,
	id,
	draft,
	pending,
	scenarios,
	busy,
	canRun,
	onApprove,
	onRun,
	onOpen,
	inLibrary,
}: {
	connection: Connection
	id: string
	draft: Draft | undefined
	pending: boolean
	scenarios: ScenarioSummary[]
	busy: boolean
	canRun: boolean
	onApprove: (ids: string) => void
	onRun: (ids: string[]) => void
	onOpen: (id: string) => void
	inLibrary: boolean
}) {
	const [detail, setDetail] = useState<ScenarioDetail | null>(null)
	const current = scenarios.find((s) => s.id === id)

	// Refetched when the workspace list changes: approving, editing or running shows up here.
	useEffect(() => {
		if (!id) return
		let live = true
		api
			.scenario(connection, id)
			.then((d) => live && setDetail(d))
			.catch(() => live && setDetail(null))
		return () => {
			live = false
		}
	}, [connection, id, current?.state, current?.last_run])

	const state = current?.state ?? detail?.state ?? ''
	const steps = detail?.steps ?? draft?.steps?.map((s) => ({ action: s.action ?? '', expect: s.expect ?? '' })) ?? []

	return (
		<div className="card">
			<div className="card-head">
				<FileText size={16} />
				<div className="card-title">
					<strong>{current?.title ?? detail?.title ?? draft?.title ?? id}</strong>
					<span className="row-sub">{id || 'writing…'}</span>
				</div>
				{current?.last_verdict && (
					<span className={`verdict verdict-${current.last_verdict.toLowerCase()}`}>{current.last_verdict}</span>
				)}
				{state && <span className={`chip chip-${state}`}>{STATE_LABEL[state] ?? state}</span>}
				{pending && <LoaderCircle size={14} className="spin" />}
			</div>

			{(detail?.preconditions.length ?? 0) > 0 && <p className="card-note">Before: {detail!.preconditions.join(' · ')}</p>}
			<ol className="card-steps">
				{steps.map((s, i) => (
					<li key={i}>
						{s.action}
						{s.expect && <span className="card-expect"> → {s.expect}</span>}
					</li>
				))}
			</ol>

			{id && !pending && (
				<div className="card-actions">
					<button
						className={state === 'ok' ? '' : 'primary'}
						disabled={busy || state === 'ok'}
						title={busy ? 'Wait for the current job to finish' : 'Read it in full, then confirm'}
						onClick={() => onApprove(id)}
					>
						<ShieldCheck size={14} /> {state === 'ok' ? 'Approved' : 'Review & approve'}
					</button>
					<button
						className={state === 'ok' ? 'primary' : ''}
						disabled={busy || state !== 'ok' || !canRun}
						title={state === 'ok' ? 'Claude runs it in a real browser' : 'Only an approved scenario can run'}
						onClick={() => onRun([id])}
					>
						<Play size={14} /> Run
					</button>
					{inLibrary && (
						<button onClick={() => onOpen(id)}>
							<FolderOpen size={14} /> Open in library
						</button>
					)}
				</div>
			)}
		</div>
	)
}

/** Under a plan: act on all of it at once. Approving is still one human decision, in the modal. */
function PlanBar({
	ids,
	scenarios,
	busy,
	canRun,
	onApprove,
	onRun,
}: {
	ids: string[]
	scenarios: ScenarioSummary[]
	busy: boolean
	canRun: boolean
	onApprove: (ids: string) => void
	onRun: (ids: string[]) => void
}) {
	const state = (id: string) => scenarios.find((s) => s.id === id)?.state ?? 'draft'
	const unapproved = ids.filter((id) => state(id) !== 'ok' && state(id) !== 'deprecated')
	const approved = ids.filter((id) => state(id) === 'ok')
	return (
		<div className="plan-bar">
			<ListChecks size={16} />
			<span className="plan-bar-label">
				{ids.length} scenarios · {approved.length} approved
			</span>
			<button
				className={unapproved.length ? 'primary' : ''}
				disabled={busy || !unapproved.length}
				onClick={() => onApprove(unapproved.join(' '))}
			>
				<ShieldCheck size={14} /> Approve all{unapproved.length ? ` (${unapproved.length})` : ''}…
			</button>
			<button disabled={busy || !approved.length || !canRun} onClick={() => onRun(approved)}>
				<Play size={14} /> Run approved{approved.length ? ` (${approved.length})` : ''}
			</button>
		</div>
	)
}

// --- verdict card --------------------------------------------------------------------

const VERDICT_ICON = { pass: Check, fail: X, blocked: CircleAlert } as const

/** Where a passing run stands with the Library. */
export type SaveState =
	| { kind: 'idle' }
	| { kind: 'saving' }
	| { kind: 'saved' }
	| { kind: 'other' } // in the Library already, recorded from an earlier run
	| { kind: 'failed'; reason: string }

export function VerdictCard({
	use,
	result,
	live,
	busy,
	save,
	onSave,
	onRerun,
	onOpenRun,
	onOpenLibrary,
}: {
	use: ToolUseBlock
	result: ToolResultBlock | undefined
	live: boolean
	busy: boolean
	save: (run: string, scenarioId: string) => SaveState
	onSave: (run: string) => void
	onRerun: (scenarioId: string) => void
	onOpenRun: (run: string) => void
	onOpenLibrary: (scenarioId: string) => void
}) {
	const steps = (use.input.steps ?? []) as { step?: number; verdict?: 'pass' | 'fail' | 'blocked'; note?: string }[]
	const summary = str(use.input.summary)
	const { text } = resultParts(result)
	const head = /(\S+): (PASS|FAIL|BLOCKED)/.exec(text)
	const verdict = head?.[2]?.toLowerCase() ?? ''
	const scenarioId = head?.[1] ?? ''
	const run = /runs\/([^/\s]+)\/results\.md/.exec(text)?.[1] ?? ''
	const state = run && verdict === 'pass' ? save(run, scenarioId) : null
	// Only a replay that did not come out clean is worth retrying as-is; every other refusal is
	// about the run itself (no checks, edited since), and only a fresh run can fix that.
	const retryable = state?.kind === 'failed' && state.reason.includes('did not replay')

	return (
		<div className={`card card-verdict ${verdict ? `card-${verdict}` : ''}`}>
			<div className="card-head">
				<Flag size={16} />
				<div className="card-title">
					<strong>{head ? `${head[1]} — ${head[2]}` : 'Run finished'}</strong>
					{summary && <span className="row-sub">{summary}</span>}
				</div>
				{!result && live && <LoaderCircle size={14} className="spin" />}
			</div>
			<ul className="card-verdicts">
				{steps.map((s, i) => {
					const Icon = VERDICT_ICON[s.verdict ?? 'blocked'] ?? CircleAlert
					return (
						<li key={i} className={`verdict-${s.verdict ?? 'blocked'}`}>
							<Icon size={14} />
							<span>
								Step {s.step} · {s.verdict}
								{s.note && <span className="card-expect"> — {s.note}</span>}
							</span>
						</li>
					)
				})}
			</ul>
			{result?.is_error && <p className="card-note bad">{text}</p>}
			{state?.kind === 'failed' && <p className="card-note bad">{state.reason}</p>}
			{head && (
				<div className="card-actions">
					{state?.kind === 'saved' ? (
						<button onClick={() => onOpenLibrary(scenarioId)}>
							<span className="card-saved">
								<Check size={14} /> In library
							</span>
						</button>
					) : state?.kind === 'failed' && !retryable ? (
						<button className="primary" disabled={busy} onClick={() => onRerun(scenarioId)}>
							<Play size={14} /> Run again
						</button>
					) : (
						state && (
							<button
								className="primary"
								disabled={busy || state.kind === 'saving'}
								title="Replays it once without a model; kept only if that passes too"
								onClick={() => onSave(run)}
							>
								{state.kind === 'saving' ? <LoaderCircle size={14} className="spin" /> : <BookMarked size={14} />}
								{state.kind === 'saving'
									? 'Replaying once…'
									: state.kind === 'other'
										? 'Replace library copy'
										: state.kind === 'failed'
											? 'Try saving again'
											: 'Save to library'}
							</button>
						)
					)}
					{run && (
						<button onClick={() => onOpenRun(run)}>
							<Eye size={14} /> Evidence
						</button>
					)}
				</div>
			)}
		</div>
	)
}


/** What the human said - with an attached sheet shown as a file, not as the note Claude reads. */
function UserMessage({ text }: { text: string }) {
	const attached = /^\[Attached: ([^\s\]]+)(.*)\]\n*/.exec(text)
	if (!attached) return <div className="msg-user">{text}</div>
	const name = attached[1]?.split('/').pop() ?? ''
	const rows = [...(attached[2] ?? '').matchAll(/(\d+) rows/g)].reduce((n, m) => n + Number(m[1]), 0)
	const rest = text.slice(attached[0].length).trim()
	return (
		<div className="msg-user">
			<div className="attach-chip attach-sent">
				<FileSpreadsheet size={14} />
				<span className="attach-name">{name}</span>
				{rows > 0 && <span className="row-sub">{rows} rows</span>}
			</div>
			{rest}
		</div>
	)
}

// --- the transcript ------------------------------------------------------------------

export function Transcript({
	connection,
	items,
	live,
	scenarios,
	busy,
	canRun,
	onApprove,
	onRun,
	onOpen,
	save,
	onSave,
	onOpenRun,
	library,
}: {
	connection: Connection
	items: Item[]
	live: boolean
	scenarios: ScenarioSummary[]
	busy: boolean
	canRun: boolean
	onApprove: (ids: string) => void
	onRun: (ids: string[]) => void
	onOpen: (id: string) => void
	save: (run: string, scenarioId: string) => SaveState
	onSave: (run: string) => void
	onOpenRun: (run: string) => void
	library: Set<string>
}) {
	const out: ReactNode[] = []
	// Scenarios each reply produced, for the bar under it. A reply ends where the human speaks.
	let segment: string[] = []
	const flush = (key: string) => {
		const ids = [...new Set(segment)]
		if (ids.length > 1)
			out.push(
				<PlanBar
					key={key}
					ids={ids}
					scenarios={scenarios}
					busy={busy}
					canRun={canRun}
					onApprove={onApprove}
					onRun={onRun}
				/>,
			)
		segment = []
	}

	items.forEach((item, i) => {
		if (item.kind === 'user') {
			flush(`bar-${i}`)
			out.push(<UserMessage key={i} text={item.text} />)
			return
		}
		if (item.kind === 'text') {
			out.push(
				<div key={i} className="msg-assistant">
					<Markdown source={item.text} />
				</div>,
			)
			return
		}
		if (item.kind === 'error') {
			out.push(
				<div key={i} className="msg-error">
					<CircleAlert size={14} /> {item.text}
				</div>,
			)
			return
		}
		const name = item.use.name
		const id = scenarioOf(item)
		// A refused draft (the batch limit, a bad area) is a tool row with its reason, not an empty card.
		if ((name === WRITE && !item.result?.is_error) || (name === READ && id)) {
			if (id) segment.push(id)
			out.push(
				<ScenarioCard
					key={i}
					connection={connection}
					id={id}
					draft={name === WRITE ? ((item.use.input.draft ?? {}) as Draft) : undefined}
					pending={!item.result && live}
					scenarios={scenarios}
					busy={busy}
					canRun={canRun}
					onApprove={onApprove}
					onRun={onRun}
					onOpen={onOpen}
					inLibrary={library.has(id)}
				/>,
			)
			return
		}
		if (name === `${NKQA}finish_run`) {
			out.push(
				<VerdictCard
					key={i}
					use={item.use}
					result={item.result}
					live={live}
					busy={busy}
					save={save}
					onSave={onSave}
					onRerun={(id) => onRun([id])}
					onOpenRun={onOpenRun}
					onOpenLibrary={onOpen}
				/>,
			)
			return
		}
		out.push(<ToolRow key={i} use={item.use} result={item.result} live={live} />)
	})
	flush('bar-end')
	return <>{out}</>
}
