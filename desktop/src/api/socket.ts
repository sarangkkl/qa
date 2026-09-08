/** The session socket: run commands, talk in plain English, answer prompts, watch frames.
 *
 * One connection is one session, so a credential typed here is reused for this window and
 * dies with it. Reconnects are automatic and quiet; a job in flight does not survive one,
 * which is honest - the sidecar cancelled it when the socket dropped.
 */

import { wsUrl } from './connection'
import type { AskFrame, Connection, EventFrame, ImageFrame, ServerFrame } from './types'

export interface Job {
	id: string
	name: string
	events: EventFrame[]
	code: number | null
	cancelled: boolean
	error: string
	done: boolean
}

export interface Handlers {
	onJob?: (job: Job) => void
	onAsk?: (ask: AskFrame) => void
	onFrame?: (frame: ImageFrame) => void
	onChat?: (id: string, title: string) => void
	onCancelled?: (jobId: string, ok: boolean) => void
	onStatus?: (connected: boolean, detail: string) => void
	onError?: (message: string) => void
}

const RETRY_MS = 1500

export class Session {
	private socket: WebSocket | null = null
	private jobs = new Map<string, Job>()
	private queue: string[] = []
	private counter = 0
	private closed = false
	private retry: ReturnType<typeof setTimeout> | null = null

	constructor(
		private connection: Connection,
		private handlers: Handlers,
	) {
		this.open()
	}

	private open(): void {
		if (this.closed) return
		const socket = new WebSocket(wsUrl(this.connection))
		this.socket = socket

		socket.onopen = () => {
			this.handlers.onStatus?.(true, '')
			for (const frame of this.queue.splice(0)) socket.send(frame)
		}
		socket.onmessage = (message: MessageEvent<string>) => this.receive(message.data)
		socket.onclose = () => {
			this.handlers.onStatus?.(false, 'reconnecting…')
			this.failInFlight('the connection dropped')
			if (!this.closed) this.retry = setTimeout(() => this.open(), RETRY_MS)
		}
		socket.onerror = () => this.handlers.onStatus?.(false, 'cannot reach the sidecar')
	}

	private failInFlight(reason: string): void {
		for (const job of this.jobs.values()) {
			if (job.done) continue
			job.done = true
			job.error = reason
			job.code = 1
			this.handlers.onJob?.({ ...job })
		}
	}

	private receive(raw: string): void {
		let frame: ServerFrame
		try {
			frame = JSON.parse(raw) as ServerFrame
		} catch {
			return
		}
		switch (frame.type) {
			case 'started': {
				const job: Job = {
					id: frame.job,
					name: frame.name,
					events: [],
					code: null,
					cancelled: false,
					error: '',
					done: false,
				}
				this.jobs.set(frame.job, job)
				this.handlers.onJob?.({ ...job })
				break
			}
			case 'event': {
				const job = this.jobs.get(frame.job)
				if (!job) break
				job.events.push(frame)
				this.handlers.onJob?.({ ...job, events: [...job.events] })
				break
			}
			case 'frame':
				this.handlers.onFrame?.(frame)
				break
			case 'ask':
				this.handlers.onAsk?.(frame)
				break
			case 'result': {
				const job = this.jobs.get(frame.job) ?? {
					id: frame.job,
					name: '',
					events: [],
					code: null,
					cancelled: false,
					error: '',
					done: false,
				}
				job.code = frame.code
				job.cancelled = frame.cancelled ?? false
				job.error = frame.error ?? ''
				job.done = true
				this.jobs.set(frame.job, job)
				this.handlers.onJob?.({ ...job, events: [...job.events] })
				break
			}
			case 'cancelled':
				// The server's `ok` is the difference between "stopped" and "there was nothing
				// to stop". Dropping it made a failed Stop look exactly like a successful one.
				this.handlers.onCancelled?.(frame.job, frame.ok)
				break
			case 'chat':
				this.handlers.onChat?.(frame.id, frame.title)
				break
			case 'error':
				this.handlers.onError?.(frame.message)
				break
		}
	}

	private send(payload: Record<string, unknown>): void {
		const text = JSON.stringify(payload)
		if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(text)
		else this.queue.push(text)
	}

	private nextId(prefix: string): string {
		this.counter += 1
		return `${prefix}${this.counter}`
	}

	command(name: string, args: Record<string, unknown> = {}): string {
		const id = this.nextId('c')
		this.send({ type: 'command', id, name, args })
		return id
	}

	/** A slash line, parsed server-side by the same parser the terminal uses.
	 *
	 * Pass `chatId` (even as '') when it was typed in the chat box: the server then records
	 * it in the transcript, so reading a conversation back does not skip what was run.
	 */
	commandLine(line: string, chatId?: string): string {
		const id = this.nextId('c')
		this.send({ type: 'command', id, line, ...(chatId !== undefined ? { chat: chatId } : {}) })
		return id
	}

	say(text: string, chatId?: string): string {
		const id = this.nextId('s')
		this.send({ type: 'say', id, text, ...(chatId ? { chat: chatId } : {}) })
		return id
	}

	answer(askId: string, value: string): void {
		this.send({ type: 'answer', id: askId, value })
	}

	cancel(jobId: string): void {
		this.send({ type: 'cancel', id: jobId })
	}

	close(): void {
		this.closed = true
		if (this.retry) clearTimeout(this.retry)
		this.socket?.close()
	}
}
