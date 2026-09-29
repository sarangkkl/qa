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
	Clock,
	Compass,
	Eye,
	FileSearch,
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
import { useState } from 'react'
import type {
	ClaudeMessage,
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
	preconditions?: string[]
	steps?: { action?: string; expect?: string }[]
}

const slugify = (s: string) =>
	s
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-|-$/g, '')

const STATE_LABEL: Record<string, string> = { ok: 'approved', draft: 'draft', stale: 'stale', deprecated: 'deprecated' }

export function ScenarioCard({
	use,
	result,
	live,
	scenarios,
	busy,
	canRun,
	onApprove,
	onRun,
	onOpen,
}: {
	use: ToolUseBlock
	result: ToolResultBlock | undefined
	live: boolean
	scenarios: ScenarioSummary[]
	busy: boolean
	canRun: boolean
	onApprove: (id: string) => void
	onRun: (id: string) => void
	onOpen: (id: string) => void
}) {
	const draft = (use.input.draft ?? {}) as Draft
	const { text } = resultParts(result)
	const id = /Wrote draft (\S+) \(/.exec(text)?.[1] ?? `${slugify(draft.area ?? '')}/${slugify(draft.slug ?? '')}`
	const written = !!result && !result.is_error && text.startsWith('Wrote draft')
	const current = scenarios.find((s) => s.id === id)
	const state = current?.state ?? (written ? 'draft' : '')

	return (
		<div className="card">
			<div className="card-head">
				<FileText size={16} />
				<div className="card-title">
					<strong>{current?.title ?? draft.title ?? id}</strong>
					<span className="row-sub">{id}</span>
				</div>
				{state && <span className={`chip chip-${state}`}>{STATE_LABEL[state] ?? state}</span>}
				{!result && live && <LoaderCircle size={14} className="spin" />}
			</div>

			{(draft.preconditions?.length ?? 0) > 0 && (
				<p className="card-note">Before: {draft.preconditions!.join(' · ')}</p>
			)}
			<ol className="card-steps">
				{(draft.steps ?? []).map((s, i) => (
					<li key={i}>
						{s.action}
						{s.expect && <span className="card-expect"> → {s.expect}</span>}
					</li>
				))}
			</ol>

			{result && !written && <p className="card-note bad">{text}</p>}
			{written && (
				<div className="card-actions">
					<button
						className="primary"
						disabled={busy || state === 'ok'}
						title={busy ? 'Wait for the current job to finish' : 'Read it in full, then confirm'}
						onClick={() => onApprove(id)}
					>
						<ShieldCheck size={14} /> {state === 'ok' ? 'Approved' : 'Review & approve'}
					</button>
					<button
						disabled={busy || state !== 'ok' || !canRun}
						title={state === 'ok' ? 'Claude runs it in a real browser' : 'Only an approved scenario can run'}
						onClick={() => onRun(id)}
					>
						<Play size={14} /> Run
					</button>
					<button onClick={() => onOpen(id)}>
						<FolderOpen size={14} /> Open
					</button>
				</div>
			)}
		</div>
	)
}

// --- verdict card --------------------------------------------------------------------

const VERDICT_ICON = { pass: Check, fail: X, blocked: CircleAlert } as const

export function VerdictCard({
	use,
	result,
	live,
	onOpen,
}: {
	use: ToolUseBlock
	result: ToolResultBlock | undefined
	live: boolean
	onOpen: (scenarioId: string) => void
}) {
	const steps = (use.input.steps ?? []) as { step?: number; verdict?: 'pass' | 'fail' | 'blocked'; note?: string }[]
	const summary = str(use.input.summary)
	const { text } = resultParts(result)
	const head = /(\S+): (PASS|FAIL|BLOCKED)/.exec(text)
	const verdict = head?.[2]?.toLowerCase() ?? ''

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
			{head && (
				<div className="card-actions">
					<button onClick={() => onOpen(head[1] ?? '')}>
						<Eye size={14} /> Evidence
					</button>
				</div>
			)}
		</div>
	)
}

// --- the transcript ------------------------------------------------------------------

export function Transcript({
	items,
	live,
	scenarios,
	busy,
	canRun,
	onApprove,
	onRun,
	onOpen,
}: {
	items: Item[]
	live: boolean
	scenarios: ScenarioSummary[]
	busy: boolean
	canRun: boolean
	onApprove: (id: string) => void
	onRun: (id: string) => void
	onOpen: (id: string) => void
}) {
	return (
		<>
			{items.map((item, i) => {
				if (item.kind === 'user') return <div key={i} className="msg-user">{item.text}</div>
				if (item.kind === 'text')
					return (
						<div key={i} className="msg-assistant">
							<Markdown source={item.text} />
						</div>
					)
				if (item.kind === 'error')
					return (
						<div key={i} className="msg-error">
							<CircleAlert size={14} /> {item.text}
						</div>
					)
				const name = item.use.name
				if (name === `${NKQA}write_scenario`)
					return (
						<ScenarioCard
							key={i}
							use={item.use}
							result={item.result}
							live={live}
							scenarios={scenarios}
							busy={busy}
							canRun={canRun}
							onApprove={onApprove}
							onRun={onRun}
							onOpen={onOpen}
						/>
					)
				if (name === `${NKQA}finish_run`)
					return <VerdictCard key={i} use={item.use} result={item.result} live={live} onOpen={onOpen} />
				return <ToolRow key={i} use={item.use} result={item.result} live={live} />
			})}
		</>
	)
}
