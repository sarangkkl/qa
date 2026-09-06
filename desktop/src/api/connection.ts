/** Where the sidecar is, and how to prove we are allowed to talk to it.
 *
 * Inside Tauri the Rust side spawns nkqa-server, reads its one-line handshake, and hands
 * us {port, token}. In a plain browser - which is how this UI gets developed and tested -
 * the same values come from the query string:
 *
 *   npm run dev  →  http://127.0.0.1:1420/?port=51734&token=abc…
 *
 * Same app either way; only where the handshake comes from differs.
 */

import { invoke } from '@tauri-apps/api/core'
import type { Connection } from './types'

/** Detection only - the calls themselves go through the official API. */
export const inTauri = (): boolean => '__TAURI_INTERNALS__' in window

/** A command returning Result<_, String> rejects with that bare string, not an Error - so
 * `e.message` is undefined and every `.catch(e => show(e.message))` renders nothing. That is
 * how a failed open used to look exactly like no open at all. Normalise at the boundary. */
async function command<T>(name: string, args?: Record<string, unknown>): Promise<T> {
	try {
		return await invoke<T>(name, args)
	} catch (e) {
		throw e instanceof Error ? e : new Error(typeof e === 'string' ? e : JSON.stringify(e))
	}
}

function fromQuery(): Connection | null {
	const params = new URLSearchParams(window.location.search)
	const port = Number(params.get('port'))
	const token = params.get('token')
	if (!port || !token) return null
	return { port, token, workspace: params.get('workspace') ?? '' }
}

/** A folder the human picked, and whether it is already a workspace. */
export interface PickedFolder {
	path: string
	is_workspace: boolean
}

/** What the setup form collected. Never a secret - it crosses Rust as a command-line argument. */
export interface InitOptions {
	app_name: string
	base_url: string
}

/** Ask Tauri to start (or re-report) the sidecar for a workspace.
 *
 * With `init` the sidecar creates the workspace first, so the folder is expected not to be
 * one yet. Creation lives in `nkqa/workspace.py`, never in the shell.
 */
export async function connect(workspace?: string, init?: InitOptions): Promise<Connection> {
	if (inTauri()) {
		const args: Record<string, unknown> = {}
		if (workspace !== undefined) args.workspace = workspace
		if (init !== undefined) args.init = init
		return await command<Connection>('sidecar_connect', args)
	}
	const fallback = fromQuery()
	if (!fallback) {
		throw new Error(
			'No sidecar. In a browser, start one with `nkqa-server --workspace <path>` and open ' +
				'this page with ?port=<port>&token=<token> from its handshake line.',
		)
	}
	return fallback
}

/** Kill the sidecar for a workspace. Reconnecting afterwards respawns it. */
export async function forceStop(workspace: string): Promise<void> {
	if (!inTauri()) throw new Error('Force stop needs the desktop app; in a browser, stop the sidecar yourself.')
	await command<null>('sidecar_stop', { workspace })
}

export async function recentWorkspaces(): Promise<string[]> {
	if (!inTauri()) return []
	return await command<string[]>('recent_workspaces')
}

export async function pickWorkspace(): Promise<PickedFolder | null> {
	if (!inTauri()) return null
	return await command<PickedFolder | null>('pick_workspace')
}

export const httpBase = (c: Connection): string => `http://127.0.0.1:${c.port}`
export const wsUrl = (c: Connection): string => `ws://127.0.0.1:${c.port}/session?token=${encodeURIComponent(c.token)}`

/** Artifact URLs come back from the server as paths; they need the host and the token. */
export function artifactUrl(c: Connection, path: string): string {
	const joiner = path.includes('?') ? '&' : '?'
	return `${httpBase(c)}${path}${joiner}token=${encodeURIComponent(c.token)}`
}
