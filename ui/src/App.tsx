import { useState, useEffect } from "react"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ArrowDown, ArrowUp, Loader2, Moon, Monitor, Sun, X } from "lucide-react"

interface Pick {
  id: number; question: string; direction: string; score: number
  entry_price: number | null; current_price: number | null; pnl_pct: number | null
}
interface Stats { total_picks: number; resolved: number; wins: number; win_rate: number | null }
interface TimelineRow { id: number; direction: string; question: string; resolved: number; outcome: number | null; adjusted_score: number; verdict: string; pnl_return_pct: number | null }
interface TokenRow { role: string; model: string; input_tok: number; output_tok: number }

function LiveCard({ p, onSelect }: { p: Pick; onSelect: (id: number) => void }) {
  const isYes = p.direction === "BUY_YES"
  const pos = (p.pnl_pct ?? 0) >= 0
  return (
    <Card className="space-y-2 p-3 cursor-pointer hover:border-indigo-400 transition-colors" onClick={() => onSelect(p.id)}>
      <div className="flex items-center gap-2">
        {isYes ? <ArrowUp className="h-4 w-4 shrink-0 text-emerald-500" /> : <ArrowDown className="h-4 w-4 shrink-0 text-red-500" />}
        <Badge variant={isYes ? "default" : "destructive"} className="h-5 text-[10px]">{isYes ? "YES" : "NO"}</Badge>
        <span className="ml-auto font-mono text-sm font-semibold tabular-nums">
          {p.pnl_pct != null ? <span className={pos ? "text-emerald-500" : "text-red-500"}>{pos ? "+" : ""}{p.pnl_pct}%</span>
            : p.current_price != null ? <span className="text-muted-foreground">${p.current_price}</span>
            : <span className="text-muted-foreground">pending</span>}
        </span>
      </div>
      <div className="text-sm line-clamp-2">{p.question}</div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>entry {p.entry_price ?? "—"}{p.current_price != null && p.entry_price ? ` · now ${p.current_price}` : ""}</span>
        <span>conf {p.score}</span>
      </div>
    </Card>
  )
}

