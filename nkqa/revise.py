"""Rewrite one scenario from a natural-language instruction (planner role).

Revising changes the content, so a stored approval hash stops matching: the scenario
goes STALE and the runner refuses it until a human re-approves. The gate holds for free.
"""

from browser_use.llm.messages import SystemMessage, UserMessage
from pydantic import BaseModel, Field

from nkqa import scenarios as scenarios_mod
from nkqa.config import Config
from nkqa.models import resolve_llm
from nkqa.scenarios import Step
from nkqa.workspace import Workspace


class RevisedStep(BaseModel):
	action: str
	expect: str = ''


class RevisedScenario(BaseModel):
	title: str
	preconditions: list[str] = []
	steps: list[RevisedStep]
	out_of_scope: list[str] = []
	changes: str = Field(default='', description='one line describing what you changed')


REVISE_SYSTEM = """\
You revise a single QA test scenario for a human reviewer. You are given the current
scenario and an instruction. Apply the instruction and return the COMPLETE revised
scenario. Rules:
- Change only what the instruction asks for; keep everything else word for word.
- Keep steps concrete and executable by someone who has never seen the app; attach an
  expectation to every step where something observable should happen.
- Never invent UI you have no evidence for.
- Keep dangerous or irreversible actions in out_of_scope unless explicitly asked.
"""


async def revise(ws: Workspace, config: Config, scenario_id: str, instruction: str) -> int:
	s = scenarios_mod.find(ws.scenarios_dir, scenario_id)
	if s is None:
		print(f'No scenario "{scenario_id}". See:  qa scenarios')
		return 2
	if not instruction.strip():
		print('Tell me how to revise it, e.g.  qa revise auth/login "make step 3 stricter"')
		return 2
	llm = resolve_llm(config, 'planner')
	if llm is None:
		print("Revising needs a real model: set models.planner in config.yaml (e.g. 'smart').")
		return 2

	was_approved = s.runnable() == 'ok'
	current = s.path.read_text(encoding='utf-8')
	print(f'✏️  Revising {s.id}...')
	response = await llm.ainvoke(
		[
			SystemMessage(content=REVISE_SYSTEM),
			UserMessage(content=f'Current scenario:\n\n{current}\n\n### Instruction\n{instruction}'),
		],
		output_format=RevisedScenario,
	)
	r = response.completion

	s.title = r.title
	s.preconditions = r.preconditions
	s.steps = [Step(st.action, st.expect) for st in r.steps]
	s.out_of_scope = r.out_of_scope
	scenarios_mod.save(s)

	print(f'📝 {s.id}: {r.changes or "revised"} ({len(s.steps)} steps)')
	if was_approved:
		print(f'⚠️  Approval invalidated - the content changed. Re-approve to run:  qa approve {s.id}')
	return 0
