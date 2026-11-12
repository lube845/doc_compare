import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'doc-compare-theme'

function readInitialTheme() {
  if (typeof window === 'undefined') return 'light'
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* storage may be unavailable in private mode */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/**
 * Keeps the light / dark theme in sync with the `data-theme` attribute on the
 * document root, so every component can switch by swapping the CSS variables.
 */
export default function useTheme() {
  const [theme, setTheme] = useState(readInitialTheme)

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') root.setAttribute('data-theme', 'dark')
    else root.removeAttribute('data-theme')
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* ignore quota / privacy mode errors */
    }
  }, [theme])

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'))
  }, [])

  return { theme, toggleTheme }
}
