import { useState, useRef, useEffect } from "react"

interface Ev {
  type: string
  msg?: string
  market_id?: string
  question?: string
  direction?: string
  score?: number
  adjusted_score?: number
  approved?: boolean
  flipped?: boolean
  edge_hint?: string
  reason?: string
  claim?: string
  evidence?: string
  stance?: string
  counter_direction?: string
  counter?: string
  concede?: boolean
  revised_score?: number
  rebuttal?: string
  verdict?: string
  summary?: string
  pick_id?: number
  est_probability?: number
}

interface Pick {
  id: number
  question: string
  direction: string
  score: number
  entry_price: number | null
  current_price: number | null
  pnl_pct: number | null
}

interface Stats { total_picks: number; resolved: number; wins: number; win_rate: number | null }

const TAGS: Record<string, [string, string]> = {
  status:       ["STATUS", "text-zinc-500"],
  scout_pick:   ["SCOUT",  "text-yellow-400"],
  gate:         ["GATE",   "text-zinc-500"],
  evidence:     ["EVID",   "text-zinc-400"],
  thesis:       ["THESIS", "text-blue-400"],
  concern:      ["CRITIC", "text-red-400"],
  counter:      ["CRITIC", "text-fuchsia-400"],
  rebuttal:     ["REPLY",  "text-blue-400"],
  decision:     ["JUDGE",  "text-emerald-400"],
  done:         ["DONE",   "text-zinc-500"],
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

export default function App() {
  const [running, setRunning] = useState(false)
  const [feed, setFeed] = useState<Ev[]>([])
  const [stats, setStats] = useState<Stats>({ total_picks: 0, resolved: 0, wins: 0, win_rate: null })
  const [positions, setPositions] = useState<Pick[]>([])
  const esRef = useRef<EventSource | null>(null)
  const termRef = useRef<HTMLDivElement | null>(null)

  async function refresh() {
    const s = await fetch("/api/stats").then(r => r.json())
    setStats(s)
    const p = await fetch("/api/picks").then(r => r.json())
    setPositions(p)
  }

  useEffect(() => { refresh(); const t = setInterval(refresh, 30000); return () => clearInterval(t) }, [])

  function run() {
    setRunning(true)
    setFeed([])
    const es = new EventSource("/api/find-markets")
    esRef.current = es
    es.onmessage = (e) => {
      const ev: Ev = JSON.parse(e.data)
      if (ev.type === "done") { setRunning(false); es.close(); refresh() }
      else { setFeed(f => [...f, ev]) }
    }
    es.onerror = () => { setRunning(false); es.close() }
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-base font-bold text-zinc-200">Altavela</h1>
          <p className="text-[11px] text-zinc-500">prediction-market research desk</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right text-[11px] text-zinc-500">
            <div>{stats.total_picks} picks · {stats.resolved} resolved</div>
            <div className={stats.win_rate != null && stats.win_rate > 50 ? "text-emerald-400" : "text-zinc-500"}>
              {stats.win_rate != null ? `${stats.win_rate}% win` : "—"}
            </div>
          </div>
          <button
            onClick={run}
            disabled={running}
            className="rounded bg-indigo-600 px-3 py-1.5 text-[13px] font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {running ? "Scanning…" : "Find Markets"}
          </button>
        </div>
      </div>

      {(running || feed.length > 0 || positions.length > 0) && (
        <div className="overflow-hidden rounded-lg border border-zinc-700 bg-zinc-950">
          <div ref={termRef} className="no-scrollbar max-h-[600px] overflow-y-auto px-4 py-3 space-y-3">

            {/* Position list */}
            {positions.length > 0 && (
              <div>
                <div className="text-zinc-500 mb-1 text-[13px]">── Open Positions ──</div>
                {positions.map(p => (
                  <div key={p.id} className="text-[13px] leading-relaxed">
                    <span className={p.direction === "BUY_YES" ? "text-emerald-400" : "text-red-400"}>
                      [{p.direction}]
                    </span>{" "}
                    <span className="text-zinc-200">{p.question?.slice(0, 70)}</span>
                    <span className="text-zinc-600"> · entry {p.entry_price}</span>
                    {p.current_price != null && (
                      <span className="text-zinc-600"> · now {p.current_price}</span>
                    )}
                    {p.pnl_pct != null && (
                      <span className={p.pnl_pct >= 0 ? "text-emerald-400 ml-1" : "text-red-400 ml-1"}>
                        {p.pnl_pct >= 0 ? "+" : ""}{p.pnl_pct}%
                      </span>
                    )}
                  </div>
                ))}
                <div className="border-t border-zinc-800 my-1" />
              </div>
            )}

            {/* Debate feed */}
            {feed.length > 0 && (() => {
              const groups: { start?: Ev; items: Ev[] }[] = []
              let cur: Ev[] = []
              for (const ev of feed) {
                if (ev.type === "debate_start") {
                  if (cur.length > 0) groups.push({ items: cur })
                  cur = [ev]
                } else {
                  cur.push(ev)
                }
              }
              if (cur.length > 0) groups.push({ items: cur })

              return (
                <>
                  <div className="text-zinc-500 mb-1 text-[13px]">── Live Feed ──</div>
                  {groups.map((g, gi) => (
                    <div key={gi}>
                      {g.items[0]?.type === "debate_start" && (
                        <TermLine ev={g.items[0]} />
                      )}
                      {g.items.slice(g.items[0]?.type === "debate_start" ? 1 : 0).map((ev, ei) => (
                        <div key={ei} className="border-l border-zinc-800 ml-1.5 pl-2.5">
                          <TermLine ev={ev} />
                        </div>
                      ))}
                      {gi < groups.length - 1 && <div className="border-t border-zinc-800 my-1" />}
                    </div>
                  ))}
                </>
              )
            })()}

            {!running && feed.length === 0 && positions.length === 0 && (
              <div className="text-zinc-500 text-[13px]">No active positions. Click Find Markets to scan for opportunities.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
