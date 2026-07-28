import { useState, useEffect } from "react"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ArrowDown, ArrowUp, Loader2, Moon, Monitor, Search, Sun, X } from "lucide-react"

interface Pick {
  id: number; ts: string; question: string; direction: string; score: number
  entry_price: number | null; current_price: number | null; pnl_pct: number | null
}
interface Stats { total_picks: number; resolved: number; wins: number; win_rate: number | null; closed: number; closed_wins: number; closed_win_rate: number | null; total_pnl_pct: number; median_pnl_pct: number }
interface TimelineRow { id: number; ts: string; direction: string; question: string; resolved: number; outcome: number | null; adjusted_score: number; verdict: string; pnl_return_pct: number | null; exit_ts: string | null; exit_reason: string | null; exit_price: number | null; market_yes_price: number | null; market_no_price: number | null }
interface TokenRow { role: string; model: string; input_tok: number; output_tok: number }

function LiveCard({ p, onSelect }: { p: Pick; onSelect: (id: number) => void }) {
  const isYes = p.direction === "BUY_YES"
  const pos = (p.pnl_pct ?? 0) >= 0
  return (
    <Card className="space-y-2 p-3 cursor-pointer hover:border-indigo-400 transition-colors" onClick={() => onSelect(p.id)}>
      <div className="flex items-center gap-2">
        {isYes ? <ArrowUp className="h-4 w-4 shrink-0 text-emerald-500" /> : <ArrowDown className="h-4 w-4 shrink-0 text-red-500" />}
        <Badge className={`h-5 text-[10px] ${isYes ? "bg-emerald-600 text-white hover:bg-emerald-700" : "bg-red-600 text-white hover:bg-red-700"}`}>{isYes ? "YES" : "NO"}</Badge>
        <span className="ml-auto font-mono text-sm font-semibold tabular-nums">
          {p.pnl_pct != null ? <span className={pos ? "text-emerald-500" : "text-red-500"}>{pos ? "+" : ""}{p.pnl_pct}%</span>
            : p.current_price != null ? <span className="text-muted-foreground">${p.current_price}</span>
            : <span className="text-muted-foreground">pending</span>}
        </span>
      </div>
      <div className="text-sm line-clamp-2">{p.question}</div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>entry {p.entry_price ?? "—"}{p.current_price != null && p.entry_price ? ` · now ${p.current_price}` : ""}</span>
        <span>{new Date(p.ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
    </Card>
  )
}

function groupByDate<T extends { ts?: string }>(items: T[]): { label: string; items: T[] }[] {
  const groups: { label: string; items: T[] }[] = []
  for (const item of items) {
    const d = item.ts ? new Date(item.ts).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "Unknown"
    const last = groups[groups.length - 1]
    if (last && last.label === d) { last.items.push(item) } else { groups.push({ label: d, items: [item] }) }
  }
  return groups
}

export default function App() {
  const [running, setRunning] = useState(false)
  const [stats, setStats] = useState<Stats>({ total_picks: 0, resolved: 0, wins: 0, win_rate: null, closed: 0, closed_wins: 0, closed_win_rate: null, total_pnl_pct: 0, median_pnl_pct: 0 })
  const [positions, setPositions] = useState<Pick[]>([])
  const [timeline, setTimeline] = useState<TimelineRow[]>([])
  const [tokens, setTokens] = useState<{ usage: TokenRow[]; total_tokens: number }>({ usage: [], total_tokens: 0 })
  const [loaded, setLoaded] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [theme, setTheme] = useState<"light" | "dark" | "system">(() => {
    try { const t = localStorage.getItem("theme"); if (t === "light" || t === "dark" || t === "system") return t as any } catch {}
    return "system"
  })
  useEffect(() => {
    const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches)
    document.documentElement.classList.toggle("dark", dark)
  }, [theme])
  const toggleTheme = () => {
    const order: ("light" | "dark" | "system")[] = ["light", "dark", "system"]
    const next = order[(order.indexOf(theme) + 1) % 3]
    try { localStorage.setItem("theme", next) } catch {}
    setTheme(next)
  }

  async function refresh() {
    const s = await fetch("/api/stats").then(r => r.json()); setStats(s)
    const p = await fetch("/api/picks").then(r => r.json()); setPositions(p)
    const t = await fetch("/api/timelines").then(r => r.json()); setTimeline(t)
    const tok = await fetch("/api/tokens").then(r => r.json()); setTokens(tok)
    setLoaded(true)
  }
  useEffect(() => { refresh(); const t = setInterval(refresh, 30000); return () => clearInterval(t) }, [])

  function run() {
    setRunning(true)
    const es = new EventSource("/api/find-markets")
    es.onmessage = (e) => { const ev = JSON.parse(e.data); if (ev.type === "done") { setRunning(false); es.close(); refresh() } }
    es.onerror = () => { setRunning(false); es.close() }
  }

  const winRate = stats.closed_win_rate

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="z-30 shrink-0 border-b bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1200px] items-center gap-4 px-5 py-3">
          <div className="flex items-center gap-2.5"><div className="h-3.5 w-3.5 rotate-45 rounded-[3px] bg-indigo-500" /><div className="leading-none"><div className="text-sm font-semibold tracking-tight">Altavela</div><div className="mt-0.5 text-[11px] text-muted-foreground">prediction-market research desk</div></div></div>
          <div className={`ml-2 flex items-center gap-1.5 text-xs font-medium ${running ? "text-emerald-500" : "text-muted-foreground"}`}><span className={`h-2 w-2 rounded-full ${running ? "animate-pulse bg-emerald-500" : "bg-muted-foreground/40"}`} />{running ? "running" : "idle"}</div>
          <div className="ml-auto flex items-center gap-4">
            <div className="hidden text-right sm:flex sm:gap-4 text-xs text-muted-foreground">
              <div><div className="font-mono font-semibold">{stats.total_picks}</div><div className="text-[10px] uppercase">picks</div></div>
              <div><div className="font-mono font-semibold">{stats.closed}</div><div className="text-[10px] uppercase">closed</div></div>
              <div><div className={`font-mono font-semibold ${winRate != null && winRate > 50 ? "text-emerald-500" : winRate != null ? "text-red-500" : ""}`}>{winRate != null ? `${winRate}%` : "—"}</div><div className="text-[10px] uppercase">win rate</div></div>
            </div>
            <Button onClick={run} disabled={running} size="default" variant="default" className="gap-1.5">{running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}{running ? "Scanning…" : "Find Markets"}</Button>
            <button onClick={toggleTheme} aria-label="Toggle theme" title={theme === "dark" ? "Dark" : theme === "light" ? "Light" : "System"} className="grid h-8 w-8 place-items-center rounded-md border text-muted-foreground transition-colors hover:text-foreground">{theme === "dark" ? <Moon className="h-4 w-4" /> : theme === "light" ? <Sun className="h-4 w-4" /> : <Monitor className="h-4 w-4" />}</button>
          </div>
        </div>
      </header>
      <main className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto grid max-w-[1200px] grid-cols-1 gap-5 px-5 py-5">
          <div className="no-scrollbar min-w-0">
            <Tabs defaultValue="live" className="gap-4">
              <TabsList className="h-9 bg-card p-1">
                <TabsTrigger value="live" className="px-3 text-sm data-active:bg-indigo-600 data-active:text-white">Live</TabsTrigger>
                <TabsTrigger value="track" className="px-3 text-sm data-active:bg-indigo-600 data-active:text-white">History</TabsTrigger>
                <TabsTrigger value="tokens" className="px-3 text-sm data-active:bg-indigo-600 data-active:text-white">Usage</TabsTrigger>
              </TabsList>
              <TabsContent value="live">{positions.length === 0 ? <Card className="p-8 text-center text-sm text-muted-foreground">No open positions</Card> : <div className="space-y-4"><div className="text-[11px] text-muted-foreground">{positions.length} open</div>{groupByDate([...positions].sort((a, b) => (b.ts || "").localeCompare(a.ts || ""))).map(g => <div key={g.label}><div className="mb-2 text-xs font-semibold text-muted-foreground">{g.label}</div><div className="space-y-2">{g.items.map(p => <LiveCard key={p.id} p={p} onSelect={setSelected} />)}</div></div>)}</div>}</TabsContent>
              <TabsContent value="track">{timeline.length === 0 ? <Card className="p-8 text-center text-sm text-muted-foreground">No picks yet</Card> : <div className="space-y-4"><div className="text-[11px] text-muted-foreground">{timeline.filter(t => t.resolved).length} resolved · {timeline.length} total</div><Card className={`flex items-center justify-between p-3 ${stats.total_pnl_pct >= 0 ? "border-emerald-500/30" : "border-red-500/30"}`}><span className="text-xs text-muted-foreground">Overall P&amp;L</span><div className="text-right"><span className={`font-mono text-lg font-bold tabular-nums ${stats.total_pnl_pct >= 0 ? "text-emerald-500" : "text-red-500"}`}>{stats.total_pnl_pct >= 0 ? "+" : ""}{stats.total_pnl_pct}%</span><div className="text-[10px] text-muted-foreground">median <span className={stats.median_pnl_pct >= 0 ? "text-emerald-500" : "text-red-500"}>{stats.median_pnl_pct >= 0 ? "+" : ""}{stats.median_pnl_pct}%</span></div></div></Card>{groupByDate([...timeline].sort((a, b) => (b.exit_ts || "").localeCompare(a.exit_ts || ""))).map(g => <div key={g.label}><div className="mb-2 text-xs font-semibold text-muted-foreground">{g.label}</div><div className="space-y-2">{g.items.map(t => {
                  const exited = !!t.exit_ts
                  const entry = t.direction === "BUY_YES" ? t.market_yes_price : t.market_no_price
                  const exitPnl = (exited && t.exit_price != null && entry && entry > 0)
                    ? ((t.exit_price - entry) / entry * 100) : null
                  const resolvedPnl = t.resolved ? t.pnl_return_pct : null
                  const pnl = resolvedPnl ?? exitPnl
                  const status = t.resolved
                    ? (t.outcome === 1 ? "WIN" : "LOSS")
                    : exited ? "exited" : "open"
                  const statusVariant = t.resolved
                    ? (t.outcome === 1 ? "default" as const : "destructive" as const)
                    : exited ? "secondary" as const : undefined
                  return <Card key={t.id} className="space-y-1.5 p-3 cursor-pointer hover:border-indigo-400 transition-colors" onClick={() => setSelected(t.id)}>
                    <div className="flex items-center justify-between gap-2">
                      <Badge className={`h-5 text-[10px] shrink-0 ${t.direction === "BUY_YES" ? "bg-emerald-600 text-white hover:bg-emerald-700" : "bg-red-600 text-white hover:bg-red-700"}`}>{t.direction === "BUY_YES" ? "YES" : "NO"}</Badge>
                      {statusVariant
                        ? <Badge variant={statusVariant} className="h-5 text-[10px] shrink-0">{status}</Badge>
                        : <span className="text-[10px] text-muted-foreground">{status}</span>}
                    </div>
                    <div className="text-sm line-clamp-2">{t.question}</div>
                    <div className="text-[11px] text-muted-foreground flex justify-between gap-2">
                      <span className="truncate">{new Date(t.ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}{exited ? ` → ${new Date(t.exit_ts!).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}` : ""}</span>
                      {pnl != null && <span className={`shrink-0 ${pnl >= 0 ? "text-emerald-500" : "text-red-500"}`}>{pnl >= 0 ? "+" : ""}{pnl.toFixed(1)}%</span>}
                    </div>
                  </Card>
                })}</div></div>)}</div>}</TabsContent>
              <TabsContent value="tokens">{tokens.usage.length === 0 ? <Card className="p-8 text-center text-sm text-muted-foreground">No usage yet</Card> : <div className="space-y-2"><div className="text-[11px] text-muted-foreground">Total: {(tokens.total_tokens / 1000).toFixed(0)}k</div>{tokens.usage.map((t, i) => <Card key={i} className="flex items-center justify-between px-3 py-2 text-sm"><span className="font-medium">{t.role}</span><span className="text-muted-foreground">{t.model} · {((t.input_tok + t.output_tok) / 1000).toFixed(0)}k</span></Card>)}</div>}</TabsContent>
            </Tabs>
          </div>
        </div>
      </main>
      <PickSheet pickId={selected} onClose={() => setSelected(null)} />
    </div>
  )
}

function PickSheet({ pickId, onClose }: { pickId: number | null; onClose: () => void }) {
  const [data, setData] = useState<any>(null)
  useEffect(() => { if (pickId) fetch(`/api/pick/${pickId}`).then(r => r.json()).then(setData); else setData(null) }, [pickId])
  if (!pickId) return null
  const isYes = data?.direction === "BUY_YES"; const dirColor = isYes ? "text-emerald-400" : "text-red-400"; const deb = data?.debate
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border bg-background shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b px-4 py-3 text-sm"><span className={dirColor}>{isYes ? "YES" : "NO"}</span><span className="font-bold"> {data?.question}</span><button onClick={onClose} className="grid h-7 w-7 place-items-center rounded text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button></div>
        <div className="no-scrollbar flex-1 overflow-y-auto p-4 text-sm leading-relaxed">{!data ? <div className="text-muted-foreground">Loading…</div> : (<div className="space-y-3"><div><div className="text-muted-foreground mb-1">── The Call ──</div><span className={`font-semibold ${isYes ? "text-emerald-400" : "text-red-400"}`}>[{isYes ? "BUY_YES" : "BUY_NO"}]</span><span className="text-muted-foreground"> · prob {data.est_probability?.toFixed(2)} · conf {Math.round(data.adjusted_score ?? data.score)}</span>{data.verdict && <span className="text-muted-foreground"> · {data.verdict}</span>}<div className="text-muted-foreground">entry: YES ${data.market_yes_price} · NO ${data.market_no_price}{data.resolved && <span className={data.outcome === 1 ? "text-emerald-400" : "text-red-400"}> · {data.outcome === 1 ? "WIN" : "LOSS"}</span>}{data.exit_ts && !data.resolved && (() => { const e = data.direction === "BUY_YES" ? data.market_yes_price : data.market_no_price; const ep = data.exit_price; const ePnl = e && ep ? ((ep - e) / e * 100) : null; return <span className="text-orange-400"> · exited @ ${ep}{ePnl != null && ` (${ePnl >= 0 ? "+" : ""}${ePnl.toFixed(1)}%)`}</span> })()}</div></div>{data.exit_ts && !data.resolved && <div><div className="text-muted-foreground mb-1">── Exit ──</div><span className="text-orange-400 font-semibold">[EXIT]</span><span> {data.exit_reason ?? "No reason recorded"}</span></div>}{data.triage_reason && <div><div className="text-muted-foreground mb-1">── Why we looked ──</div><span className="text-indigo-400 font-semibold">[SCOUT]</span><span> {data.triage_reason}</span></div>}{data.thesis && <div><div className="text-muted-foreground mb-1">── Researcher ──</div><span className="text-blue-400 font-semibold">[THESIS]</span><span> {data.thesis}</span></div>}{deb?.concerns?.length > 0 && <div><div className="text-muted-foreground mb-1">── Critic ──</div>{deb.concerns.map((c: any, i: number) => <div key={i}><span className="text-red-400 font-semibold">[CRITIC #{i + 1}]</span><span> {c.claim}</span>{c.evidence && <div className="text-muted-foreground ml-4">{c.evidence}</div>}</div>)}{deb.counter && deb.critic_stance !== "SUPPORT" && <div className="text-fuchsia-400 text-xs mt-1">{deb.critic_stance === "FLIP" ? `→ ${deb.counter_direction}` : "STAND_ASIDE"} · {deb.counter}</div>}</div>}{deb?.rebuttal && <div><div className="text-muted-foreground mb-1">── Reply ──</div><span className="text-blue-400 font-semibold">[REPLY]</span><span> {deb.rebuttal.rebuttal}</span><div className="text-xs ml-4 text-muted-foreground">score → {deb.rebuttal.revised_score}/100 · conceded: {deb.rebuttal.concede ? "yes" : "no"}</div></div>}{deb?.judge_summary && <div><div className="text-muted-foreground mb-1">── Judge ──</div><span className="text-emerald-400 font-semibold">[JUDGE]</span><span> {deb.judge_summary}</span><div className="text-xs ml-4 text-muted-foreground">{deb.flipped && <span className="text-fuchsia-400">reversed · </span>}adj prob {data.est_probability?.toFixed(2)} · score {data.adjusted_score}</div></div>}</div>)}</div>
      </div>
    </div>
  )
}
