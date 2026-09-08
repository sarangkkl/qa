/** A deliberately small markdown renderer.
 *
 * The appmap and run reports are markdown we wrote ourselves, so this covers what we
 * actually emit - headings, lists, tables, code, bold, links - and nothing else. Pulling
 * in a full parser plus a sanitizer for that is not a trade worth making.
 */

const escape = (s: string): string =>
	s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function inline(text: string): string {
	return escape(text)
		.replace(/`([^`]+)`/g, '<code>$1</code>')
		.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
		.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
}

function table(rows: string[]): string {
	const cells = (line: string) =>
		line
			.replace(/^\||\|$/g, '')
			.split('|')
			.map((c) => c.trim())
	const [head, , ...body] = rows
	if (!head) return ''
	const th = cells(head).map((c) => `<th>${inline(c)}</th>`).join('')
	const tr = body.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')
	return `<table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`
}

/** Scenario files lead with YAML frontmatter; the UI shows that as chips instead. */
export function stripFrontmatter(source: string): string {
	const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(source)
	return match ? source.slice(match[0].length) : source
}

/** Drop the report's trailing `## Evidence` list.
 *
 * Its links are relative paths written for someone reading results.md on disk. In the app they
 * point at the app origin and go nowhere, and `videos/` and `conversation/` are directories the
 * artifact route refuses outright. The Evidence component renders the real thing in its place;
 * leaving these would mean two Evidence sections, one of them broken.
 */
export function stripEvidence(source: string): string {
	const lines = source.split('\n')
	const at = lines.findIndex((l) => /^#{2,3}\s+Evidence\s*$/.test(l))
	if (at < 0) return source
	// Only the heading and its bullet list go. `qa file-bug` appends a "Filed: PROJ-1" line
	// after this section, and swallowing that would hide that a bug was already raised.
	let end = at + 1
	while (end < lines.length && /^(\s*[-*]\s|\s*$)/.test(lines[end] ?? '')) end += 1
	return [...lines.slice(0, at), ...lines.slice(end)].join('\n').trimEnd()
}

export function toHtml(markdown: string): string {
	const out: string[] = []
	const lines = markdown.split('\n')
	let i = 0
	while (i < lines.length) {
		const line = lines[i] ?? ''
		if (line.startsWith('```')) {
			const block: string[] = []
			i += 1
			while (i < lines.length && !(lines[i] ?? '').startsWith('```')) block.push(lines[i] ?? ''), (i += 1)
			out.push(`<pre><code>${escape(block.join('\n'))}</code></pre>`)
			i += 1
			continue
		}
		if (line.trimStart().startsWith('|')) {
			const rows: string[] = []
			while (i < lines.length && (lines[i] ?? '').trimStart().startsWith('|')) rows.push(lines[i] ?? ''), (i += 1)
			out.push(table(rows))
			continue
		}
		if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
			out.push('<hr />')
			i += 1
			continue
		}
		const heading = /^(#{1,4})\s+(.*)$/.exec(line)
		if (heading) {
			const level = heading[1]?.length ?? 1
			out.push(`<h${level}>${inline(heading[2] ?? '')}</h${level}>`)
			i += 1
			continue
		}
		if (/^\s*[-*]\s+/.test(line)) {
			const items: string[] = []
			while (i < lines.length && /^\s*[-*]\s+/.test(lines[i] ?? '')) {
				items.push(`<li>${inline((lines[i] ?? '').replace(/^\s*[-*]\s+/, ''))}</li>`)
				i += 1
			}
			out.push(`<ul>${items.join('')}</ul>`)
			continue
		}
		if (/^\s*\d+\.\s+/.test(line)) {
			const items: string[] = []
			while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i] ?? '')) {
				items.push(`<li>${inline((lines[i] ?? '').replace(/^\s*\d+\.\s+/, ''))}</li>`)
				i += 1
			}
			out.push(`<ol>${items.join('')}</ol>`)
			continue
		}
		if (line.trim()) out.push(`<p>${inline(line)}</p>`)
		i += 1
	}
	return out.join('\n')
}

export function Markdown({ source }: { source: string }) {
	return <div className="markdown" dangerouslySetInnerHTML={{ __html: toHtml(source) }} />
}