export default function App() {
  const [running, setRunning] = useState(false)
  const [stats, setStats] = useState<Stats>({ total_picks: 0, resolved: 0, wins: 0, win_rate: null })
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

  const winRate = stats.win_rate

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="z-30 shrink-0 border-b bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1200px] items-center gap-4 px-5 py-3">
          <div className="flex items-center gap-2.5"><div className="h-3.5 w-3.5 rotate-45 rounded-[3px] bg-indigo-500" /><div className="leading-none"><div className="text-sm font-semibold tracking-tight">Altavela</div><div className="mt-0.5 text-[11px] text-muted-foreground">prediction-market research desk</div></div></div>
          <div className={`ml-2 flex items-center gap-1.5 text-xs font-medium ${running ? "text-emerald-500" : "text-muted-foreground"}`}><span className={`h-2 w-2 rounded-full ${running ? "animate-pulse bg-emerald-500" : "bg-muted-foreground/40"}`} />{running ? "running" : "idle"}</div>
          <div className="ml-auto flex items-center gap-4">
            <div className="hidden text-right sm:flex sm:gap-4 text-xs text-muted-foreground">
              <div><div className="font-mono font-semibold">{stats.total_picks}</div><div className="text-[10px] uppercase">picks</div></div>
              <div><div className="font-mono font-semibold">{stats.resolved}</div><div className="text-[10px] uppercase">resolved</div></div>
              <div><div className={`font-mono font-semibold ${winRate != null && winRate > 50 ? "text-emerald-500" : ""}`}>{winRate != null ? `${winRate}%` : "—"}</div><div className="text-[10px] uppercase">win rate</div></div>
            </div>
            <Button onClick={run} disabled={running} size="sm" className="gap-1.5">{running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}{running ? "Scanning…" : "Find Markets"}</Button>
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
              <TabsContent value="live">{positions.length === 0 ? <Card className="p-8 text-center text-sm text-muted-foreground">No open positions</Card> : <div className="space-y-2"><div className="text-[11px] text-muted-foreground">{positions.length} open</div>{positions.map(p => <LiveCard key={p.id} p={p} onSelect={setSelected} />)}</div>}</TabsContent>
              <TabsContent value="track">{timeline.length === 0 ? <Card className="p-8 text-center text-sm text-muted-foreground">No picks yet</Card> : <div className="space-y-2"><div className="text-[11px] text-muted-foreground">{timeline.filter(t => t.resolved).length} resolved · {timeline.length} total</div>{timeline.map(t => <Card key={t.id} className="space-y-1.5 p-3 cursor-pointer hover:border-indigo-400 transition-colors" onClick={() => setSelected(t.id)}><div className="flex items-center justify-between"><Badge variant={t.direction === "BUY_YES" ? "default" : "destructive"} className="h-5 text-[10px]">{t.direction === "BUY_YES" ? "YES" : "NO"}</Badge>{t.resolved ? <Badge variant={t.outcome === 1 ? "default" : "destructive"} className="h-5 text-[10px]">{t.outcome === 1 ? "WIN" : "LOSS"}</Badge> : <span className="text-[10px] text-muted-foreground">open</span>}</div><div className="text-sm line-clamp-2">{t.question}</div><div className="text-[11px] text-muted-foreground flex justify-between"><span>conf {(t.adjusted_score ?? 0).toFixed(0)} · {t.verdict}</span>{t.pnl_return_pct != null && <span className={t.pnl_return_pct >= 0 ? "text-emerald-500" : "text-red-500"}>{t.pnl_return_pct >= 0 ? "+" : ""}{t.pnl_return_pct}%</span>}</div></Card>)}</div>}</TabsContent>
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
      <div className="relative z-10 flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-lg border bg-background shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b px-4 py-3 text-sm"><span className={dirColor}>{isYes ? "YES" : "NO"}</span><span className="font-bold"> {data?.question}</span><button onClick={onClose} className="grid h-7 w-7 place-items-center rounded text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button></div>
        <div className="no-scrollbar flex-1 overflow-y-auto p-4 text-sm leading-relaxed">{!data ? <div className="text-muted-foreground">Loading…</div> : (<div className="space-y-3"><div><div className="text-muted-foreground mb-1">── The Call ──</div><span className={dirColor}>[{isYes ? "BUY_YES" : "BUY_NO"}]</span><span className="text-muted-foreground"> · prob {data.est_probability?.toFixed(2)} · conf {Math.round(data.adjusted_score ?? data.score)}</span>{data.verdict && <span className="text-muted-foreground"> · {data.verdict}</span>}<div className="text-muted-foreground">entry: YES ${data.market_yes_price} · NO ${data.market_no_price}{data.resolved && <span className={data.outcome === 1 ? "text-emerald-400" : "text-red-400"}> · {data.outcome === 1 ? "WIN" : "LOSS"}</span>}</div></div>{data.triage_reason && <div><div className="text-muted-foreground mb-1">── Why we looked ──</div><span className="text-yellow-400 font-semibold">[SCOUT]</span><span> {data.triage_reason}</span></div>}{data.thesis && <div><div className="text-muted-foreground mb-1">── Researcher ──</div><span className="text-blue-400 font-semibold">[THESIS]</span><span> {data.thesis}</span></div>}{deb?.concerns?.length > 0 && <div><div className="text-muted-foreground mb-1">── Critic ──</div>{deb.concerns.map((c: any, i: number) => <div key={i}><span className="text-red-400 font-semibold">[CRITIC #{i + 1}]</span><span> {c.claim}</span>{c.evidence && <div className="text-muted-foreground ml-4">{c.evidence}</div>}</div>)}{deb.counter && deb.critic_stance !== "SUPPORT" && <div className="text-fuchsia-400 text-xs mt-1">{deb.critic_stance === "FLIP" ? `→ ${deb.counter_direction}` : "STAND_ASIDE"} · {deb.counter}</div>}</div>}{deb?.rebuttal && <div><div className="text-muted-foreground mb-1">── Reply ──</div><span className="text-blue-400 font-semibold">[REPLY]</span><span> {deb.rebuttal.rebuttal}</span><div className="text-xs ml-4 text-muted-foreground">score → {deb.rebuttal.revised_score}/100 · conceded: {deb.rebuttal.concede ? "yes" : "no"}</div></div>}{deb?.judge_summary && <div><div className="text-muted-foreground mb-1">── Judge ──</div><span className="text-emerald-400 font-semibold">[JUDGE]</span><span> {deb.judge_summary}</span><div className="text-xs ml-4 text-muted-foreground">{deb.flipped && <span className="text-fuchsia-400">reversed · </span>}adj prob {data.est_probability?.toFixed(2)} · score {data.adjusted_score}</div></div>}</div>)}</div>
      </div>
    </div>
  )
}
