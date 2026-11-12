import { useEffect, useState } from 'react'

/** Delays a rapidly changing value so filtering does not run on every keystroke. */
export default function useDebouncedValue(value, delay = 180) {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  return debounced
}
