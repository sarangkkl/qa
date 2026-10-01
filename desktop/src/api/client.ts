/** HTTP reads. The socket carries everything that happens; this is state you can fetch. */

import { httpBase } from './connection'
import type {
	ChatDetail,
	ChatSummary,
	Connection,
	Health,
	LibraryTest,
	RunDetail,
	ScenarioDetail,
	WorkspaceState,
} from './types'

async function get<T>(c: Connection, path: string): Promise<T> {
	const response = await fetch(`${httpBase(c)}${path}`, {
		headers: { Authorization: `Bearer ${c.token}` },
	})
	if (!response.ok) {
		const detail = await response.text().catch(() => '')
		throw new Error(`${path} → ${response.status} ${detail.slice(0, 200)}`)
	}
	return (await response.json()) as T
}

export const health = (c: Connection, recheck = false) => get<Health>(c, `/health${recheck ? '?recheck=true' : ''}`)
export const workspace = (c: Connection) => get<WorkspaceState>(c, '/workspace')
export const scenario = (c: Connection, id: string) => get<ScenarioDetail>(c, `/scenarios/${id}`)
export const run = (c: Connection, name: string) => get<RunDetail>(c, `/runs/${encodeURIComponent(name)}`)
export const library = (c: Connection) => get<{ tests: LibraryTest[] }>(c, '/library')
export const chats = (c: Connection) => get<{ chats: ChatSummary[] }>(c, '/chats')
export const chat = (c: Connection, id: string) => get<ChatDetail>(c, `/chats/${encodeURIComponent(id)}`)

/** appmap and report files are served as artifacts; fetch their text for rendering. */
export async function text(c: Connection, path: string): Promise<string> {
	const response = await fetch(`${httpBase(c)}${path}`, {
		headers: { Authorization: `Bearer ${c.token}` },
	})
	if (!response.ok) throw new Error(`${path} → ${response.status}`)
	return await response.text()
}

async function post(c: Connection, path: string): Promise<void> {
	const response = await fetch(`${httpBase(c)}${path}`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${c.token}` },
	})
	if (!response.ok) throw new Error(`${path} → ${response.status}`)
}

/** Opens the browser sign-in. It finishes when the human does; re-check health afterwards. */
export const claudeLogin = (c: Connection) => post(c, '/claude/login')
/** The sidecar opens it: the webview has no way to hand a URL to the system browser. */
export const openPage = (c: Connection, which: 'install' | 'upgrade') => post(c, `/open/${which}`)
