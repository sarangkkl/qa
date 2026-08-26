"""Ingest app knowledge into the appmap from an annotated document.

Primary input: a markdown file the human wrote - embedded screenshots plus text saying
what each screen is, the roles, the flows, the quirks. Fallback: a bare folder of
images (same pipeline, less signal). The multimodal planner-role model distills it
into appmap files; open questions are surfaced, never guessed away.
"""

import base64
import re
from pathlib import Path

from browser_use.llm.messages import (
	BaseMessage,
	ContentPartImageParam,
	ContentPartTextParam,
	ImageURL,
	SystemMessage,
	UserMessage,
)

from nkqa import appmap
from nkqa.appmap import AppmapUpdate
from nkqa.config import Config
from nkqa.models import resolve_llm
from nkqa.workspace import Workspace

IMAGE_LINK_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)\)')
MEDIA_TYPES = {
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.gif': 'image/gif',
	'.webp': 'image/webp',
}
MAX_IMAGES = 20
MAX_IMAGE_BYTES = 4_000_000  # anthropic caps ~5MB/image

INGEST_SYSTEM = """\
You are building a QA knowledge base (the "appmap") for a web application from an
annotated document: screenshots plus the team's own descriptions. Produce appmap files:
- overview.md: what the app is, base URL, roles/test accounts, list of areas. MERGE
  with the existing overview - keep what humans wrote, fill the blanks.
- pages/<slug>.md: one per screen - purpose, key elements visible in the screenshot,
  navigation in/out, quirks mentioned.
- flows/<slug>.md: cross-page user journeys, as ordered step lists. Flows are the most
  valuable content - extract every flow stated or clearly implied.
Rules: describe ONLY what the document shows or says - never invent UI. Keep files
short and factual. Put every uncertainty or unanswered question into notes instead of
guessing. File paths must be relative like 'pages/login.md'.
"""


def collect_inputs(path: Path) -> tuple[str, list[Path]]:
	"""(document text, ordered image paths) from an .md file, a bare folder, or one image."""
	if path.is_dir():
		text = '\n\n'.join(
			f.read_text(encoding='utf-8') for f in sorted(path.glob('*.md')) + sorted(path.glob('*.txt'))
		)
		images = sorted(f for f in path.rglob('*') if f.suffix.lower() in MEDIA_TYPES)
		return text, images
	if path.suffix.lower() in MEDIA_TYPES:
		return '', [path]
	text = path.read_text(encoding='utf-8')
	images: list[Path] = []
	for link in IMAGE_LINK_RE.findall(text):
		if link.startswith(('http://', 'https://')):
			print(f'⚠️  Skipping remote image {link} (only local files are read)')
			continue
		img = (path.parent / link).resolve()
		if img.is_file():
			images.append(img)
		else:
			print(f'⚠️  Image not found, skipping: {link}')
	return text, images


def build_messages(text: str, images: list[Path], current_appmap: dict[str, str]) -> list[BaseMessage]:
	parts: list[ContentPartTextParam | ContentPartImageParam] = []
	if current_appmap:
		existing = '\n\n'.join(
			f'--- appmap/{name} (current) ---\n{content}' for name, content in current_appmap.items()
		)
		parts.append(ContentPartTextParam(text=f'Existing appmap to merge with:\n\n{existing}'))
	parts.append(ContentPartTextParam(text=f'The annotated document:\n\n{text or "(no text - screenshots only)"}'))

	if len(images) > MAX_IMAGES:
		print(f'⚠️  {len(images)} images; using the first {MAX_IMAGES}')
		images = images[:MAX_IMAGES]
	for img in images:
		data = img.read_bytes()
		if len(data) > MAX_IMAGE_BYTES:
			print(f'⚠️  {img.name} is {len(data) // 1_000_000}MB (>4MB), skipping')
			continue
		media = MEDIA_TYPES[img.suffix.lower()]
		parts.append(ContentPartTextParam(text=f'Screenshot file: {img.name}'))
		parts.append(
			ContentPartImageParam(
				image_url=ImageURL(url=f'data:{media};base64,{base64.b64encode(data).decode()}', media_type=media)  # type: ignore[arg-type]
			)
		)
	return [SystemMessage(content=INGEST_SYSTEM), UserMessage(content=parts)]


async def learn(ws: Workspace, config: Config, path_str: str) -> int:
	path = Path(path_str).expanduser().resolve()
	if not path.exists():
		print(f'No such file or folder: {path}')
		return 2
	text, images = collect_inputs(path)
	if not text and not images:
		print(f'Nothing to ingest in {path} (no markdown, no images).')
		return 2
	llm = resolve_llm(config, 'planner')
	if llm is None:
		print("qa learn needs a real (multimodal) model: set models.planner in config.yaml (e.g. 'smart').")
		return 2

	print(f'📚 Ingesting {path.name}: {len(text)} chars of text, {len(images)} screenshot(s)...')
	response = await llm.ainvoke(build_messages(text, images, appmap.read_all(ws)), output_format=AppmapUpdate)
	update = response.completion

	written = appmap.apply(ws, update, f'appmap: learned from {path.name}')
	for f in written:
		print(f'🗺️  appmap/{f}')
	if update.notes:
		print(f'\n🗒️  Open questions: {update.notes}')
	if written:
		committed = ' (git-committed)' if appmap.in_git_repo(ws) else ''
		print(
			f'\n{len(written)} appmap file(s) updated{committed}. Review:  git diff appmap/  ·  Plan:  qa plan "<ask>"'
		)
	else:
		print('The document produced no appmap changes.')
	return 0
