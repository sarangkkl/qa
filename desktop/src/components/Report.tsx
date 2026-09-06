/** A run report, rendered or as it was written.
 *
 * Raw is not a debugging affordance: a report goes into a ticket or a message, and you cannot
 * paste rendered HTML into either. The <pre> is the literal results.md, selectable as text.
 */

import { useState } from 'react'
import { Markdown, stripEvidence } from './Markdown'

export function Report({ source }: { source: string }) {
	const [raw, setRaw] = useState(false)
	// Rendered drops the report's own Evidence list, whose relative links go nowhere here; the
	// Evidence panel below replaces it. Raw stays byte-for-byte what is on disk, where those
	// links are correct - that is the point of showing the raw file at all.
	return (
		<>
			<div className="detail-tabs">
				<button className={raw ? '' : 'tab-on'} onClick={() => setRaw(false)}>
					Rendered
				</button>
				<button className={raw ? 'tab-on' : ''} onClick={() => setRaw(true)}>
					Raw
				</button>
			</div>
			{raw ? <pre className="report-raw">{source}</pre> : <Markdown source={stripEvidence(source)} />}
		</>
	)
}
