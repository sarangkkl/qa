"""PyInstaller entry point for the sidecar. Kept beside the shell that bundles it."""

import sys

if sys.platform == 'darwin':
	# This binary lives inside Kiwame.app, so its first AppKit call (browser-use measures the
	# screen while it imports) makes macOS treat it as the app itself: a second Kiwame in the
	# Dock, bouncing forever, and quitting it kills the chat. Background-only, before any import.
	from AppKit import NSApplication, NSApplicationActivationPolicyProhibited

	NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyProhibited)

from nkqa.server.main import main  # noqa: E402 - must come after the policy is set

if __name__ == '__main__':
	main()
