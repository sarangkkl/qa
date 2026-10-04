/** Light, dark, or whatever the OS says - like VS Code. Remembered per machine. */

import { useEffect, useState } from 'react'
import { inTauri } from './api/connection'

export type Theme = 'system' | 'light' | 'dark'
export const THEMES: Theme[] = ['system', 'light', 'dark']
const KEY = 'nkqa.theme'

function saved(): Theme {
	try {
		const value = localStorage.getItem(KEY)
		return value === 'light' || value === 'dark' ? value : 'system'
	} catch {
		return 'system'
	}
}

export function useTheme(): [Theme, (t: Theme) => void] {
	const [theme, setTheme] = useState<Theme>(saved)

	useEffect(() => {
		const root = document.documentElement
		if (theme === 'system') root.removeAttribute('data-theme')
		else root.setAttribute('data-theme', theme)
		try {
			localStorage.setItem(KEY, theme)
		} catch {
			// storage can be unavailable; the choice still holds for this window
		}
		// The native title bar follows too, or a light window gets a dark frame.
		if (inTauri()) {
			void import('@tauri-apps/api/window')
				.then(({ getCurrentWindow }) => getCurrentWindow().setTheme(theme === 'system' ? null : theme))
				.catch(() => undefined)
		}
	}, [theme])

	return [theme, setTheme]
}
