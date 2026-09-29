/** Enter sends, Shift+Enter is a new line; it grows with the message, then scrolls. */

import { ArrowUp, Square } from 'lucide-react'
import { useLayoutEffect, useRef, useState } from 'react'

export function Composer({
	onSend,
	onStop,
	running,
	disabled,
	hint,
}: {
	onSend: (text: string) => void
	onStop: () => void
	running: boolean
	disabled: boolean
	hint: string
}) {
	const [text, setText] = useState('')
	const box = useRef<HTMLTextAreaElement>(null)

	// Height follows content; CSS max-height caps it and overflow takes over from there.
	useLayoutEffect(() => {
		const el = box.current
		if (!el) return
		el.style.height = 'auto'
		el.style.height = `${el.scrollHeight}px`
	}, [text])

	const send = () => {
		const message = text.trim()
		if (!message || disabled) return
		onSend(message)
		setText('')
	}

	return (
		<form
			className="composer"
			onSubmit={(e) => {
				e.preventDefault()
				send()
			}}
		>
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
				placeholder={hint}
			/>
			{running ? (
				<button type="button" className="composer-btn danger" title="Stop (⌘.)" onClick={onStop}>
					<Square size={14} fill="currentColor" />
				</button>
			) : (
				<button type="submit" className="composer-btn primary" disabled={disabled || !text.trim()} title="Send (Enter)">
					<ArrowUp size={16} />
				</button>
			)}
		</form>
	)
}
