"""Suites: run the approved scenarios, compare against last time, exit like CI expects.

The suite *is* the approved set. A draft was never approved, so it is not part of the
suite and its absence is reported, not failed on. A STALE scenario is different: it was
approved and then edited, which is a hole in your regression cover - `--strict` fails on
either, and CI should probably use it.

The vault (docs/DESKTOP-PLAN.md §6) is what makes this runnable unattended: without stored
credentials a suite stops at the first login prompt.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from nkqa import scenarios as scenarios_mod
from nkqa.config import Config
from nkqa.hitl import HumanInTheLoop
from nkqa.scenarios import Scenario
from nkqa.stop import StopSignal
from nkqa.ui import Channel, Event
from nkqa.workspace import Workspace

EXCLUDED_REASONS = {
	'draft': 'never approved - not part of the suite',
	'stale': 'edited after approval - re-approve to put it back in the suite',
	'deprecated': 'deprecated',
}
ICONS = {'pass': '✅', 'fail': '❌', 'blocked': '🚧', 'excluded': '⊘'}


@dataclass
class Entry:
	scenario_id: str
	title: str = ''
	state: str = 'ok'
	verdict: str = 'excluded'  # pass | fail | blocked | excluded
	run: str = ''
	seconds: float = 0.0
	reason: str = ''

	@property
	def ran(self) -> bool:
		return self.verdict != 'excluded'


@dataclass
class SuiteResult:
	name: str = ''
	started: str = ''
	tag: str = ''
	entries: list[Entry] = field(default_factory=list[Entry])

	def by_verdict(self, verdict: str) -> list[Entry]:
		return [e for e in self.entries if e.verdict == verdict]

	@property
	def counts(self) -> dict[str, int]:
		return {v: len(self.by_verdict(v)) for v in ('pass', 'fail', 'blocked', 'excluded')}

	def verdicts(self) -> dict[str, str]:
		return {e.scenario_id: e.verdict for e in self.entries}

	def exit_code(self, strict: bool = False) -> int:
		counts = self.counts
		if counts['fail'] or counts['blocked']:
			return 1
		if strict and counts['excluded']:
			return 1
		if not any(e.ran for e in self.entries):
			return 2  # nothing ran at all: that is a usage problem, not a passing suite
		return 0


def select(all_scenarios: list[Scenario], tag: str = '') -> tuple[list[Scenario], list[Scenario]]:
	"""(in the suite, excluded). Tag filtering happens before the approval check."""
	tagged = [s for s in all_scenarios if not tag or tag in s.tags]
	runnable = [s for s in tagged if s.runnable() == 'ok']
	excluded = [s for s in tagged if s.runnable() != 'ok']
	return runnable, excluded


# --- history ----------------------------------------------------------------


def suite_name(runs_dir: Path, at: datetime) -> str:
	"""Unique even for two suites in the same second - otherwise the second would
	overwrite the first's report and the comparison would have nothing to read."""
	base = f'suite--{at:%Y%m%d-%H%M%S}'
	name, n = base, 1
	while (runs_dir / name).exists():
		n += 1
		name = f'{base}-{n}'
	return name


def suite_dirs(runs_dir: Path) -> list[Path]:
	"""Suite run dirs, oldest first."""
	if not runs_dir.is_dir():
		return []
	return sorted((d for d in runs_dir.glob('suite--*') if (d / 'suite.json').is_file()), key=lambda d: d.name)


def load_suite(path: Path) -> SuiteResult | None:
	f = path / 'suite.json' if path.is_dir() else path
	try:
		loaded: Any = json.loads(f.read_text(encoding='utf-8'))
	except (FileNotFoundError, json.JSONDecodeError):
		return None
	raw = cast(dict[str, Any], loaded) if isinstance(loaded, dict) else {}
	raw_entries: list[Any] = raw.get('entries') or []
	entries: list[Entry] = []
	for item in raw_entries:
		data = cast(dict[str, Any], item) if isinstance(item, dict) else {}
		entries.append(
			Entry(
				scenario_id=str(data.get('scenario_id') or ''),
				title=str(data.get('title') or ''),
				state=str(data.get('state') or 'ok'),
				verdict=str(data.get('verdict') or 'excluded'),
				run=str(data.get('run') or ''),
				seconds=float(data.get('seconds') or 0.0),
				reason=str(data.get('reason') or ''),
			)
		)
	return SuiteResult(
		name=str(raw.get('name') or ''),
		started=str(raw.get('started') or ''),
		tag=str(raw.get('tag') or ''),
		entries=entries,
	)


