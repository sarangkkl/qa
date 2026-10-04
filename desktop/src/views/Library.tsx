/** The Library: tests that passed, proven by checks, replayed with no model.
 *
 * A test arrives from the chat - a Claude run that passed and whose recording then replayed once
 * by itself - and leaves when its scenario is edited, or when the QA deletes it. Folders are the
 * QA's: made here, empty or not, and a test moves between them by drag or by "Move to…".
 */

import { BookCheck, Folder, FolderPlus, Play, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState, type DragEvent } from 'react'
import * as api from '../api/client'
import type { Connection, LibraryTest, RunDetail, ScenarioDetail } from '../api/types'
import { Evidence } from '../components/Evidence'
import { Report } from '../components/Report'

const verdictClass = (v: string) => (v ? `verdict verdict-${v.toLowerCase()}` : 'verdict')
const depth = (path: string) => (path ? path.split('/').length : 0)
// By segment, so `a/b` stays under `a` and is not split from it by `a-c`.
const treeKey = (path: string) => path.split('/').join('\u0001')
const under = (path: string, folder: string) => !folder || path === folder || path.startsWith(`${folder}/`)

export function Library({
	connection,
	tests,
	folders,
	busy,
	focus,
	onReplay,
	onChanged,
}: {
	connection: Connection
	tests: LibraryTest[]
	/** Folders on disk, empty ones included. */
	folders: string[]
	busy: boolean
	/** A test the chat asked to open. */
	focus: string
	/** A test id, a folder, or '' for everything. */
	onReplay: (target: string) => void
	/** After a move, delete or new folder: fetch the Library again. */
	onChanged: () => void
}) {
	// `test:<id>` or `folder:<path>`; one selection, so the detail pane has one subject.
	const [selected, setSelected] = useState(focus ? `test:${focus}` : '')
	useEffect(() => {
		if (focus) setSelected(`test:${focus}`)
	}, [focus])
	const [problem, setProblem] = useState('')
	// The parent a new folder is being named under ('' = top level), or null.
	const [creating, setCreating] = useState<string | null>(null)
	const [dragging, setDragging] = useState('')
	const [over, setOver] = useState<string | null>(null)

	const tree = useMemo(() => {
		const all = new Set(folders)
		for (const t of tests) if (t.folder) all.add(t.folder)
		for (const f of [...all]) {
			const parts = f.split('/')
			for (let i = 1; i < parts.length; i++) all.add(parts.slice(0, i).join('/'))
		}
		return [...all].sort((a, b) => (treeKey(a) < treeKey(b) ? -1 : 1))
	}, [tests, folders])

	const test = selected.startsWith('test:') ? tests.find((t) => t.id === selected.slice(5)) : undefined
	const folder = selected.startsWith('folder:') ? selected.slice(7) : null
	const passed = tests.filter((t) => t.last_verdict === 'PASS').length
	const top = tests.filter((t) => !t.folder)

	const act = (work: Promise<unknown>) => {
		setProblem('')
		work.then(onChanged, (e: Error) => setProblem(e.message))
	}
	const move = (id: string, to: string) => {
		if (tests.find((t) => t.id === id)?.folder === to) return
		act(api.moveTest(connection, id, to).then((r) => setSelected(`test:${r.id}`)))
	}
	const create = (parent: string, name: string) => {
		setCreating(null)
		if (name.trim()) act(api.createFolder(connection, parent, name).then((r) => setSelected(`folder:${r.path}`)))
	}

	const dropTarget = (path: string) => ({
		onDragOver: (e: DragEvent) => {
			if (!dragging) return
			e.preventDefault()
			setOver(path)
		},
		onDragLeave: () => setOver((o) => (o === path ? null : o)),
		onDrop: (e: DragEvent) => {
			e.preventDefault()
			setOver(null)
			if (dragging) move(dragging, path)
		},
	})

	const testRow = (t: LibraryTest) => (
		<button
			key={t.id}
			className={`row tree-row ${selected === `test:${t.id}` ? 'row-selected' : ''}`}
			style={{ paddingLeft: 26 + 12 * Math.max(depth(t.folder) - 1, 0) }}
			draggable={!busy}
			onDragStart={(e) => {
				e.dataTransfer.setData('text/plain', t.id)
				e.dataTransfer.effectAllowed = 'move'
				setDragging(t.id)
			}}
			onDragEnd={() => {
				setDragging('')
				setOver(null)
			}}
			onClick={() => setSelected(`test:${t.id}`)}
		>
			<span className="tree-label">
				<span className="row-clip">{t.title}</span>
				{t.last_verdict && <span className={verdictClass(t.last_verdict)}>{t.last_verdict}</span>}
			</span>
		</button>
	)

	const nameInput = (parent: string) => (
		<input
			className="tree-input"
			style={{ marginLeft: 8 + 12 * depth(parent) }}
			autoFocus
			placeholder="Folder name"
			onKeyDown={(e) => {
				if (e.key === 'Enter') create(parent, e.currentTarget.value)
				if (e.key === 'Escape') setCreating(null)
			}}
			onBlur={(e) => create(parent, e.currentTarget.value)}
		/>
	)

	return (
		<div className="split">
			<div className="list">
				<div className="list-head">
					<h2>Library</h2>
					<span>
						<button className="icon-btn" title="New folder" disabled={busy} onClick={() => setCreating('')}>
							<FolderPlus size={16} />
						</button>
						<button className="icon-btn" title="Replay everything, no model" disabled={busy || !tests.length} onClick={() => onReplay('')}>
							<Play size={16} />
						</button>
					</span>
				</div>
				{problem && <p className="error tree-problem">{problem}</p>}
				{tests.length === 0 && tree.length === 0 && (
					<p className="empty">
						Empty. In the chat, run an approved scenario; when it passes, press <strong>Save to library</strong>.
					</p>
				)}
				{creating === '' && nameInput('')}
				{(top.length > 0 || (dragging && tree.length > 0)) && (
					<div className={`tree-line ${over === '' ? 'tree-drop' : ''}`} {...dropTarget('')}>
						<button
							className={`row tree-row ${selected === 'folder:' ? 'row-selected' : ''}`}
							style={{ paddingLeft: 8 }}
							onClick={() => setSelected('folder:')}
						>
							<span className="tree-label">
								<Folder size={14} /> <span className="row-clip">Top level</span>
								<span className="row-sub tree-count">{top.length}</span>
							</span>
						</button>
					</div>
				)}
				{top.map(testRow)}
				{tree.map((path) => {
					const own = tests.filter((t) => t.folder === path)
					const inside = tests.filter((t) => under(t.folder, path)).length
					return (
						<div key={path} className="tree-folder">
							<div
								className={`tree-line ${over === path ? 'tree-drop' : ''} ${selected === `folder:${path}` ? 'row-selected' : ''}`}
								{...dropTarget(path)}
							>
								<button
									className="row tree-row"
									style={{ paddingLeft: 8 + 12 * (depth(path) - 1) }}
									onClick={() => setSelected(`folder:${path}`)}
								>
									<span className="tree-label">
										<Folder size={14} /> <span className="row-clip">{path.split('/').pop()}</span>
										<span className="row-sub tree-count">{inside}</span>
									</span>
								</button>
								<span className="tree-actions">
									<button className="icon-btn" title="New subfolder" disabled={busy} onClick={() => setCreating(path)}>
										<FolderPlus size={14} />
									</button>
									{inside === 0 && (
										<button
											className="icon-btn"
											title="Delete folder"
											disabled={busy}
											onClick={() => {
												if (selected === `folder:${path}`) setSelected('')
												act(api.deleteFolder(connection, path))
											}}
										>
											<Trash2 size={14} />
										</button>
									)}
								</span>
							</div>
							{creating === path && nameInput(path)}
							{own.map(testRow)}
						</div>
					)
				})}
			</div>

			<div className="detail">
				{test && (
					<TestDetail
						key={test.id}
						connection={connection}
						test={test}
						folders={tree}
						busy={busy}
						onReplay={onReplay}
						onMove={(to) => move(test.id, to)}
						onDelete={() => {
							setSelected('')
							act(api.deleteTest(connection, test.id))
						}}
					/>
				)}
				{folder !== null && (
					<FolderDetail
						path={folder}
						tests={tests.filter((t) => under(t.folder, folder))}
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
	folders,
	busy,
	onReplay,
	onMove,
	onDelete,
}: {
	connection: Connection
	test: LibraryTest
	folders: string[]
	busy: boolean
	onReplay: (target: string) => void
	onMove: (folder: string) => void
	onDelete: () => void
}) {
	const [confirming, setConfirming] = useState(false)
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
					<select
						title="Move to…"
						value={test.folder}
						disabled={busy}
						onChange={(e) => onMove(e.target.value)}
					>
						<option value="">Top level</option>
						{folders.map((f) => (
							<option key={f} value={f}>
								{f}
							</option>
						))}
					</select>
					<button className="danger" disabled={busy} title="Delete this test" onClick={() => setConfirming(true)}>
						<Trash2 size={14} />
					</button>
					<button className="primary" disabled={busy} onClick={() => onReplay(test.id)}>
						<Play size={14} /> Replay
					</button>
				</div>
			</div>
			{confirming && (
				<div className="confirm-bar">
					<span>Delete this test and its scenario? Its runs and evidence are kept.</span>
					<button className="danger" disabled={busy} onClick={onDelete}>
						Delete
					</button>
					<button onClick={() => setConfirming(false)}>Cancel</button>
				</div>
			)}
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
