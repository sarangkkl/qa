/** Choosing the provider and models, written straight into config.yaml.
 *
 * It edits the two tier aliases, not the five roles: every role ships pointing at `smart` or
 * `fast`, so this moves all of them while keeping the split that makes planning good and
 * execution cheap. A role someone has pointed straight at a model is left alone and named.
 *
 * The catalogue comes from the server and is a convenience, not a constraint - "Other" takes
 * any id and passes it to the provider verbatim, so a model released after this build is
 * still reachable without a new build.
 */

import { useEffect, useMemo, useState } from 'react'
import type { Job, Session } from '../api/socket'
import type { Health, ProviderInfo } from '../api/types'

const TIERS = [
	{ key: 'smart', label: 'Smart', used: 'planning, revising, ingesting docs' },
	{ key: 'fast', label: 'Fast', used: 'driving the browser, reflecting, chat' },
] as const

const OTHER = '__other__'

/** A stored alias -> [provider, id], for either name form. Mirrors models.split_model. */
function split(name: string): [string, string] {
	if (name.includes(':')) {
		const at = name.indexOf(':')
		return [name.slice(0, at), name.slice(at + 1)]
	}
	const at = name.indexOf('_')
	if (at < 0) return ['', name]
	return [name.slice(0, at), name.slice(at + 1).replace(/_/g, '-')]
}

export function ModelPicker({
	session,
	jobs,
	providers,
	aliases,
	roles,
	health,
	root,
}: {
	session: Session
	jobs: Job[]
	providers: ProviderInfo[]
	aliases: Record<string, string>
	roles: Record<string, string>
	health: Health | null
	root: string
}) {
	const saved = useMemo(() => {
		const [provider] = split(aliases.smart ?? '')
		const known = providers.some((p) => p.name === provider)
		return {
			provider: known ? provider : (providers[0]?.name ?? ''),
			smart: split(aliases.smart ?? '')[1],
			fast: split(aliases.fast ?? '')[1],
		}
	}, [aliases, providers])

	// Local state is the *pending* edit only. Clearing it when the saved value changes is what
	// makes the form show the truth again after an apply, instead of a stale draft that looks
	// like it was saved.
	const [draft, setDraft] = useState<typeof saved | null>(null)
	// Deliberately keyed on the values, not on `saved`'s identity: /workspace is re-fetched
	// every time any job finishes, and clearing on identity would wipe a half-typed model id
	// because something unrelated completed.
	useEffect(() => setDraft(null), [saved.provider, saved.smart, saved.fast])
	const [sent, setSent] = useState('')
	const current = draft ?? saved
	// The server's own reply - the ✅ lines, the role table, any ❌ - rather than a hopeful
	// "saved". A rejected id has to be visible here; nothing else on this page would show it.
	const result = jobs.find((j) => j.id === sent)

	const catalogue = providers.find((p) => p.name === current.provider)?.models ?? []
	const dirty =
		draft !== null &&
		(draft.provider !== saved.provider || draft.smart !== saved.smart || draft.fast !== saved.fast)

	// Switching provider is not a half-move: the previous provider's ids mean nothing to the
	// new one, so both tiers go to that provider's own picks and can be narrowed after.
	const pickProvider = (name: string) => {
		const chosen = providers.find((p) => p.name === name)
		const models = chosen?.models ?? []
		// The server's picks, not the first entry of each tier: deriving it here as well is how
		// this page and `qa set-model --provider` got to disagree about the same question.
		const pick = (tier: string) =>
			chosen?.defaults?.[tier] ?? models.find((m) => m.tier === tier)?.id ?? models[0]?.id ?? ''
		setDraft({ provider: name, smart: pick('smart'), fast: pick('fast') })
	}

	const apply = () =>
		setSent(session.command('set-model', { provider: current.provider, smart: current.smart, fast: current.fast }))

	// health describes the config on disk, so this only speaks about what is actually saved -
	// it never guesses what a pending switch will need.
	const missing = useMemo(() => {
		const keys = new Set<string>()
		for (const role of Object.values(health?.roles ?? {})) role.missing_keys.forEach((k) => keys.add(k))
		return [...keys]
	}, [health])

	const detached = Object.entries(roles).filter(([, value]) => value !== 'smart' && value !== 'fast')

	return (
		<>
			<h3>Models</h3>
			<div className="field-row">
				<label className="field">
					<span>Provider</span>
					<select value={current.provider} onChange={(e) => pickProvider(e.target.value)}>
						{providers.map((p) => (
							<option key={p.name} value={p.name}>
								{p.label}
							</option>
						))}
					</select>
				</label>

				{TIERS.map((tier) => {
					const value = current[tier.key]
					const listed = catalogue.some((m) => m.id === value)
					return (
						<label className="field" key={tier.key} title={`Used for ${tier.used}.`}>
							<span>
								{tier.label} — {tier.used}
							</span>
							<select
								value={listed ? value : OTHER}
								onChange={(e) =>
									setDraft({ ...current, [tier.key]: e.target.value === OTHER ? '' : e.target.value })
								}
							>
								{catalogue.map((m) => (
									<option key={m.id} value={m.id}>
										{m.label}
									</option>
								))}
								<option value={OTHER}>Other…</option>
							</select>
							{!listed && (
								<input
									value={value}
									placeholder="model id, e.g. claude-opus-5"
									onChange={(e) => setDraft({ ...current, [tier.key]: e.target.value })}
								/>
							)}
						</label>
					)
				})}
			</div>

			<div className="detail-actions">
				<button onClick={apply} disabled={!dirty || !current.smart || !current.fast}>
					Apply
				</button>
				{dirty && <span className="row-sub">not saved yet</span>}
				{result && !result.done && <span className="row-sub">saving…</span>}
				{result?.done && result.code !== 0 && <span className="bad">{result.error || 'not saved'}</span>}
			</div>

			{result && result.events.length > 0 && (
				<pre className="console">{result.events.map((e) => e.text).join('\n')}</pre>
			)}

			{detached.length > 0 && (
				<p className="warn">
					{detached.map(([role]) => role).join(', ')} point straight at a model in <code>config.yaml</code> rather
					than at a tier, so they do not follow this. Edit the <code>models:</code> block to bring them back.
				</p>
			)}
			{missing.length > 0 && (
				<p className="warn">
					{missing.join(' and ')} {missing.length > 1 ? 'are' : 'is'} not set. Add{' '}
					{missing.length > 1 ? 'them' : 'it'} to <code>{root}/.env</code>, then reopen the workspace — keys are
					read once, when the workspace opens.
				</p>
			)}
		</>
	)
}
