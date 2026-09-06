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

export interface RoleHealth {
	model: string
	provider: string
	missing_keys: string[]
}

export interface Health {
	nkqa: string
	browser_use: string
	workspace: string
	models_ok: boolean
	roles: Record<string, RoleHealth>
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
}

export interface ChatSummary {
	id: string
	title: string
	created: string
	updated: string
	turns: number
}

export interface ChatTurn {
	role: 'user' | 'assistant' | 'event'
	text: string
	command: string
	args: Record<string, string>
	exit: number | null
	at: string
}

export interface ChatDetail {
	id: string
	title: string
	created: string
	updated: string
	turns: ChatTurn[]
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
	| ImageFrame
	| ErrorFrame
