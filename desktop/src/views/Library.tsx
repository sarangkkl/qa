/** The Library: tests that passed, proven by checks, replayed with no model.
 *
 * Nothing is added here by hand. A test arrives from the chat - a Claude run that passed and
 * whose recording then replayed once by itself - and leaves when its scenario is edited. A
 * replay that fails is the QA's to judge: file the bug, or re-record it from the chat.
 */

import { BookCheck, Folder, Play } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import * as api from '../api/client'
import type { Connection, LibraryTest, RunDetail, ScenarioDetail } from '../api/types'
import { Evidence } from '../components/Evidence'
import { Report } from '../components/Report'

const verdictClass = (v: string) => (v ? `verdict verdict-${v.toLowerCase()}` : 'verdict')

export function Library({
	connection,
	tests,
	busy,
	focus,
	onReplay,
}: {
	connection: Connection
	tests: LibraryTest[]
	busy: boolean
	/** A test the chat asked to open. */
	focus: string
	/** A test id, a folder, or '' for everything. */
	onReplay: (target: string) => void
}) {
	// `test:<id>` or `folder:<path>`; one selection, so the detail pane has one subject.
	const [selected, setSelected] = useState(focus ? `test:${focus}` : '')
	useEffect(() => {
		if (focus) setSelected(`test:${focus}`)
	}, [focus])

	const folders = useMemo(() => {
		const by = new Map<string, LibraryTest[]>()
		for (const t of tests) by.set(t.folder, [...(by.get(t.folder) ?? []), t])
		return [...by.entries()].sort(([a], [b]) => a.localeCompare(b))
	}, [tests])

	const test = selected.startsWith('test:') ? tests.find((t) => t.id === selected.slice(5)) : undefined
	const folder = selected.startsWith('folder:') ? selected.slice(7) : null
	const passed = tests.filter((t) => t.last_verdict === 'PASS').length

	return (
		<div className="split">
			<div className="list">
				<div className="list-head">
					<h2>Library</h2>
					<button className="icon-btn" title="Replay everything, no model" disabled={busy || !tests.length} onClick={() => onReplay('')}>
						<Play size={16} />
					</button>
				</div>
				{tests.length === 0 && (
					<p className="empty">
						Empty. In the chat, run an approved scenario; when it passes, press <strong>Save to library</strong>.
					</p>
				)}
				{folders.map(([path, items]) => (
					<div key={path} className="tree-folder">
						<button
							className={`row tree-row ${selected === `folder:${path}` ? 'row-selected' : ''}`}
							style={{ paddingLeft: 8 + 12 * (path ? path.split('/').length - 1 : 0) }}
							onClick={() => setSelected(`folder:${path}`)}
						>
							<span className="tree-label">
								<Folder size={14} /> <span className="row-clip">{path || 'Top level'}</span>
								<span className="row-sub tree-count">{items.length}</span>
							</span>
						</button>
						{items.map((t) => (
							<button
								key={t.id}
								className={`row tree-row ${selected === `test:${t.id}` ? 'row-selected' : ''}`}
								style={{ paddingLeft: 26 + 12 * (path ? path.split('/').length - 1 : 0) }}
								onClick={() => setSelected(`test:${t.id}`)}
							>
								<span className="tree-label">
									<span className="row-clip">{t.title}</span>
									{t.last_verdict && <span className={verdictClass(t.last_verdict)}>{t.last_verdict}</span>}
								</span>
							</button>
						))}
					</div>
				))}
			</div>

			<div className="detail">
				{test && <TestDetail connection={connection} test={test} busy={busy} onReplay={onReplay} />}
				{folder !== null && (
					<FolderDetail
						path={folder}
						tests={tests.filter((t) => t.folder === folder || t.folder.startsWith(`${folder}/`) || folder === '')}
						busy={busy}
						onOpen={(id) => setSelected(`test:${id}`)}
						onReplay={onReplay}
					/>
				)}
				{!test && folder === null && (
					<div className="library-summary">
						<BookCheck size={28} />
						<h2>
							{tests.length} test{tests.length === 1 ? '' : 's'} · {passed} passing on the last run
						</h2>
						<p className="row-sub">Replays repeat each recorded step and re-check every expectation, with no model.</p>
						<button className="primary" disabled={busy || !tests.length} onClick={() => onReplay('')}>
							<Play size={14} /> Replay all
						</button>
					</div>
				)}
			</div>
		</div>
	)
}

