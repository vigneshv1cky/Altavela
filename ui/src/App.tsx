import { useState, useRef, useEffect } from "react"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ArrowDown, ArrowUp, Loader2, RefreshCw } from "lucide-react"

interface Ev {
  type: string; msg?: string; market_id?: string; question?: string; direction?: string
  score?: number; adjusted_score?: number; approved?: boolean; flipped?: boolean
  edge_hint?: string; reason?: string; claim?: string; evidence?: string
  stance?: string; counter_direction?: string; counter?: string
  concede?: boolean; revised_score?: number; verdict?: string; summary?: string
  pick_id?: number; est_probability?: number; entry?: number; now?: number
}

interface Pick {
  id: number; question: string; direction: string; score: number
  entry_price: number | null; current_price: number | null; pnl_pct: number | null
  est_probability: number | null; exit_ts: string | null; exit_reason: string | null
  yes_price: number | null; no_price: number | null
}

interface Stats { total_picks: number; resolved: number; wins: number; win_rate: number | null }

interface TimelineRow {
  id: number; ts: string; question: string; direction: string; resolved: number
  outcome: number | null; adjusted_score: number; verdict: string; approved: number
}

interface TokenRow { role: string; model: string; input_tok: number; output_tok: number }

const TAGS: Record<string, [string, string]> = {
  status: ["STATUS", "text-zinc-500"], scout_pick: ["SCOUT", "text-yellow-400"],
  gate: ["GATE", "text-zinc-500"], evidence: ["EVID", "text-zinc-400"],
  thesis: ["THESIS", "text-blue-400"], concern: ["CRITIC", "text-red-400"],
  counter: ["CRITIC", "text-fuchsia-400"], rebuttal: ["REPLY", "text-blue-400"],
  decision: ["JUDGE", "text-emerald-400"], done: ["DONE", "text-zinc-500"],
  debate_start: ["DEBATE", "text-indigo-200"],
}

function TermLine({ ev }: { ev: Ev }) {
  const [tag, color] = TAGS[ev.type] ?? ["EVENT", "text-zinc-500"]
  let body = ""
  switch (ev.type) {
    case "status": body = ev.msg ?? ""; break
    case "scout_pick": body = `${ev.question?.slice(0, 80)} · ${ev.direction} · ${ev.edge_hint} — ${ev.reason ?? ""}`; break
    case "gate": body = `${ev.question?.slice(0, 60)} skipped: ${ev.reason ?? ""}`; break
    case "debate_start": body = `${ev.question?.slice(0, 60)} · ${ev.edge_hint ?? ""}`; break
    case "evidence": body = `${ev.msg ?? ""}`; break
    case "thesis": body = `${ev.direction} · prob ${(ev.est_probability ?? 0).toFixed(2)} · score ${ev.score}/100`; break
    case "concern": body = `${ev.claim ?? ""}`; break
    case "counter": body = ev.stance === "FLIP" ? `FLIP → ${ev.counter_direction}` : "STAND_ASIDE"; break
    case "rebuttal": body = `score → ${ev.revised_score}/100 (concede: ${ev.concede ? "yes" : "no"})`; break
    case "decision": body = `${ev.direction} · ${ev.approved ? "APPROVED" : "thin lean"} · ${ev.verdict} · ${ev.adjusted_score}/100${ev.flipped ? " · REVERSED" : ""}`; break
    case "done": body = `Run complete — ${ev.msg ?? ""}`; break
    default: body = JSON.stringify(ev)
  }
  return (
    <div className="text-[13px] leading-relaxed">
      <span className={`font-semibold ${color}`}>[{tag}]</span>{" "}
      <span className="text-zinc-300">{body}</span>
    </div>
  )
}

