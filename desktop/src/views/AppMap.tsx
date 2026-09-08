/** What the agent knows. Flows get their own section: they are the highest-value content. */

import { useEffect, useState } from 'react'
import * as api from '../api/client'
import type { Connection } from '../api/types'
import { Markdown } from '../components/Markdown'

export function AppMap({ connection, files, flowsOnly }: { connection: Connection; files: string[]; flowsOnly: boolean }) {
	const shown = files.filter((f) => (flowsOnly ? f.startsWith('flows/') : !f.startsWith('flows/')))
	const [selected, setSelected] = useState('')
	const [body, setBody] = useState('')

	const current = selected && shown.includes(selected) ? selected : (shown[0] ?? '')

	useEffect(() => {
		if (!current) return setBody('')
		api
			.text(connection, `/artifacts/appmap/${current}`)
			.then(setBody)
			.catch((e: Error) => setBody(`Could not read appmap/${current}: ${e.message}`))
	}, [connection, current])

	return (
		<div className="split">
			<div className="list">
				<div className="list-head">
					<h2>{flowsOnly ? 'App flows' : 'App map'}</h2>
				</div>
				{shown.length === 0 && (
					<p className="empty">
						{flowsOnly ? 'No flows yet.' : 'Nothing here yet.'} Run <code>qa learn</code> on an annotated doc.
					</p>
				)}
				{shown.map((f) => (
					<button key={f} className={`row ${current === f ? 'row-selected' : ''}`} onClick={() => setSelected(f)}>
						<span className="row-title">{f}</span>
					</button>
				))}
			</div>
			<div className="detail">{body ? <Markdown source={body} /> : <p className="empty">Pick a file.</p>}</div>
		</div>
	)
}
