"""Real-time price streaming via Polymarket CLOB WebSocket.

Free, no auth. Replaces 60s Gamma API polling with sub-second price updates.
Supports the watcher by maintaining a live price dict and calling a callback
on every price change for active positions.
"""

import json
import logging
import threading
import time

log = logging.getLogger("altavela.stream")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# In-memory price store: {market_id: (yes_px, no_px)}
_prices: dict[str, tuple[float, float]] = {}
_lock = threading.Lock()

# Per-market tracking: {clob_token_id: {"market_id": ..., "side": "yes"|"no"}}
_token_map: dict[str, dict] = {}
_token_lock = threading.Lock()

# Thread management
_stream_thread: threading.Thread | None = None
_stream_stop = threading.Event()


def start_stream(token_map: dict[str, dict]) -> None:
    """Launch WebSocket stream in a background thread.

    token_map: {clob_token_id: {"market_id": str, "side": "yes"|"no"}}
    """
    global _token_map, _stream_thread, _stream_stop

    # Stop old thread
    if _stream_thread and _stream_thread.is_alive():
        _stream_stop.set()
        _stream_thread.join(timeout=5)
    _stream_stop.clear()

    with _token_lock:
        _token_map = token_map

    _stream_thread = threading.Thread(target=_run_stream, daemon=True, name="altavela-ws")
    _stream_thread.start()
    log.info("WebSocket stream started with %d tokens", len(token_map))


def get_prices(market_ids: list[str]) -> dict[str, tuple[float, float]]:
    """Get current prices from the live stream. Same interface as live_prices()."""
    with _lock:
        return {mid: _prices[mid] for mid in market_ids if mid in _prices}


def set_price(market_id: str, yes_px: float, no_px: float) -> None:
    """Set initial prices for a market from REST before stream delivers."""
    with _lock:
        _prices[market_id] = (yes_px, no_px)


def _run_stream() -> None:
    while True:
        try:
            _connect()
        except Exception as exc:
            log.warning("WebSocket error: %s — reconnecting in 5s", exc)
            time.sleep(5)


def _handle_event(msg: dict, from_snapshot: bool = False) -> None:
    """Process a single WebSocket event — book snapshot, price_change, or last_trade.
    
    Book events from the initial subscription response (from_snapshot=True) are
    skipped because they show full book depth with extreme bid/ask edges, not
    the actual best prices. REST-seeded prices from _register_stream serve as
    the initial base; price_change events provide live updates."""
    event_type = msg.get("event_type", "")

    if event_type == "book":
        if not from_snapshot:
            _handle_book(msg)

    elif event_type == "price_change":
        for change in msg.get("price_changes", []):
            _handle_price_change_item(change)

    elif event_type == "last_trade_price":
        _handle_trade_price(msg)

    elif event_type == "market_resolved":
        _handle_resolved(msg)


def _handle_book(msg: dict) -> None:
    """Extract best bid/ask from a book snapshot."""
    asset_id = msg.get("asset_id", "")
    info = _token_map.get(asset_id)
    if not info:
        return
    bids = msg.get("bids", [])
    asks = msg.get("asks", [])
    best_bid = float(bids[0]["price"]) if bids else 0.0
    best_ask = float(asks[0]["price"]) if asks else 0.0
    _set_mid(info["market_id"], info["side"], best_bid, best_ask)


def _handle_price_change_item(change: dict) -> None:
    """Extract best bid/ask from a price_change item."""
    asset_id = change.get("asset_id", "")
    info = _token_map.get(asset_id)
    if not info:
        return
    best_bid = float(change.get("best_bid", "0"))
    best_ask = float(change.get("best_ask", "0"))
    _set_mid(info["market_id"], info["side"], best_bid, best_ask)


def _handle_trade_price(msg: dict) -> None:
    asset_id = msg.get("asset_id", "")
    info = _token_map.get(asset_id)
    if not info:
        return
    px = float(msg.get("price") or 0)
    if px <= 0:
        return
    _set_price_side(info["market_id"], info["side"], px)


def _handle_resolved(msg: dict) -> None:
    winning = msg.get("winning_asset_id", "")
    asset_ids = msg.get("assets_ids", [])
    with _lock:
        for tid in asset_ids:
            info = _token_map.get(tid)
            if not info:
                continue
            mid = info["market_id"]
            if mid not in _prices:
                _prices[mid] = (0.5, 0.5)
            prices = list(_prices[mid])
            val = 0.999 if tid == winning else 0.001
            if info["side"] == "yes":
                prices[0] = val
            else:
                prices[1] = val
            _prices[mid] = tuple(prices)


def _set_mid(market_id: str, side: str, best_bid: float, best_ask: float) -> None:
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0
    if mid <= 0:
        return
    _set_price_side(market_id, side, round(mid, 4))


def _set_price_side(market_id: str, side: str, px: float) -> None:
    with _lock:
        if market_id not in _prices:
            _prices[market_id] = (0.5, 0.5)
        prices = list(_prices[market_id])
        if side == "yes":
            prices[0] = round(px, 4)
        else:
            prices[1] = round(px, 4)
        _prices[market_id] = tuple(prices)


def _connect() -> None:
    try:
        import websocket  # pip install websocket-client
    except ImportError:
        log.error("websocket-client not installed — install with: pip install websocket-client")
        time.sleep(30)
        return  # _run_stream will catch and retry after sleep

    ws = websocket.WebSocket()
    ws.connect(WS_URL)

    # Subscribe to all tokens
    with _token_lock:
        token_ids = list(_token_map.keys())
    sub = json.dumps({
        "assets_ids": token_ids,
        "type": "market",
        "custom_feature_enabled": True,
    })
    ws.send(sub)
    log.info("WebSocket subscribed to %d tokens", len(token_ids))

    last_ping = time.time()

    while not _stream_stop.is_set():
        try:
            ws.settimeout(1.0)
            raw = ws.recv()
        except Exception:
            # Timeout — send ping
            if time.time() - last_ping > 10:
                try:
                    ws.send("PING")
                    last_ping = time.time()
                except Exception:
                    break
            continue

        if raw == "PONG":
            continue

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Initial subscription response is a list of book snapshots.
        # These show full depth with extreme edges — skip them so we don't
        # overwrite REST-seeded prices. price_change events provide live updates.
        if isinstance(msg, list):
            for item in msg:
                _handle_event(item, from_snapshot=True)
            continue

        if not isinstance(msg, dict):
            continue

        _handle_event(msg)
