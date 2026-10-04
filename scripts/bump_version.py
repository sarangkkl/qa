"""Set the release version everywhere it lives: python3 scripts/bump_version.py 0.2.0

pyproject.toml (the Python package), desktop/package.json (the app; tauri.conf.json reads its
version from here) and desktop/src-tauri/Cargo.toml (the Rust shell). Stdlib only.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = {
	ROOT / 'pyproject.toml': r"(?m)^(version = ')[^']*(')",
	ROOT / 'desktop/package.json': r'(?m)^(  "version": ")[^"]*(")',
	ROOT / 'desktop/src-tauri/Cargo.toml': r'(?m)^(version = ")[^"]*(")',
}


def main() -> int:
	if len(sys.argv) != 2 or not re.fullmatch(r'\d+\.\d+\.\d+', sys.argv[1]):
		print(__doc__.strip().splitlines()[0])
		return 2
	version = sys.argv[1]
	for path, pattern in FILES.items():
		text = path.read_text(encoding='utf-8')
		new, count = re.subn(pattern, rf'\g<1>{version}\g<2>', text, count=1)
		if count != 1:
			print(f'could not find the version in {path.relative_to(ROOT)}')
			return 1
		path.write_text(new, encoding='utf-8')
		print(f'{path.relative_to(ROOT)} -> {version}')
	print(f'\nNext:\n  git commit -am "Release v{version}"\n  git tag v{version}\n  git push origin HEAD v{version}')
	return 0


if __name__ == '__main__':
	sys.exit(main())
