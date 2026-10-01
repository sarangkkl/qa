"""Assertions a recording can replay, and finding a recorded element again.

A regression test that only repeats clicks proves the app did not crash. A check proves it did
the right thing: Claude records one per expectation while it runs, and replay re-evaluates the
same ones with no model involved.

Text is compared the way a person reads it: whitespace collapsed and case ignored, since CSS
`text-transform` changes what innerText returns without changing what anyone sees.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false

from typing import Any

KINDS = ('text_visible', 'text_absent', 'url_contains', 'title_contains', 'element_visible', 'element_text')
ELEMENT_KINDS = ('element_visible', 'element_text')


def norm(text: str) -> str:
	return ' '.join(text.split()).casefold()


async def page_text(session: Any) -> str:
	"""What is visible: innerText skips hidden elements, which is the point of text_visible."""
	cdp = await session.get_or_create_cdp_session()
	reply = await cdp.cdp_client.send.Runtime.evaluate(
		params={'expression': 'document.body ? document.body.innerText : ""', 'returnByValue': True},
		session_id=cdp.session_id,
	)
	return str(reply.get('result', {}).get('value') or '')


def node_text(node: Any) -> str:
	text = node.get_all_children_text() if hasattr(node, 'get_all_children_text') else ''
	if not text and getattr(node, 'ax_node', None) is not None:
		text = node.ax_node.name or ''
	return str(text or '')


async def evaluate(session: Any, kind: str, value: str, node: Any = None) -> tuple[bool, str]:
	"""(passed, what was seen). `node` is the element for the element kinds, already located."""
	want = norm(value)
	if kind in ('text_visible', 'text_absent'):
		found = want in norm(await page_text(session))
		if kind == 'text_visible':
			return found, f'"{value}" is on the page' if found else f'"{value}" is not on the page'
		return not found, f'"{value}" is not on the page' if not found else f'"{value}" is still on the page'
	if kind == 'url_contains':
		url = str(await session.get_current_page_url())
		return want in norm(url), f'URL is {url}'
	if kind == 'title_contains':
		title = str(await session.get_current_page_title())
		return want in norm(title), f'title is "{title}"'
	if kind == 'element_visible':
		return node is not None, 'the element is there' if node is not None else 'the element is not on the page'
	if kind == 'element_text':
		if node is None:
			return False, 'the element is not on the page'
		seen = node_text(node)
		return want in norm(seen), f'the element says "{seen[:200]}"'
	raise ValueError(f'Unknown check kind "{kind}". Use one of: {", ".join(KINDS)}')


def locate(fingerprint: dict[str, Any], selector_map: dict[int, Any]) -> tuple[int | None, str]:
	"""(index in this page, how it matched) for an element recorded in an earlier one.

	A port of browser-use's cascade (`Agent._update_action_indices`, agent/service.py), which is a
	private method on an Agent - and an Agent cannot be built without an LLM. Strictest first,
	so a loose match is only used when nothing better exists.
	"""
	items = list(selector_map.items())
	frame = fingerprint.get('frame_id')
	if frame:
		items.sort(key=lambda kv: getattr(kv[1], 'frame_id', None) != frame)
	name = str(fingerprint.get('node_name') or '').lower()
	ax = fingerprint.get('ax_name')
	stable = fingerprint.get('stable_hash')

	def ax_of(node: Any) -> str | None:
		return node.ax_node.name if getattr(node, 'ax_node', None) is not None else None

	levels: list[tuple[str, Any]] = [
		('exact', lambda n: hash(n) == fingerprint.get('element_hash')),
		('stable', lambda n: stable is not None and n.compute_stable_hash() == stable),
		('xpath', lambda n: bool(fingerprint.get('x_path')) and n.xpath == fingerprint['x_path']),
		('name', lambda n: bool(ax) and str(n.node_name).lower() == name and ax_of(n) == ax),
	]
	for level, test in levels:
		for index, node in items:
			if test(node):
				return index, level
	attributes: dict[str, Any] = fingerprint.get('attributes') or {}
	for key in ('id', 'name', 'data-testid', 'aria-label'):
		wanted = attributes.get(key)
		if not wanted:
			continue
		hits = [index for index, node in items if (getattr(node, 'attributes', None) or {}).get(key) == wanted]
		if len(hits) == 1:
			return hits[0], f'attribute {key}'
	return None, ''


def describe_element(fingerprint: dict[str, Any] | None) -> str:
	if not fingerprint:
		return 'the element'
	label = fingerprint.get('ax_name') or (fingerprint.get('attributes') or {}).get('aria-label') or ''
	return f'<{str(fingerprint.get("node_name") or "?").lower()}>' + (f' "{label}"' if label else '')
