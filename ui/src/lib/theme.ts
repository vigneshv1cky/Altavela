import { useEffect, useState } from "react"

export type Theme = "light" | "dark" | "system"

function resolveSystem(): "light" | "dark" {
  if (window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark"
  return "light"
}

function applied(): Theme {
  try {
    const raw = localStorage.getItem("theme")
    if (raw === "light" || raw === "dark" || raw === "system") return raw
  } catch { /* ignore */ }
  return "system"
}

function apply(t: Theme) {
  const dark = t === "dark" || (t === "system" && resolveSystem() === "dark")
  document.documentElement.classList.toggle("dark", dark)
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(applied)
  useEffect(() => { apply(theme) }, [theme])
  const cycle = () => {
    const order: Theme[] = ["light", "dark", "system"]
    const next = order[(order.indexOf(theme) + 1) % order.length]
    apply(next)
    try { localStorage.setItem("theme", next) } catch { /* ignore */ }
    setTheme(next)
  }
  return [theme, cycle]
}