def previous_suite(runs_dir: Path, before: str = '') -> SuiteResult | None:
	"""The newest suite before `before` (a suite dir name), or the newest overall."""
	candidates = [d for d in suite_dirs(runs_dir) if not before or d.name < before]
	return load_suite(candidates[-1]) if candidates else None


# --- comparison -------------------------------------------------------------


@dataclass
class Comparison:
	newly_failing: list[str] = field(default_factory=list[str])
	newly_passing: list[str] = field(default_factory=list[str])
	still_failing: list[str] = field(default_factory=list[str])
	added: list[str] = field(default_factory=list[str])
	removed: list[str] = field(default_factory=list[str])

	@property
	def interesting(self) -> bool:
		return bool(self.newly_failing or self.newly_passing or self.added or self.removed)


def compare(previous: SuiteResult | None, current: SuiteResult) -> Comparison:
	"""What changed since last time. `newly_failing` is the line that matters in CI."""
	if previous is None:
		return Comparison(added=sorted(current.verdicts()))
	before, after = previous.verdicts(), current.verdicts()
	bad = {'fail', 'blocked'}
	delta = Comparison()
	for sid, verdict in sorted(after.items()):
		if sid not in before:
			delta.added.append(sid)
		elif verdict in bad and before[sid] not in bad:
			delta.newly_failing.append(sid)
		elif verdict == 'pass' and before[sid] in bad:
			delta.newly_passing.append(sid)
		elif verdict in bad and before[sid] in bad:
			delta.still_failing.append(sid)
	delta.removed = sorted(set(before) - set(after))
	return delta


# --- reporting --------------------------------------------------------------


def render(result: SuiteResult, delta: Comparison) -> str:
	counts = result.counts
	headline = 'PASS' if not (counts['fail'] or counts['blocked']) else 'FAIL'
	lines = [
		f'# Suite {result.name} — {headline}',
		'',
		f'{counts["pass"]} passed · {counts["fail"]} failed · {counts["blocked"]} blocked · '
		f'{counts["excluded"]} not in the suite' + (f' · tag: {result.tag}' if result.tag else ''),
		'',
		'| Scenario | Verdict | Time | Run |',
		'|----------|---------|------|-----|',
	]
	for e in result.entries:
		when = f'{e.seconds:.0f}s' if e.ran else '—'
		where = f'[{e.run}]({e.run}/results.md)' if e.run else (e.reason or '—')
		lines.append(f'| {e.scenario_id} | {ICONS[e.verdict]} {e.verdict.upper()} | {when} | {where} |')

	if delta.interesting or delta.still_failing:
		lines += ['', '## Since the last suite', '']
		for label, ids in (
			('🔻 newly failing', delta.newly_failing),
			('🔺 newly passing', delta.newly_passing),
			('still failing', delta.still_failing),
			('🆕 new', delta.added),
			('gone', delta.removed),
		):
			if ids:
				lines.append(f'- **{label}:** {", ".join(ids)}')
	return '\n'.join(lines) + '\n'


def write_report(run_dir: Path, result: SuiteResult, delta: Comparison) -> None:
	run_dir.mkdir(parents=True, exist_ok=True)
	(run_dir / 'suite.json').write_text(json.dumps(asdict(result), indent=1), encoding='utf-8')
	(run_dir / 'suite.md').write_text(render(result, delta), encoding='utf-8')


# --- running ----------------------------------------------------------------