function LiveCard({ p }: { p: Pick }) {
  const isYes = p.direction === "BUY_YES"
  const pos = (p.pnl_pct ?? 0) >= 0
  return (
    <Card className="space-y-2 p-3">
      <div className="flex items-center gap-2">
        {isYes ? (
          <ArrowUp className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <ArrowDown className="h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />
        )}
        <Badge variant={isYes ? "default" : "destructive"} className="h-5 text-[10px]">
          {isYes ? "YES" : "NO"}
        </Badge>
        <span className="ml-auto font-mono text-sm font-semibold tabular-nums">
          {p.pnl_pct != null ? (
            <span className={pos ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
              {pos ? "+" : ""}{p.pnl_pct}%
            </span>
          ) : p.current_price != null ? (
            <span className="text-zinc-500">${p.current_price}</span>
          ) : (
            <span className="text-zinc-600">pending</span>
          )}
        </span>
      </div>
      <div className="text-sm text-zinc-800 dark:text-zinc-200 line-clamp-2">
        {p.question}
      </div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>entry {p.entry_price ?? "—"}{p.current_price != null && p.entry_price ? ` · now ${p.current_price}` : ""}</span>
        {p.est_probability != null && <span>target {p.est_probability.toFixed(2)}</span>}
        <span>conf {p.score}</span>
      </div>
    </Card>
  )
}

export default function App() {
  const [running, setRunning] = useState(false)
  const [feed, setFeed] = useState<Ev[]>([])
  const [stats, setStats] = useState<Stats>({ total_picks: 0, resolved: 0, wins: 0, win_rate: null })
  const [positions, setPositions] = useState<Pick[]>([])
  const [timeline, setTimeline] = useState<TimelineRow[]>([])
  const [tokens, setTokens] = useState<{ usage: TokenRow[]; total_tokens: number }>({ usage: [], total_tokens: 0 })
  const esRef = useRef<EventSource | null>(null)
  const [loaded, setLoaded] = useState(false)

  async function refresh() {
    const s = await fetch("/api/stats").then(r => r.json()); setStats(s)
    const p = await fetch("/api/picks").then(r => r.json()); setPositions(p)
    const t = await fetch("/api/timelines").then(r => r.json()); setTimeline(t)
    const tok = await fetch("/api/tokens").then(r => r.json()); setTokens(tok)
    setLoaded(true)
  }
  useEffect(() => { refresh(); const t = setInterval(refresh, 30000); return () => clearInterval(t) }, [])

  function run() {
    setRunning(true); setFeed([])
    const es = new EventSource("/api/find-markets"); esRef.current = es
    es.onmessage = (e) => {
      const ev: Ev = JSON.parse(e.data)
      if (ev.type === "done") { setRunning(false); es.close(); refresh() }
      else setFeed(f => [...f, ev])
    }
    es.onerror = () => { setRunning(false); es.close() }
  }

  const winRate = stats.win_rate

  return (
    <div className="mx-auto flex h-screen max-w-[1200px] flex-col p-4">
      {/* Header */}
      <header className="mb-3 flex shrink-0 items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-3.5 w-3.5 rotate-45 rounded-[3px] bg-indigo-500" />
          <div className="leading-none">
            <div className="text-base font-semibold tracking-tight text-zinc-900 dark:text-zinc-200">Altavela</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">prediction-market research desk</div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="hidden text-right sm:flex sm:gap-4 text-[11px] text-muted-foreground">
            <div>
              <div className="tracking-wider uppercase">{stats.total_picks}</div>
              <div className="text-[10px]">picks</div>
            </div>
            <div>
              <div className="tracking-wider uppercase">{stats.resolved}</div>
              <div className="text-[10px]">resolved</div>
            </div>
            <div>
              <div className={`tracking-wider uppercase ${winRate != null && winRate > 50 ? "text-emerald-400" : ""}`}>
                {winRate != null ? `${winRate}%` : "—"}
              </div>
              <div className="text-[10px]">win rate</div>
            </div>
          </div>
          <Button onClick={run} disabled={running} size="sm" className="gap-1.5">
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {running ? "Scanning…" : "Find Markets"}
          </Button>
        </div>
      </header>

      {/* Main grid */}
      <div className="flex min-h-0 flex-1 gap-4">
        {/* LEFT — terminal feed */}
        <div className="flex min-w-0 flex-1 flex-col">
          <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex items-center gap-2 border-b px-4 py-2 text-[11px] text-muted-foreground">
              <RefreshCw className="h-3 w-3" />
              <span>Live Feed{running ? " — scanning markets…" : ""}</span>
              {running && <Loader2 className="ml-auto h-3 w-3 animate-spin" />}
            </div>
            <div className="no-scrollbar flex-1 overflow-y-auto px-4 py-3">
              {(running || feed.length > 0 || positions.length > 0) ? (
                <div className="space-y-3">
                  {positions.length > 0 && (
                    <div>
                      <div className="mb-2 text-[13px] font-semibold text-indigo-400">Open Positions</div>
                      <div className="space-y-2">
                        {positions.map(p => <LiveCard key={p.id} p={p} />)}
                      </div>
                      <Separator className="my-2" />
                    </div>
                  )}
                  {feed.length > 0 && (() => {
                    const groups: { question: string; items: Ev[] }[] = []
                    let cur: Ev[] = []
                    for (const ev of feed) {
                      if (ev.type === "debate_start") {
                        if (cur.length > 0) groups.push({ question: groups.length > 0 ? groups[groups.length - 1].question : cur[0]?.question ?? "", items: cur })
                        cur = [ev]
                      } else { cur.push(ev) }
                    }
                    if (cur.length > 0) {
                      const ls = [...cur].reverse().find(e => e.type === "debate_start")
                      groups.push({ question: ls?.question ?? "", items: cur })
                    }
                    return (
                      <>
                        <div className="mb-2 text-[13px] font-semibold text-indigo-400">Live Feed</div>
                        {groups.map((g, gi) => (
                          <div key={gi}>
                            {gi > 0 && <Separator className="my-2" />}
                            {g.items[0]?.type === "debate_start" && <TermLine ev={g.items[0]} />}
                            <div className="ml-1.5 space-y-0.5 border-l border-zinc-200 pl-2.5 dark:border-zinc-800">
                              {g.items.slice(g.items[0]?.type === "debate_start" ? 1 : 0).map((ev, ei) => <TermLine key={ei} ev={ev} />)}
                            </div>
                          </div>
                        ))}
                      </>
                    )
                  })()}
                </div>
              ) : loaded ? (
                <div className="py-16 text-center text-sm text-muted-foreground">
                  <p>No picks yet</p>
                  <p className="mt-1 text-xs">Hit <span className="font-medium text-foreground">Find Markets</span> to run the desk</p>
                </div>
              ) : (
                <div className="py-16 text-center text-sm text-muted-foreground">Loading…</div>
              )}
            </div>
          </Card>
        </div>

        {/* RIGHT — stats panel */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <Tabs defaultValue="live" className="flex min-h-0 flex-1 flex-col">
            <TabsList className="h-9 bg-card p-1">
              <TabsTrigger value="live" className="px-3 text-sm data-active:bg-indigo-600 data-active:text-white">Live</TabsTrigger>
              <TabsTrigger value="track" className="px-3 text-sm data-active:bg-indigo-600 data-active:text-white">Track</TabsTrigger>
              <TabsTrigger value="tokens" className="px-3 text-sm data-active:bg-indigo-600 data-active:text-white">Tokens</TabsTrigger>
            </TabsList>

            <TabsContent value="live" className="mt-2 flex-1 overflow-y-auto">
              {positions.length === 0 ? (
                <Card className="p-8 text-center text-sm text-muted-foreground">No open positions</Card>
              ) : (
                <div className="space-y-2">
                  <div className="text-[11px] text-muted-foreground">{positions.length} open</div>
                  {positions.map(p => <LiveCard key={p.id} p={p} />)}
                </div>
              )}
            </TabsContent>

            <TabsContent value="track" className="mt-2 flex-1 overflow-y-auto">
              {timeline.length === 0 ? (
                <Card className="p-8 text-center text-sm text-muted-foreground">No track record yet</Card>
              ) : (
                <div className="space-y-2">
                  <div className="text-[11px] text-muted-foreground">
                    {timeline.filter(t => t.resolved).length} resolved · {timeline.length} total
                  </div>
                  {timeline.filter(t => t.resolved).map(t => (
                    <Card key={t.id} className="space-y-1.5 p-3">
                      <div className="flex items-center justify-between">
                        <Badge variant={t.direction === "BUY_YES" ? "default" : "destructive"} className="h-5 text-[10px]">
                          {t.direction === "BUY_YES" ? "YES" : "NO"}
                        </Badge>
                        <Badge variant={t.outcome === 1 ? "default" : "destructive"} className="h-5 text-[10px]">
                          {t.outcome === 1 ? "WIN" : "LOSS"}
                        </Badge>
                      </div>
                      <div className="text-sm text-zinc-800 dark:text-zinc-200 line-clamp-2">{t.question}</div>
                      <div className="text-[11px] text-muted-foreground">
                        conf {(t.adjusted_score ?? 0).toFixed(0)} · {t.verdict}{t.approved ? " · approved" : ""}
                      </div>
                    </Card>
                  ))}
                </div>
              )}
            </TabsContent>

            <TabsContent value="tokens" className="mt-2 flex-1 overflow-y-auto">
              {tokens.usage.length === 0 ? (
                <Card className="p-8 text-center text-sm text-muted-foreground">No usage yet</Card>
              ) : (
                <div className="space-y-2">
                  <div className="text-[11px] text-muted-foreground">
                    Total: {(tokens.total_tokens / 1000).toFixed(0)}k tokens
                  </div>
                  {tokens.usage.map((t, i) => (
                    <Card key={i} className="flex items-center justify-between px-3 py-2 text-sm">
                      <span className="font-medium">{t.role}</span>
                      <span className="text-muted-foreground">
                        {t.model} · {((t.input_tok + t.output_tok) / 1000).toFixed(0)}k
                      </span>
                    </Card>
                  ))}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  )
}
