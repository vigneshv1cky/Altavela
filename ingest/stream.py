"""Real-time price streaming via Polymarket CLOB WebSocket.

Free, no auth. Replaces 60s Gamma API polling with sub-second price updates.
Supports the watcher by maintaining a live price dict and calling a callback
on every price change for active positions.
"""

import asyncio
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


def _run_stream() -> None:
    while True:
        try:
            _connect()
        except Exception as exc:
            log.warning("WebSocket error: %s — reconnecting in 5s", exc)
            time.sleep(5)


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

        # Subscription confirmation comes as a list
        if isinstance(msg, list):
            continue

        if not isinstance(msg, dict):
            continue

        event_type = msg.get("event_type", "")

        # Best bid/ask — gives us real-time mid-market prices
        if event_type == "best_bid_ask":
            asset_id = msg.get("asset_id", "")
            info = _token_map.get(asset_id)
            if not info:
                continue
            bid = float(msg.get("best_bid", "0"))
            ask = float(msg.get("best_ask", "0"))
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0

            mid_id = info["market_id"]
            side = info["side"]
            with _lock:
                if mid_id not in _prices:
                    _prices[mid_id] = [0.5, 0.5]
                prices = list(_prices[mid_id])
                if side == "yes":
                    prices[0] = round(mid, 4)
                else:
                    prices[1] = round(mid, 4)
                _prices[mid_id] = tuple(prices)

        # Last trade price — more reliable than best_bid_ask for illiquid markets
        elif event_type == "last_trade_price":
            asset_id = msg.get("asset_id", "")
            info = _token_map.get(asset_id)
            if not info:
                continue
            px = float(msg.get("price", "0"))
            if px <= 0:
                continue

            mid_id = info["market_id"]
            side = info["side"]
            with _lock:
                if mid_id not in _prices:
                    _prices[mid_id] = [0.5, 0.5]
                prices = list(_prices[mid_id])
                if side == "yes":
                    prices[0] = round(px, 4)
                else:
                    prices[1] = round(px, 4)
                _prices[mid_id] = tuple(prices)

        # Market resolved
        elif event_type == "market_resolved":
            winning = msg.get("winning_asset_id", "")
            # Update all tokens for this resolved market
            asset_ids = msg.get("assets_ids", [])
            mid_id = msg.get("id", "")
            with _lock:
                for tid in asset_ids:
                    info = _token_map.get(tid)
                    if not info:
                        continue
                    mid = info["market_id"]
                    if mid not in _prices:
                        _prices[mid] = [0.5, 0.5]
                    prices = list(_prices[mid])
                    if info["side"] == "yes":
                        prices[0] = 0.999 if tid == winning else 0.001
                    else:
                        prices[1] = 0.999 if tid == winning else 0.001
                    _prices[mid] = tuple(prices)
