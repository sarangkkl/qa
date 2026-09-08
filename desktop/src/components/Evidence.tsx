/** The evidence behind a verdict, as things you can actually open.
 *
 * The report ends with a `## Evidence` list, but those links are relative paths meant for
 * someone reading results.md on disk — in the app they resolve against the app origin and go
 * nowhere, and two of the four (`videos/`, `conversation/`) are directories, which the artifact
 * route will not serve at all. So the report's own list is dropped when rendering and this is
 * shown in its place: the same evidence, as a player, a filmstrip and links that carry the token.
 *
 * Absent evidence says so. "No video was recorded for this run" is a fact worth showing; an
 * empty space just looks like the page failed to load.
 */

import { useState } from 'react'
import { artifactUrl } from '../api/connection'
import type { Connection, RunDetail } from '../api/types'

export function Evidence({ connection, detail }: { connection: Connection; detail: RunDetail }) {
	const [shot, setShot] = useState(0)
	const url = (path: string) => artifactUrl(connection, path)
	const shots = detail.shots ?? []
	const conversation = detail.conversation ?? []
	const chosen = shots[Math.min(shot, shots.length - 1)]

	return (
		<section className="evidence">
			<h3>Evidence</h3>

			{detail.videos.length > 0 ? (
				detail.videos.map((v) => <video key={v} className="evidence-video" controls src={url(v)} />)
			) : (
				<p className="row-sub">
					No video was recorded for this run. Newer runs record one; older evidence keeps the gif and the
					step screenshots below.
				</p>
			)}

			{detail.artifacts.gif && (
				<img className="evidence-gif" src={url(detail.artifacts.gif)} alt="every step, as a gif" />
			)}

			{shots.length > 0 && (
				<>
					<p className="row-sub">
						Step {Math.min(shot, shots.length - 1) + 1} of {shots.length}
					</p>
					{chosen && <img className="evidence-gif" src={url(chosen)} alt={`step ${shot + 1}`} />}
					<div className="filmstrip">
						{shots.map((s, i) => (
							<button
								key={s}
								className={`thumb ${i === Math.min(shot, shots.length - 1) ? 'thumb-on' : ''}`}
								title={`step ${i + 1}`}
								onClick={() => setShot(i)}
							>
								<img alt="" src={url(s)} />
							</button>
						))}
					</div>
				</>
			)}

			<ul className="evidence-links">
				{detail.artifacts.history && (
					<li>
						<a href={url(detail.artifacts.history)} target="_blank" rel="noreferrer">
							Recording (history.json)
						</a>{' '}
						<span className="row-sub">— what `qa replay` re-runs, without an LLM</span>
					</li>
				)}
				{conversation.length > 0 && (
					<li>
						LLM transcript:{' '}
						{conversation.map((c, i) => (
							<span key={c}>
								{i > 0 && ' · '}
								<a href={url(c)} target="_blank" rel="noreferrer">
									{i + 1}
								</a>
							</span>
						))}
					</li>
				)}
			</ul>
		</section>
	)
}
