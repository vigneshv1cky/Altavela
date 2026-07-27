import { useState, useRef, useEffect } from "react"

interface Ev {
  type: string
  msg?: string; market_id?: string; question?: string; direction?: string
  score?: number; adjusted_score?: number; approved?: boolean; flipped?: boolean
  edge_hint?: string; reason?: string; claim?: string; evidence?: string
  stance?: string; counter_direction?: string; counter?: string
  concede?: boolean; revised_score?: number; verdict?: string; summary?: string
  pick_id?: number; est_probability?: number; entry?: number; now?: number
}

interface Pick { id: number; question: string; direction: string; score: number; entry_price: number | null; current_price: number | null; pnl_pct: number | null }
interface Stats { total_picks: number; resolved: number; wins: number; win_rate: number | null }
interface TimelineRow { id: number; ts: string; question: string; direction: string; resolved: number; outcome: number | null; adjusted_score: number; verdict: string; approved: number }
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
    case "scout_pick": body = `${ev.question?.slice(0, 80)} \u00b7 ${ev.direction} \u00b7 ${ev.edge_hint} \u2014 ${ev.reason ?? ""}`; break
    case "gate": body = `${ev.question?.slice(0, 60)} skipped: ${ev.reason ?? ""}`; break
    case "debate_start": body = `${ev.question?.slice(0, 60)} \u00b7 ${ev.edge_hint ?? ""}`; break
    case "evidence": body = `${ev.msg ?? ""}`; break
    case "thesis": body = `${ev.direction} \u00b7 prob ${(ev.est_probability ?? 0).toFixed(2)} \u00b7 score ${ev.score}/100`; break
    case "concern": body = `${ev.claim ?? ""}`; break
    case "counter": body = ev.stance === "FLIP" ? `FLIP \u2192 ${ev.counter_direction}` : "STAND_ASIDE"; break
    case "rebuttal": body = `score \u2192 ${ev.revised_score}/100 (concede: ${ev.concede ? "yes" : "no"})`; break
    case "decision": body = `${ev.direction} \u00b7 ${ev.approved ? "APPROVED" : "thin lean"} \u00b7 ${ev.verdict} \u00b7 ${ev.adjusted_score}/100${ev.flipped ? " \u00b7 REVERSED" : ""}`; break
    case "done": body = `Run complete \u2014 ${ev.msg ?? ""}`; break
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
  const [timeline, setTimeline] = useState<TimelineRow[]>([])
  const [tokens, setTokens] = useState<{ usage: TokenRow[]; total_tokens: number }>({ usage: [], total_tokens: 0 })
  const [rightTab, setRightTab] = useState<"positions" | "track" | "tokens">("positions")
  const esRef = useRef<EventSource | null>(null)
  const termRef = useRef<HTMLDivElement | null>(null)

  async function refresh() {
    const s = await fetch("/api/stats").then(r => r.json()); setStats(s)
    const p = await fetch("/api/picks").then(r => r.json()); setPositions(p)
    const t = await fetch("/api/timelines").then(r => r.json()); setTimeline(t)
    const tok = await fetch("/api/tokens").then(r => r.json()); setTokens(tok)
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
    <div className="mx-auto max-w-[1200px] p-4 h-screen flex flex-col">
      <div className="flex items-center justify-between mb-3 shrink-0">
        <div>
          <h1 className="text-base font-bold text-zinc-200">Altavela</h1>
          <p className="text-[11px] text-zinc-500">prediction-market research desk</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right text-[11px] text-zinc-500 leading-tight">
            <div>{stats.total_picks} picks · {stats.resolved} resolved</div>
            <div className={winRate != null && winRate > 50 ? "text-emerald-400" : "text-zinc-500"}>
              {winRate != null ? `${winRate}% win` : "\u2014"}
            </div>
          </div>
          <button onClick={run} disabled={running}
            className="rounded bg-indigo-600 px-3 py-1.5 text-[13px] font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
            {running ? "Scanning\u2026" : "Find Markets"}
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 flex gap-4">
        {/* LEFT — terminal */}
        <div className="flex-1 min-w-0 flex flex-col">
          {(running || feed.length > 0 || positions.length > 0) && (
            <div className="flex-1 min-h-0 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-950">
              <div ref={termRef} className="no-scrollbar h-full overflow-y-auto px-4 py-3 space-y-3">
                {positions.length > 0 && (
                  <div>
                    <div className="text-zinc-500 mb-1 text-[13px]">\u2500\u2500 Open Positions \u2500\u2500</div>
                    {positions.map(p => (
                      <div key={p.id} className="text-[13px] leading-relaxed">
                        <span className={p.direction === "BUY_YES" ? "text-emerald-400" : "text-red-400"}>[{p.direction}]</span>{" "}
                        <span className="text-zinc-200">{p.question?.slice(0, 60)}</span>
                        <span className="text-zinc-600"> · entry {p.entry_price}</span>
                        {p.current_price != null && <span className="text-zinc-600"> · now {p.current_price}</span>}
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
                {feed.length > 0 && (() => {
                  const groups: { question: string; items: Ev[] }[] = []
                  let cur: Ev[] = []
                  for (const ev of feed) {
                    if (ev.type === "debate_start") {
                      if (cur.length > 0) groups.push({ question: groups.length > 0 ? groups[groups.length-1].question : cur[0]?.question ?? "", items: cur })
                      cur = [ev]
                    } else { cur.push(ev) }
                  }
                  if (cur.length > 0) {
                    const ls = [...cur].reverse().find(e => e.type === "debate_start")
                    groups.push({ question: ls?.question ?? "", items: cur })
                  }
                  return (
                    <>
                      <div className="text-zinc-500 mb-1 text-[13px]">\u2500\u2500 Live Feed \u2500\u2500</div>
                      {groups.map((g, gi) => (
                        <div key={gi}>
                          {gi > 0 && <div className="border-t border-zinc-800 my-2" />}
                          {g.items[0]?.type === "debate_start" && <TermLine ev={g.items[0]} />}
                          <div className="border-l border-zinc-800 ml-1.5 pl-2.5 space-y-0.5">
                            {g.items.slice(g.items[0]?.type === "debate_start" ? 1 : 0).map((ev, ei) => <TermLine key={ei} ev={ev} />)}
                          </div>
                        </div>
                      ))}
                    </>
                  )
                })()}
              </div>
            </div>
          )}
        </div>

        {/* RIGHT — stats panel */}
        <div className="w-72 shrink-0 flex flex-col gap-3 min-h-0">
          <div className="flex gap-1">
            {(["positions","track","tokens"] as const).map(t => (
              <button key={t} onClick={() => setRightTab(t)}
                className={`flex-1 rounded px-2 py-1 text-[11px] font-medium ${
                  rightTab === t ? "bg-zinc-800 text-zinc-200" : "text-zinc-500 hover:text-zinc-300"}`}>
                {t === "positions" ? "Live" : t === "track" ? "Track" : "Tokens"}
              </button>
            ))}
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar rounded-lg border border-zinc-800 bg-zinc-950 p-3">
            {rightTab === "positions" && (
              <div className="space-y-2">
                <div className="text-[11px] text-zinc-500">Open Positions</div>
                {positions.length === 0 ? (
                  <div className="text-[12px] text-zinc-600">None</div>
                ) : positions.map(p => (
                  <div key={p.id} className="border-b border-zinc-800 pb-1.5 text-[12px]">
                    <div className="flex justify-between">
                      <span className={p.direction === "BUY_YES" ? "text-emerald-400" : "text-red-400"}>
                        {p.direction === "BUY_YES" ? "YES" : "NO"}
                      </span>
                      {p.pnl_pct != null && (
                        <span className={p.pnl_pct >= 0 ? "text-emerald-400" : "text-red-400"}>
                          {p.pnl_pct >= 0 ? "+" : ""}{p.pnl_pct}%
                        </span>
                      )}
                    </div>
                    <div className="text-zinc-300 truncate">{p.question?.slice(0, 50)}</div>
                    <div className="text-zinc-600">entry {p.entry_price}{p.current_price != null ? ` \u00b7 now ${p.current_price}` : ""}</div>
                  </div>
                ))}
              </div>
            )}

            {rightTab === "track" && (
              <div className="space-y-2">
                <div className="text-[11px] text-zinc-500">Track Record</div>
                {timeline.length === 0 ? (
                  <div className="text-[12px] text-zinc-600">No picks yet</div>
                ) : timeline.filter(t => t.resolved).length === 0 ? (
                  <div className="text-[12px] text-zinc-600">No resolved picks</div>
                ) : timeline.filter(t => t.resolved).map(t => (
                  <div key={t.id} className="border-b border-zinc-800 pb-1.5 text-[12px]">
                    <div className="flex justify-between">
                      <span className={t.direction === "BUY_YES" ? "text-emerald-400" : "text-red-400"}>
                        {t.direction}
                      </span>
                      <span className={t.outcome === 1 ? "text-emerald-400" : "text-red-400"}>
                        {t.outcome === 1 ? "WIN" : "LOSS"}
                      </span>
                    </div>
                    <div className="text-zinc-300 truncate">{t.question?.slice(0, 50)}</div>
                    <div className="text-zinc-600">conf {(t.adjusted_score ?? 0).toFixed(0)} · {t.verdict}</div>
                  </div>
                ))}
              </div>
            )}

            {rightTab === "tokens" && (
              <div className="space-y-2">
                <div className="text-[11px] text-zinc-500">Token Usage</div>
                {tokens.usage.length === 0 ? (
                  <div className="text-[12px] text-zinc-600">No usage yet</div>
                ) : (
                  <>
                    <div className="text-[12px] text-zinc-400 mb-1">
                      Total: {(tokens.total_tokens / 1000).toFixed(0)}k tokens
                    </div>
                    {tokens.usage.map((t, i) => (
                      <div key={i} className="text-[12px] text-zinc-500 flex justify-between">
                        <span>{t.role}</span>
                        <span>{((t.input_tok + t.output_tok) / 1000).toFixed(0)}k</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
