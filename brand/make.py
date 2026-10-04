"""Regenerate the Kiwame SVGs: python3 brand/make.py (from anywhere). Stdlib only, no fonts.

PNGs are screenshots of these SVGs (any browser at the listed size, transparent background);
desktop icons come from png/kiwame-app-icon-1024.png via `npx tauri icon` in desktop/.
"""

from pathlib import Path

RED, RED_TOP, RED_BOT, INK, PAPER = '#D63B2A', '#E2472F', '#C3301F', '#1C1B1A', '#F6F1E7'
K_STEM = 'M29 25V75'
K_ARMS = 'M64 25L40 50L54 70L72 41'  # upper arm, then the lower leg drawn as a check mark
LINE = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
SEAL = '<rect x="4" y="4" width="92" height="92" rx="22" fill="{}"/>'


def mark(color: str = '#fff', width: float = 11) -> str:
	return ''.join(f'<path {LINE} stroke="{color}" stroke-width="{width}" d="{d}"/>' for d in (K_STEM, K_ARMS))


def frame(color: str = '#fff') -> str:
	return f'<rect x="11" y="11" width="78" height="78" rx="15" fill="none" stroke="{color}" stroke-opacity=".9" stroke-width="2.2"/>'


def svg(view: str, body: str) -> str:
	return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">{body}</svg>\n'


def wordmark(color: str) -> str:
	"""'kiwame' in geometric strokes. Baseline 90, x-height 40, stroke 10."""
	paths = [
		'M5 12V90', 'M40 42L9 70', 'M21 59L42 90',  # k
		'M62 42V90',  # i
		'M80 42L93 90L107 52L121 90L134 42',  # w
		'M206 42V90',  # a (stem; bowl below)
		'M228 90V58A18 18 0 0 1 264 58V90M264 58A18 18 0 0 1 300 58V90',  # m
		'M326 66H376A25 25 0 1 0 370 82',  # e
	]
	body = ''.join(f'<path {LINE} stroke="{color}" stroke-width="10" d="{d}"/>' for d in paths)
	return body + f'<circle cx="62" cy="21" r="6.5" fill="{color}"/><circle cx="181" cy="66" r="25" fill="none" stroke="{color}" stroke-width="10"/>'


def knockout(color: str) -> str:
	return svg('0 0 100 100',
		f'<defs><mask id="k"><rect width="100" height="100" fill="#fff"/>{frame("#000")}{mark("#000")}</mask></defs>'
		f'<rect x="4" y="4" width="92" height="92" rx="22" fill="{color}" mask="url(#k)"/>')


def lockup(text: str) -> str:
	return svg('0 0 430 100', SEAL.format(RED) + frame() + mark() + f'<g transform="translate(122 9) scale(.78)">{wordmark(text)}</g>')


FILES = {
	'kiwame-mark.svg': svg('0 0 100 100', SEAL.format(RED) + frame() + mark()),
	'kiwame-mark-simple.svg': svg('0 0 100 100', f'<rect width="100" height="100" rx="22" fill="{RED}"/>' + mark(width=12)),
	'kiwame-mark-black.svg': knockout(INK),
	'kiwame-mark-white.svg': knockout('#fff'),
	'kiwame-wordmark.svg': svg('0 4 390 94', wordmark(INK)),
	'kiwame-wordmark-white.svg': svg('0 4 390 94', wordmark(PAPER)),
	'kiwame-logo.svg': lockup(INK),
	'kiwame-logo-white.svg': lockup(PAPER),
	# 824 px body in a 1024 canvas, as macOS expects; gradient and shadow only here.
	'kiwame-app-icon.svg': svg('-7.15 -7.15 114.3 114.3',
		f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{RED_TOP}"/><stop offset="1" stop-color="{RED_BOT}"/></linearGradient>'
		'<filter id="s" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="1.2" stdDeviation="1.6" flood-color="#000" flood-opacity=".28"/></filter></defs>'
		'<rect x="4" y="4" width="92" height="92" rx="21" fill="url(#g)" filter="url(#s)"/>' + frame() + mark()),
}

if __name__ == '__main__':
	here = Path(__file__).parent
	for name, text in FILES.items():
		(here / name).write_text(text, encoding='utf-8')
	print(f'{len(FILES)} SVGs written to {here}')
