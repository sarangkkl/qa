"""Planner: drafts scenarios from workspace knowledge + the ask. Never opens a browser.

Like Claude Code plan mode: it reads the appmap, existing scenarios, and past run
verdicts, then writes draft scenario files for human review. Approval stays human.
"""

from pydantic import BaseModel, Field

from nkqa import scenarios as scenarios_mod
from nkqa.config import Config
from nkqa.execution.report import last_verdict
from nkqa.models import resolve_llm
from nkqa.scenarios import Scenario, Step
from nkqa.workspace import Workspace, slugify


class DraftStep(BaseModel):
	action: str
	expect: str = ''


class DraftScenario(BaseModel):
	area: str = Field(description='short kebab-case folder grouping, e.g. "checkout"')
	slug: str = Field(description='short kebab-case file name, e.g. "purchase-with-coupon"')
	title: str
	tags: list[str] = []
	preconditions: list[str] = []
	steps: list[DraftStep]
	out_of_scope: list[str] = []


class PlanOutput(BaseModel):
	scenarios: list[DraftScenario]
	notes: str = Field(default='', description='open questions or assumptions for the human reviewer')


PLANNER_SYSTEM = """\
You are a senior QA engineer writing MANUAL test scenarios for a human to review and
approve before an agent executes them in a real browser. Rules:
- Base scenarios ONLY on the provided app knowledge and the ask. Do not invent UI you
  have no evidence for; when unsure, keep steps generic and flag it in notes.
- Each scenario: one focused user journey, 3-8 concrete steps, each step one action.
  Attach an expectation to every step where something observable should happen.
- Steps must be executable by someone who has never seen the app.
- Put anything dangerous or irreversible (real payments, deletions, emails) in
  out_of_scope unless the ask explicitly demands it.
- Do not duplicate existing scenarios; prefer covering gaps.
"""


def gather_context(ws: Workspace, config: Config) -> str:
	parts = [f'### Application\nName: {config.app_name}\nBase URL: {config.base_url or "unknown"}']
	for f in sorted(ws.appmap_dir.rglob('*.md')):
		parts.append(f'### appmap/{f.relative_to(ws.appmap_dir)}\n{f.read_text(encoding="utf-8").strip()}')
	existing = scenarios_mod.load_all(ws.scenarios_dir)
	if existing:
		lines = [
			f'- {s.id} [{s.runnable()}, last run: {last_verdict(ws.runs_dir, s.id) or "never"}] {s.title}'
			for s in existing
		]
		parts.append('### Existing scenarios (do NOT duplicate)\n' + '\n'.join(lines))
	return '\n\n'.join(parts)


def write_drafts(ws: Workspace, drafts: list[DraftScenario], force: bool = False) -> tuple[list[Scenario], list[str]]:
	"""Serialize drafts to scenario files. Returns (written, skipped_ids)."""
	written: list[Scenario] = []
	skipped: list[str] = []
	for d in drafts:
		sid = f'{slugify(d.area)}/{slugify(d.slug)}'
		path = ws.scenarios_dir / f'{sid}.md'
		if path.exists() and not force:
			skipped.append(sid)
			continue
		s = Scenario(
			id=sid,
			path=path,
			title=d.title,
			tags=d.tags,
			preconditions=d.preconditions,
			steps=[Step(st.action, st.expect) for st in d.steps],
			out_of_scope=d.out_of_scope,
		)
		scenarios_mod.save(s)
		written.append(s)
	return written, skipped


async def plan(ws: Workspace, config: Config, ask: str, area: str = '', force: bool = False) -> int:
	llm = resolve_llm(config, 'planner')
	if llm is None:
		print("The planner needs a real model: set models.planner in config.yaml (e.g. 'smart').")
		return 2

	from browser_use.llm.messages import SystemMessage, UserMessage

	prompt = gather_context(ws, config) + f'\n\n### Ask\n{ask}'
	if area:
		prompt += f'\nPut all scenarios under the area "{slugify(area)}".'
	print(f'🧠 Planning with {config.models.get("planner")} (no browser - knowledge only)...')
	response = await llm.ainvoke(
		[SystemMessage(content=PLANNER_SYSTEM), UserMessage(content=prompt)], output_format=PlanOutput
	)
	output = response.completion

	written, skipped = write_drafts(ws, output.scenarios, force)
	for s in written:
		print(f'📝 draft  {s.id}  ({len(s.steps)} steps) - {s.title}')
	for sid in skipped:
		print(f'⏭️  kept existing {sid} (use --force to overwrite)')
	if output.notes:
		print(f'\n🗒️  Planner notes: {output.notes}')
	if written:
		print(f'\nReview the files under {ws.scenarios_dir}/, edit freely, then:  qa approve <id>')
	else:
		print('\nNothing new to draft.')
	return 0
