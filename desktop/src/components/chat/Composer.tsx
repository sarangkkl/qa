/** Enter sends, Shift+Enter is a new line; it grows with the message, then scrolls.
 *
 * A test case sheet can ride along: 📎 (or a drop on the chat) uploads it, and the message that
 * goes out starts with the [Attached: …] line the server wrote - that line is what tells Claude
 * where the readable CSV is and what is in it.
 */

import { ArrowUp, FileSpreadsheet, LoaderCircle, Paperclip, Square, X } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import * as api from '../../api/client'
import type { Connection } from '../../api/types'

type Attachment =
	| { name: string; state: 'uploading' }
	| { name: string; state: 'ready'; imported: api.Imported }
	| { name: string; state: 'error'; error: string }

const ACCEPT = '.xlsx,.csv,.tsv'

export function Composer({
	connection,
	incoming,
	onTaken,
	onSend,
	onStop,
	running,
	disabled,
	hint,
}: {
	connection: Connection
	/** A file dropped on the chat, for this composer to upload. */
	incoming: File | null
	onTaken: () => void
	onSend: (text: string) => void
	onStop: () => void
	running: boolean
	disabled: boolean
	hint: string
}) {
	const [text, setText] = useState('')
	const [attachment, setAttachment] = useState<Attachment | null>(null)
	const box = useRef<HTMLTextAreaElement>(null)
	const picker = useRef<HTMLInputElement>(null)

	// Height follows content; CSS max-height caps it and overflow takes over from there.
	useLayoutEffect(() => {
		const el = box.current
		if (!el) return
		el.style.height = 'auto'
		el.style.height = `${el.scrollHeight}px`
	}, [text])

	const attach = (file: File) => {
		setAttachment({ name: file.name, state: 'uploading' })
		api
			.uploadImport(connection, file)
			.then((imported) => setAttachment({ name: file.name, state: 'ready', imported }))
			.catch((e: Error) => setAttachment({ name: file.name, state: 'error', error: e.message }))
		box.current?.focus()
	}

	useEffect(() => {
		if (!incoming) return
		attach(incoming)
		onTaken()
		// attach is recreated each render; the file is what this effect is about.
	}, [incoming])

	const ready = attachment?.state === 'ready' ? attachment.imported : null
	const canSend = !disabled && attachment?.state !== 'uploading' && (!!text.trim() || !!ready)

	const send = () => {
		if (!canSend) return
		const message = text.trim()
		onSend(ready ? `${ready.note}\n\n${message || 'Import these test cases.'}` : message)
		setText('')
		setAttachment(null)
	}

	const rows = ready ? ready.sheets.reduce((n, s) => n + s.rows, 0) : 0

	return (
		<form
			className="composer"
			onSubmit={(e) => {
				e.preventDefault()
				send()
			}}
		>
			{attachment && (
				<div className={`attach-chip ${attachment.state === 'error' ? 'attach-error' : ''}`}>
					{attachment.state === 'uploading' ? <LoaderCircle size={14} className="spin" /> : <FileSpreadsheet size={14} />}
					<span className="attach-name">{attachment.name}</span>
					<span className="row-sub">
						{attachment.state === 'uploading' && 'uploading…'}
						{attachment.state === 'error' && attachment.error}
						{ready &&
							`${ready.sheets.length} sheet${ready.sheets.length === 1 ? '' : 's'} · ${rows} row${rows === 1 ? '' : 's'}`}
					</span>
					<button type="button" className="icon-btn" title="Remove" onClick={() => setAttachment(null)}>
						<X size={14} />
					</button>
				</div>
			)}
			<button
				type="button"
				className="composer-btn icon-btn"
				title="Attach test cases (.xlsx, .csv)"
				disabled={disabled}
				onClick={() => picker.current?.click()}
			>
				<Paperclip size={16} />
			</button>
			<input
				ref={picker}
				type="file"
				accept={ACCEPT}
				hidden
				onChange={(e) => {
					const file = e.target.files?.[0]
					if (file) attach(file)
					e.target.value = '' // picking the same file again still fires
				}}
			/>
			<textarea
				ref={box}
				rows={1}
				value={text}
				autoFocus
				onChange={(e) => setText(e.target.value)}
				onKeyDown={(e) => {
					// isComposing: Enter that confirms an IME candidate (Japanese, Chinese…) is not a send.
					if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
						e.preventDefault()
						send()
					}
				}}
				placeholder={ready ? 'Anything to add? (Enter to import)' : hint}
			/>
			{running ? (
				<button type="button" className="composer-btn danger" title="Stop (⌘.)" onClick={onStop}>
					<Square size={14} fill="currentColor" />
				</button>
			) : (
				<button type="submit" className="composer-btn primary" disabled={!canSend} title="Send (Enter)">
					<ArrowUp size={16} />
				</button>
			)}
		</form>
	)
}

