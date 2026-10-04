/** The wire contract, from docs/PROTOCOL.md. Change here means change there. */

export type EventKind = 'log' | 'step' | 'verdict' | 'artifact' | 'progress' | 'frame' | 'done'
export type AskKind = 'text' | 'secret' | 'confirm' | 'choice'
export type ScenarioState = 'ok' | 'draft' | 'stale' | 'deprecated'

export interface Connection {
	port: number
	token: string
	workspace: string
}

// --- HTTP -------------------------------------------------------------------

/** The user's own Claude Code, which does all the thinking. `ready` = signed in and paying. */
export interface ClaudeStatus {
	path: string
	version: string
	logged_in: boolean
	auth_method: string
	plan: string
	ready: boolean
}

export interface Health {
	nkqa: string
	browser_use: string
	workspace: string
	claude: ClaudeStatus
	busy: boolean
}

export interface ScenarioSummary {
	id: string
	title: string
	state: ScenarioState
	ticket: string
	tags: string[]
	approved_by: string
	approved_at: string
	last_verdict: string
	/** Run directory behind `last_verdict`; '' if never run. Its results.md is servable as-is. */
	last_run: string
}

export interface ConnectorInfo {
	name: string
	command: string
	expose_to_executor: boolean
	needs_env: string[]
	project: string
}

export interface CommandParam {
	name: string
	help: string
	type: 'string' | 'integer' | 'boolean'
	flag: boolean
	required: boolean
}

export interface CommandInfo {
	name: string
	help: string
	human_only: boolean
	shell_only: boolean
	/** Runs outside the one-job-at-a-time runner, so it still works during a run. */
	instant: boolean
	params: CommandParam[]
}

/** How the session answers a permission request. Session-scoped: resets on every reconnect. */
export type Autonomy = 'ask' | 'allow' | 'refuse'

export interface ModelChoice {
	id: string
	label: string
	/** Which tier this model is offered for: 'smart' plans, 'fast' executes. */
	tier: string
}

export interface ProviderInfo {
	name: string
	label: string
	models: ModelChoice[]
	/** tier -> model id: what picking this provider alone means. The server decides, not list order. */
	defaults: Record<string, string>
}

export interface WorkspaceState {
	root: string
	app_name: string
	base_url: string
	headless: boolean
	/** role -> alias name or a model id written straight into config.yaml. */
	models: Record<string, string>
	/** The two tiers the roles point at. What the settings page edits. */
	aliases: Record<string, string>
	/** What settings offers, not what it accepts - any id typed in is passed through. */
	providers: ProviderInfo[]
	appmap: string[]
	scenarios: ScenarioSummary[]
	runs: string[]
	connectors: ConnectorInfo[]
	commands: CommandInfo[]
}

export interface ScenarioDetail extends ScenarioSummary {
	body: string
	preconditions: string[]
	steps: { action: string; expect: string }[]
}

export interface StepVerdict {
	step: number
	verdict: 'pass' | 'fail' | 'blocked'
	note: string
}

export interface RunDetail {
	name: string
	steps: number
	result: {
		scenario_id: string
		verdict: string
		approved_hash: string
		result: { steps: StepVerdict[]; summary: string } | null
	} | null
	artifacts: Partial<Record<'report' | 'gif' | 'history', string>>
	videos: string[]
	/** Per-step screenshots, in step order. */
	shots: string[]
	/** The LLM transcript, one file per step, in step order. */
	conversation: string[]
}

/** A Library test: passed, proven by checks, and replayable with no model. */
export interface LibraryTest {
	id: string
	title: string
	/** The id's path without its last part - `auth/login` is in `auth`. '' at the top. */
	folder: string
	steps: number
	saved_at: string
	/** The Claude run its recording came from. */
	recorded_from: string
	last_run: string
	last_verdict: string
}

/** A Claude Code session in this workspace's folder, whoever started it. */
export interface ChatSummary {
	id: string
	title: string
	updated: string
	turns: number
}

export interface ChatDetail {
	id: string
	title: string
	messages: ClaudeMessage[]
}

// --- Claude Code's stream-json, which is also its session-file shape ---------------

export interface TextBlock {
	type: 'text'
	text: string
}

export interface ImageBlock {
	type: 'image'
	source: { type: string; media_type: string; data: string }
}

export interface ToolUseBlock {
	type: 'tool_use'
	id: string
	name: string
	input: Record<string, unknown>
}

export interface ToolResultBlock {
	type: 'tool_result'
	tool_use_id: string
	content: string | (TextBlock | ImageBlock)[]
	is_error?: boolean
}

export type Block = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock | { type: 'thinking' | 'redacted_thinking' }

export interface ClaudeMessage {
	type: 'user' | 'assistant' | 'system' | 'result'
	subtype?: string
	message?: { content: string | Block[] }
	is_error?: boolean
	result?: string
}

// --- WebSocket --------------------------------------------------------------

export interface StartedFrame {
	type: 'started'
	job: string
	name: string
}

export interface EventFrame {
	type: 'event'
	job: string
	kind: EventKind
	text: string
	data: Record<string, unknown>
}

export interface AskFrame {
	type: 'ask'
	id: string
	job: string
	kind: AskKind
	prompt: string
	key: string
	options: string[]
	body: string
	interrupt: boolean
}

export interface ResultFrame {
	type: 'result'
	job: string
	code: number
	cancelled?: boolean
	error?: string
}

export interface CancelledFrame {
	type: 'cancelled'
	job: string
	ok: boolean
}

export interface ClaudeFrame {
	type: 'claude'
	job: string
	msg: ClaudeMessage
}

export interface ChatFrame {
	type: 'chat'
	id: string
	title: string
}

export interface ImageFrame {
	type: 'frame'
	job: string
	image: string
	format: string
	width: number | null
	height: number | null
}

export interface ErrorFrame {
	type: 'error'
	message: string
}

export type ServerFrame =
	| StartedFrame
	| EventFrame
	| AskFrame
	| ResultFrame
	| CancelledFrame
	| ChatFrame
	| ClaudeFrame
	| ImageFrame
	| ErrorFrame