async def run_suite(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	ch: Channel,
	tag: str = '',
	strict: bool = False,
	model: str | None = None,
	stop: StopSignal | None = None,
) -> int:
	from nkqa.execution.report import last_verdict, latest_run_dir
	from nkqa.execution.scenario_runner import run_scenario

	runnable, excluded = select(scenarios_mod.load_all(ws.scenarios_dir), tag)
	if not runnable and not excluded:
		await ch.log(f'No scenarios{f" tagged {tag}" if tag else ""}. Draft some:  qa plan "<what to test>"')
		return 2

	started = datetime.now()
	result = SuiteResult(name=suite_name(ws.runs_dir, started), started=started.isoformat(timespec='seconds'), tag=tag)
	await ch.log(
		f'\n🧪 Suite: {len(runnable)} approved scenario(s)'
		+ (f' tagged "{tag}"' if tag else '')
		+ (f', {len(excluded)} not in the suite' if excluded else '')
		+ '\n'
	)

	stop = stop or StopSignal()
	stopped_at: list[Scenario] = []
	for index, scenario in enumerate(runnable):
		# Without this, stopping one scenario just moves on to the next one - which is the
		# opposite of what pressing Stop means.
		if stop.stopped:
			stopped_at = runnable[index:]
			await ch.log(f'\n⏹  Suite stopped - {len(stopped_at)} scenario(s) not run.')
			break
		at = datetime.now()
		await ch.log(f'── {scenario.id} ──')
		await run_scenario(ws, config, hitl, ch, scenario, model, stop)
		run_dir = latest_run_dir(ws.runs_dir, scenario.id)
		result.entries.append(
			Entry(
				scenario_id=scenario.id,
				title=scenario.title,
				state='ok',
				verdict=(last_verdict(ws.runs_dir, scenario.id) or 'blocked').lower(),
				run=run_dir.name if run_dir else '',
				seconds=(datetime.now() - at).total_seconds(),
			)
		)

	for scenario in stopped_at:
		result.entries.append(
			Entry(
				scenario_id=scenario.id,
				title=scenario.title,
				state=scenario.runnable(),
				verdict='excluded',
				reason='not run - the suite was stopped',
			)
		)

	for scenario in excluded:
		state = scenario.runnable()
		result.entries.append(
			Entry(
				scenario_id=scenario.id,
				title=scenario.title,
				state=state,
				verdict='excluded',
				reason=EXCLUDED_REASONS.get(state, state),
			)
		)

	delta = compare(previous_suite(ws.runs_dir), result)
	run_dir = ws.runs_dir / result.name
	write_report(run_dir, result, delta)

	counts = result.counts
	# A stopped suite is not a passing suite: everything left over counts as `excluded`, and
	# passes-plus-excluded would otherwise report 0.
	code = 1 if stopped_at else result.exit_code(strict)
	await ch.emit(
		Event(
			'verdict',
			f'\n{"✅" if code == 0 else "❌"} Suite {result.name}: '
			f'{counts["pass"]} passed, {counts["fail"]} failed, {counts["blocked"]} blocked'
			+ (f', {counts["excluded"]} not in the suite' if counts['excluded'] else ''),
			{'suite': result.name, 'counts': counts, 'exit': code},
		)
	)
	if delta.newly_failing:
		await ch.log(f'🔻 Newly failing since the last suite: {", ".join(delta.newly_failing)}')
	if delta.newly_passing:
		await ch.log(f'🔺 Newly passing: {", ".join(delta.newly_passing)}')
	for entry in result.by_verdict('excluded'):
		await ch.log(f'⊘  {entry.scenario_id}: {entry.reason}')
	await ch.artifact(f'📄 Report: {run_dir / "suite.md"}', suite=result.name, report=str(run_dir / 'suite.md'))
	return code


async def compare_suites(ws: Workspace, ch: Channel, first: str = '', second: str = '') -> int:
	"""`qa compare` - the two newest suites by default."""
	dirs = suite_dirs(ws.runs_dir)
	if len(dirs) < 2 and not (first and second):
		await ch.log('Need two recorded suites to compare. Run:  qa suite')
		return 2

	def find(name: str) -> SuiteResult | None:
		return load_suite(ws.runs_dir / name) if name else None

	current = find(second) or find(first) or load_suite(dirs[-1])
	previous = find(first) if second else (load_suite(dirs[-2]) if len(dirs) >= 2 else None)
	if current is None:
		await ch.log(f'No suite "{second or first}" under {ws.runs_dir}/.')
		return 2

	delta = compare(previous, current)
	await ch.log(f'\nComparing {previous.name if previous else "(nothing)"} → {current.name}\n')
	rows = (
		('🔻 newly failing', delta.newly_failing),
		('🔺 newly passing', delta.newly_passing),
		('still failing', delta.still_failing),
		('🆕 new', delta.added),
		('gone', delta.removed),
	)
	for label, ids in rows:
		if ids:
			await ch.emit(Event('log', f'{label}: {", ".join(ids)}', {'change': label, 'scenarios': ids}))
	if not any(ids for _, ids in rows):
		await ch.log('No change.')
	return 1 if delta.newly_failing else 0
