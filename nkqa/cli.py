"""qa - AI QA teammate. Dispatch only; the verbs live in nkqa.actions."""

import argparse
import sys

from dotenv import load_dotenv

from nkqa import actions
from nkqa import workspace as workspace_mod
from nkqa.workspace import Workspace


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog='qa', description=__doc__)
	sub = parser.add_subparsers(dest='command')

	sub.add_parser('init', help='create a QA workspace in the current directory')
	sub.add_parser('chat', help='interactive session (same as running `qa` with no arguments)')

	plan = sub.add_parser('plan', help='draft test scenarios from app knowledge (no browser)')
	plan.add_argument('ask', nargs='?', default='', help='what to test, e.g. "the checkout flow"')
	plan.add_argument('--ticket', default='', metavar='KEY', help='plan from a Jira ticket, e.g. PROJ-123')
	plan.add_argument('--area', default='', help='force all drafts under this scenario area')
	plan.add_argument('--force', action='store_true', help='overwrite existing scenario files')

	revise = sub.add_parser('revise', help='rewrite a scenario from an instruction (invalidates approval)')
	revise.add_argument('id', help='scenario id')
	revise.add_argument('instruction', help='how to change it, e.g. "make step 3 stricter"')

	run = sub.add_parser('run', help='execute an approved scenario')
	run.add_argument('id', nargs='?', default='', help='scenario id, e.g. checkout/coupon')
	run.add_argument('--model', default=None, metavar='MODEL', help='executor model (alias or browser-use name)')

	explore = sub.add_parser('explore', help='record a freeform AI-driven test (no scenario)')
	explore.add_argument('url', nargs='?', default='', help='URL to test')
	explore.add_argument('focus', nargs='?', default='', help='what to focus on')
	explore.add_argument('--name', default='', metavar='TEST_NAME', help='meaningful name for this recording')
	explore.add_argument('--model', default=None, metavar='MODEL', help='executor model (alias or browser-use name)')

	sub.add_parser('scenarios', help='list scenarios with status and last verdict')

	approve = sub.add_parser('approve', help='review a scenario and approve it (hash-bound)')
	approve.add_argument('id', help='scenario id')

	learn = sub.add_parser('learn', help='ingest app knowledge from an annotated doc (md + screenshots)')
	learn.add_argument('path', help='markdown file with embedded images, or a folder of screenshots')

	reflect = sub.add_parser('reflect', help='update the appmap from a past run (auto after runs by default)')
	reflect.add_argument('run', help='run dir name under runs/')

	crawl = sub.add_parser('crawl', help='explore the live app read-only and enrich the appmap (optional)')
	crawl.add_argument('--pages', type=int, default=0, help='page budget (default: appmap.crawl_pages)')
	crawl.add_argument('--model', default=None, metavar='MODEL', help='executor model override')

	bug = sub.add_parser('file-bug', help='file a Jira bug from a failed run (human-instructed only)')
	bug.add_argument('run', help='run dir name under runs/, e.g. checkout-coupon--20260826-2238')
	bug.add_argument('--step', type=int, default=0, help='which failed step to file (when several failed)')
	bug.add_argument('--project', default='', metavar='KEY', help='Jira project key (default: jira.project in config)')

	replay = sub.add_parser('replay', help='replay a recorded run deterministically, without the LLM')
	replay.add_argument('name', nargs='?', default='', help='run name, dir, or history.json path')
	replay.add_argument('--all', action='store_true', help='replay every recorded run (CI mode)')
	replay.add_argument('--var', action='append', default=[], metavar='KEY=VALUE', help='override a recorded value')

	sub.add_parser('list', help='list all recorded runs')
	sub.add_parser('models', help='show model roles, providers, and whether their API keys are set')
	sub.add_parser('version', help='show nkqa and browser-use versions')
	return parser


def require_workspace() -> Workspace:
	ws = workspace_mod.find()
	if ws is None:
		print('Not inside a QA workspace. Create one first:  qa init')
		sys.exit(2)
	return ws


def main() -> None:
	load_dotenv()
	args = build_parser().parse_args()
	command = args.command

	if command is None or command == 'chat':
		from nkqa.shell.session import start

		sys.exit(actions.run_sync(start()))
	if command == 'version':
		from importlib.metadata import version as pkg_version

		print(f'nkqa {pkg_version("nkqa")} (browser-use {pkg_version("browser-use")})')
		sys.exit(0)
	if command == 'init':
		sys.exit(actions.init())
	if command == 'models':
		sys.exit(actions.models())

	ws = require_workspace()
	if command == 'scenarios':
		sys.exit(actions.list_scenarios(ws))
	if command == 'approve':
		sys.exit(actions.approve(ws, args.id))
	if command == 'list':
		sys.exit(actions.list_runs(ws))
	if command == 'plan':
		sys.exit(actions.run_sync(actions.plan(ws, args.ask, args.ticket, args.area, args.force)))
	if command == 'revise':
		sys.exit(actions.run_sync(actions.revise(ws, args.id, args.instruction)))
	if command == 'run':
		sys.exit(actions.run_sync(actions.run_scenario(ws, args.id, args.model)))
	if command == 'explore':
		sys.exit(actions.run_sync(actions.explore(ws, args.url, args.focus, args.name, args.model)))
	if command == 'learn':
		sys.exit(actions.run_sync(actions.learn(ws, args.path)))
	if command == 'reflect':
		sys.exit(actions.run_sync(actions.reflect(ws, args.run)))
	if command == 'crawl':
		sys.exit(actions.run_sync(actions.crawl(ws, args.pages, args.model)))
	if command == 'file-bug':
		sys.exit(actions.run_sync(actions.file_bug(ws, args.run, args.step, args.project)))
	if command == 'replay':
		sys.exit(actions.run_sync(actions.replay(ws, args.name, args.all, args.var)))

	build_parser().print_help()
	sys.exit(2)