function FolderDetail({
	path,
	tests,
	busy,
	onOpen,
	onReplay,
}: {
	path: string
	tests: LibraryTest[]
	busy: boolean
	onOpen: (id: string) => void
	onReplay: (target: string) => void
}) {
	return (
		<>
			<div className="detail-head">
				<div>
					<h2>{path || 'Top level'}</h2>
					<p className="row-sub">{tests.length} tests</p>
				</div>
				<div className="detail-actions">
					<button className="primary" disabled={busy || !tests.length} onClick={() => onReplay(path)}>
						<Play size={14} /> Replay folder
					</button>
				</div>
			</div>
			<table className="grid">
				<thead>
					<tr>
						<th>Test</th>
						<th>Last result</th>
						<th>Last run</th>
					</tr>
				</thead>
				<tbody>
					{tests.map((t) => (
						<tr key={t.id} className="grid-link" onClick={() => onOpen(t.id)}>
							<td>
								{t.title}
								<div className="row-sub">{t.id}</div>
							</td>
							<td>{t.last_verdict ? <span className={verdictClass(t.last_verdict)}>{t.last_verdict}</span> : '—'}</td>
							<td className="row-sub">{t.last_run || '—'}</td>
						</tr>
					))}
				</tbody>
			</table>
		</>
	)
}

function TestDetail({
	connection,
	test,
	busy,
	onReplay,
}: {
	connection: Connection
	test: LibraryTest
	busy: boolean
	onReplay: (target: string) => void
}) {
	const [scenario, setScenario] = useState<ScenarioDetail | null>(null)
	const [report, setReport] = useState('')
	const [run, setRun] = useState<RunDetail | null>(null)

	useEffect(() => {
		let live = true
		api
			.scenario(connection, test.id)
			.then((d) => live && setScenario(d))
			.catch(() => live && setScenario(null))
		return () => {
			live = false
		}
	}, [connection, test.id])

	// The latest run - a replay or the Claude run it came from. Evidence next to the verdict.
	useEffect(() => {
		setReport('')
		setRun(null)
		if (!test.last_run) return
		let live = true
		api
			.text(connection, `/artifacts/runs/${encodeURIComponent(test.last_run)}/results.md`)
			.then((t) => live && setReport(t))
			.catch(() => undefined)
		api
			.run(connection, test.last_run)
			.then((d) => live && setRun(d))
			.catch(() => undefined)
		return () => {
			live = false
		}
	}, [connection, test.last_run])

	return (
		<>
			<div className="detail-head">
				<div>
					<h2>{test.title}</h2>
					<p className="row-sub">
						{test.id} · {test.steps} recorded steps · saved from {test.recorded_from}
					</p>
				</div>
				<div className="detail-actions">
					<button className="primary" disabled={busy} onClick={() => onReplay(test.id)}>
						<Play size={14} /> Replay
					</button>
				</div>
			</div>
			{scenario && (
				<ol className="card-steps library-steps">
					{scenario.steps.map((s, i) => (
						<li key={i}>
							{s.action}
							{s.expect && <span className="card-expect"> → {s.expect}</span>}
						</li>
					))}
				</ol>
			)}
			<h3>Last run {test.last_verdict && <span className={verdictClass(test.last_verdict)}>{test.last_verdict}</span>}</h3>
			{report ? <Report source={report} /> : <p className="empty">Not replayed yet.</p>}
			{run && <Evidence connection={connection} detail={run} />}
		</>
	)
}
