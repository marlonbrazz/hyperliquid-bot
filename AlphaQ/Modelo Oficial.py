# ======================================
# ASSET'S CHANNEL MODEL — STATE MACHINE 
# ======================================

import time
import os
import json
import logging
from datetime import datetime
import requests
import MetaTrader5 as mt5
from dotenv import load_dotenv

# =====================================================
# ENV / CONFIG
# =====================================================
load_dotenv()

API_KEY = os.getenv("API_KEY")
MT5_LOGIN = int(os.getenv("MT5_LOGIN"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")

TIMEFRAME_API = "15m"
API_CHECK_MINUTES = 15

VOLUME = 0.01
SLIPPAGE = 30
MAGIC_NUMBER = 12345671
ORDER_COMMENT = "CHANNEL_MODEL_SM_V1"

CHECK_INTERVAL = 1

BASE_LOW = 1906.88
CHANNEL_SIZE = 5.61

STATE_FILE = "state.json"

# =====================================================
# TICKER'S
# =====================================================
tick_demo = "XAUUSD"
tick_api = "XAUUSD"

# =====================================================
# LOGGING
# =====================================================
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename=f"logs/xau_state_machine_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
)

def log(msg):
    print(msg)
    logging.info(msg)

# =====================================================
# STATE DEFINITIONS
# =====================================================
STATE_IDLE = "IDLE"
STATE_REGIME_A = "REGIME_A"
STATE_REGIME_B = "REGIME_B"
STATE_POST_STOP = "POST_STOP"

# =====================================================
# CHANNEL FUNCTIONS
# =====================================================
def get_channel_index(price):
    if price >= BASE_LOW:
        return int((price - BASE_LOW) // CHANNEL_SIZE)
    return int((price - BASE_LOW) // CHANNEL_SIZE) - 1

def get_channel_bounds(channel):
    low = BASE_LOW + channel * CHANNEL_SIZE
    high = low + CHANNEL_SIZE
    return low, high

# =====================================================
# API
# =====================================================
def get_api_signal(symbol, timeframe):
    payload = {"model": "forex", "ticker": symbol, "timeframe": timeframe}
    headers = {"Authorization": f"Api-Key {API_KEY}"}

    try:
        r = requests.post(
            "https://om-qs.com/api/v1/models/",
            headers=headers,
            json=payload,
            timeout=10
        )

        if r.status_code != 200:
            log(f"[API] HTTP {r.status_code}")
            return None

        signal = r.json().get("data", {}).get("signal")
        return signal if signal in (0, 0.5, 1) else None

    except Exception as e:
        log(f"[API ERROR] {e}")
        return None

def interpret_signal(signal):
    return "BUY" if signal == 1 else "SELL" if signal == 0 else "NEUTRAL" if signal == 0.5 else None

# =====================================================
# MT5
# =====================================================
if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
    raise RuntimeError("MT5 init failed")

mt5.symbol_select(tick_demo, True)

def send_order(order_type):
    tick = mt5.symbol_info_tick(tick_demo)
    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": tick_demo,
        "volume": VOLUME,
        "type": order_type,
        "price": price,
        "deviation": SLIPPAGE,
        "magic": MAGIC_NUMBER,
        "comment": ORDER_COMMENT
    }

    result = mt5.order_send(req)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"[ORDER ERROR] {result}")
        return None
    return result

def close_position(pos):
    tick = mt5.symbol_info_tick(pos.symbol)
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": close_type,
        "position": pos.ticket,
        "price": price,
        "deviation": SLIPPAGE,
        "magic": MAGIC_NUMBER,
        "comment": "CLOSE"
    }

    return mt5.order_send(req)

def market_is_open(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        return False
    return info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL
# =====================================================
# TIME CONTROL
# =====================================================
last_api_check = None
force_api_check = True

def get_current_slot(now=None):
    now = now or datetime.now()
    minute = (now.minute // API_CHECK_MINUTES) * API_CHECK_MINUTES
    return now.replace(minute=minute, second=0, microsecond=0)

def should_check_api():
    global last_api_check, force_api_check
    now = datetime.now()
    slot = get_current_slot(now)

    if force_api_check:
        force_api_check = False
        last_api_check = slot
        return True

    if last_api_check is None or slot > last_api_check:
        last_api_check = slot
        return True

    return False

# =====================================================
# STATE PERSISTENCE
# =====================================================
def save_state():
    data = {
        "state": state,
        "last_signal": last_signal,
        "entry_channel": entry_channel,
        "trailing_channel": trailing_channel,
        "max_channel_reached": max_channel_reached,
        "min_channel_reached": min_channel_reached,
        "c2_triggered": c2_triggered
    }
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    global state, last_signal, entry_channel, trailing_channel, max_channel_reached, min_channel_reached, c2_triggered
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            state = data.get("state", STATE_IDLE)
            last_signal = data.get("last_signal")
            entry_channel = data.get("entry_channel")
            trailing_channel = data.get("trailing_channel")
            max_channel_reached = data.get("max_channel_reached")
            min_channel_reached = data.get("min_channel_reached")
            c2_triggered = data.get("c2_triggered", False)

            log(f"[RECOVERY] Estado restaurado: {state}")
    except FileNotFoundError:
        log("[RECOVERY] Nenhum estado salvo")

def recover_from_mt5():
    global state, last_signal, entry_channel, trailing_channel, max_channel_reached, c2_triggered
    positions = mt5.positions_get(symbol=tick_demo)
    if not positions:
        return
    pos = positions[0]
    tick = mt5.symbol_info_tick(tick_demo)
    price = tick.ask if pos.type == mt5.POSITION_TYPE_BUY else tick.bid

    last_signal = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
    entry_channel = get_channel_index(price)
    max_channel_reached = entry_channel
    min_channel_reached = entry_channel
    c2_triggered = False
    trailing_channel = None
    state = STATE_REGIME_A

    log(f"[RECOVERY] Posição detectada → {last_signal} | canal {entry_channel}")

# =====================================================
# INIT
# =====================================================
state = STATE_IDLE
last_signal = None
entry_channel = None
trailing_channel = None
max_channel_reached = None
min_channel_reached = None
c2_triggered = False

load_state()
if state == STATE_IDLE:
    recover_from_mt5()

log("[SYSTEM] STARTED — STATE MACHINE ACTIVE")

# =====================================================
# MAIN LOOP
# =====================================================
while True:

    positions = mt5.positions_get(symbol=tick_demo)

    # =================================================
    # CONSISTENCY GUARD — MT5 É A FONTE DA VERDADE
    # =================================================

    has_position = positions is not None and len(positions) > 0

    # Estado exige posição, mas MT5 não tem → RESET TOTAL
    if state in (STATE_REGIME_A, STATE_REGIME_B) and not has_position:
        log("[CONSISTENCY] Estado ativo sem posição no MT5 → reset para IDLE")
        state = STATE_IDLE
        last_signal = None
        entry_channel = None
        trailing_channel = None
        max_channel_reached = None
        min_channel_reached = None
        c2_triggered = False
        force_api_check = True

    # POST_STOP não deve coexistir com posição aberta
    if state == STATE_POST_STOP and has_position:
        log("[CONSISTENCY] POST_STOP com posição aberta → forçando REGIME_A")
        
        pos = positions[0]
        tick = mt5.symbol_info_tick(tick_demo)
        price = tick.ask if pos.type == mt5.POSITION_TYPE_BUY else tick.bid

        last_signal = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
        entry_channel = get_channel_index(price)
        trailing_channel = None
        max_channel_reached = entry_channel
        min_channel_reached = entry_channel
        c2_triggered = False
        state = STATE_REGIME_A

        save_state()
        time.sleep(CHECK_INTERVAL)
        continue
        

    # =================================================
    # IDLE STATE
    # =================================================

    if state == STATE_IDLE:
        if should_check_api():
            raw = get_api_signal(tick_api, TIMEFRAME_API)
            if raw is None:
                log("[API] Sem resposta válida - mantendo estado atual")
                continue

            signal = interpret_signal(raw)
            
            if signal is None:
                log("[IDLE] Sem atualização de sinal (API indisponível)")

            elif signal in ("BUY", "SELL"):

                if not market_is_open(tick_demo):
                    log("[IDLE] Mercado fechado — aguardando abertura")
                    time.sleep(5)
                    continue

                result = send_order(mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL)

                if result is None:
                    log("[IDLE] Ordem rejeitada — mantendo IDLE")
                    force_api_check = False
                    time.sleep(5)
                    continue

                last_signal = signal
                state = STATE_REGIME_A
                entry_channel = None
                trailing_channel = None
                max_channel_reached = None
                min_channel_reached = None
                c2_triggered = False
                save_state()
                log(f"[STATE] IDLE → REGIME_A ({signal})")

    # =================================================
    # REGIME A
    # =================================================

    elif state == STATE_REGIME_A and positions:
        pos = positions[0]
        tick = mt5.symbol_info_tick(tick_demo)
        price = tick.ask if pos.type == mt5.POSITION_TYPE_BUY else tick.bid
        current_channel = get_channel_index(price)

        # =================================
        # ATUALIZA OS EXTREMOS ATINGINDOS
        # =================================
        if pos.type == mt5.POSITION_TYPE_BUY:
            if max_channel_reached is None:
                max_channel_reached = current_channel
            else:
                max_channel_reached = max(max_channel_reached, current_channel)
        else:
            if min_channel_reached is None:
                min_channel_reached = current_channel
            else:
                min_channel_reached = min(min_channel_reached, current_channel)

        # ================================
        # CANAL DE ENTRADA
        # ================================
        if entry_channel is None:
            entry_channel = current_channel
            save_state()

        # ================================
        # LÓGICA DA API
        # ================================
        if should_check_api():
            raw = get_api_signal(tick_api, TIMEFRAME_API)
            signal = interpret_signal(raw)

            if signal is None:
                continue

            # 1️⃣ NEUTRO → fecha e volta para IDLE
            if signal == "NEUTRAL":
                close_position(pos)
                state = STATE_IDLE
                entry_channel = None
                trailing_channel = None
                max_channel_reached = None
                min_channel_reached = None
                c2_triggered = False
                last_signal = None
                force_api_check = True
                save_state()
                log("[STATE] REGIME_A → IDLE (NEUTRAL)")
                continue

            # 2️⃣ MESMO SINAL → mantém posição
            elif signal == last_signal:
                log(f"[REGIME_A] Mesmo sinal ({signal}) — mantém")
            
            # 3️⃣ SINAL OPOSTO → reversão imediata
            elif signal in ("BUY", "SELL") and signal != last_signal:
                close_position(pos)

                if not market_is_open(tick_demo):
                    log("[IDLE] Mercado fechado — aguardando abertura")
                    time.sleep(5)
                    continue

                result = send_order(mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL)

                if result is None:
                    log("[IDLE] Ordem rejeitada — mantendo IDLE")
                    force_api_check = False
                    time.sleep(5)
                    continue

                last_signal = signal
                entry_channel = None
                max_channel_reached = None
                min_channel_reached = None
                c2_triggered = False
                save_state()
                log("[STATE] REGIME_A → REGIME_A (REVERSAL)")
                continue
        if entry_channel is None:
            continue

        # =============================
        # TRANSIÇÃO ROBUSTA A → B
        # =============================
        c2 = entry_channel + (2 if pos.type == mt5.POSITION_TYPE_BUY else -2)

        if not c2_triggered:
            if (
                pos.type == mt5.POSITION_TYPE_BUY and max_channel_reached >= c2
            ) or (
                pos.type == mt5.POSITION_TYPE_SELL and min_channel_reached <= c2
            ):
                c2_triggered = True
                trailing_channel = entry_channel + (1 if pos.type == mt5.POSITION_TYPE_BUY else -1)
                state = STATE_REGIME_B
                save_state()
                log("[STATE] REGIME_A → REGIME_B")

    # =================================================
    # REGIME B — TRAILING TÉCNICO MÓVEL
    # =================================================

    elif state == STATE_REGIME_B and positions:
        pos = positions[0]
        tick = mt5.symbol_info_tick(tick_demo)
        price = tick.ask if pos.type == mt5.POSITION_TYPE_BUY else tick.bid
        current_channel = get_channel_index(price)

        # ==========================================
        # ATUALIZA OS EXTREMOS ATINGINDOS
        # ==========================================
        if pos.type == mt5.POSITION_TYPE_BUY:
            if max_channel_reached is None:
                max_channel_reached = current_channel
            else:
                max_channel_reached = max(max_channel_reached, current_channel)
        else:
            if min_channel_reached is None:
                min_channel_reached = current_channel
            else:
                min_channel_reached = min(min_channel_reached, current_channel)


        # =========================
        # BUY
        # =========================

        if pos.type == mt5.POSITION_TYPE_BUY:

            # 1️⃣ AVANÇO DO TRAILING
            next_trigger = trailing_channel + 2

            if max_channel_reached >= next_trigger:
                trailing_channel = next_trigger - 1
                low, _ = get_channel_bounds(trailing_channel)
                save_state()
                log(f"[TRAIL BUY] Stop sobe para canal {trailing_channel} | {low:.2f}")

            # 2️⃣ STOP TÉCNICO
            stop_low, _ = get_channel_bounds(trailing_channel)
            if price <= stop_low:
                close_position(pos)
                state = STATE_POST_STOP
                entry_channel = None
                trailing_channel = None
                max_channel_reached = None
                min_channel_reached = None
                force_api_check = True
                save_state()
                log("[EXIT BUY] STOP — vai para POST_STOP")
                continue

        # =========================
        # SELL
        # =========================
        else:
            # 1️⃣ AVANÇO DO TRAILING
            next_trigger = trailing_channel - 2

            if min_channel_reached <= next_trigger:
                trailing_channel = next_trigger + 1
                _, high = get_channel_bounds(trailing_channel)
                save_state()
                log(f"[TRAIL BUY] Stop sobe para canal {trailing_channel} | {high:.2f}")

            # 2️⃣ STOP TÉCNICO
            _, stop_high = get_channel_bounds(trailing_channel)
            if price >= stop_high:
                close_position(pos)
                state = STATE_POST_STOP
                entry_channel = None
                trailing_channel = None
                max_channel_reached = None
                min_channel_reached = None
                force_api_check = True
                save_state()
                log("[EXIT SELL] STOP — vai para o POST_STOP")
                continue
        
    # =================================================
    # POST STOP — DECISÃO APÓS STOP NO LUCRO
    # =================================================

    elif state == STATE_POST_STOP:

        if should_check_api():
            raw = get_api_signal(tick_api, TIMEFRAME_API)
            if raw is None:
                log("[POST_STOP] API indisponível — aguardando")
                continue

            signal = interpret_signal(raw)

            # 1️⃣ NEUTRO → reset completo
            if signal == "NEUTRAL":
                state = STATE_IDLE
                last_signal = None
                trailing_channel = None
                max_channel_reached = None
                min_channel_reached = None
                force_api_check = True
                save_state()
                log("[POST_STOP] NEUTRAL → IDLE (reset total)")
                continue

            # 2️⃣ MESMO SINAL → NÃO entra, apenas aguarda próximos 15m
            elif signal == last_signal:
                log(f"[POST_STOP] Mesmo sinal ({signal}) — aguardando novo slot")
                # não muda estado, não entra
                continue
                
            # 3️⃣ SINAL OPOSTO → ENTRA IMEDIATAMENTE
            elif signal in ("BUY", "SELL") and signal != last_signal:
                if not market_is_open(tick_demo):
                    log("[IDLE] Mercado fechado — aguardando abertura")
                    time.sleep(5)
                    continue

                result = send_order(mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL)

                if result is None:
                    log("[IDLE] Ordem rejeitada — mantendo IDLE")
                    force_api_check = False
                    time.sleep(5)
                    continue
                
                
                last_signal = signal
                state = STATE_REGIME_A
                entry_channel = None
                trailing_channel = None
                max_channel_reached = None
                min_channel_reached = None
                c2_triggered = False
                save_state()
                log(f"[POST_STOP] Reentrada imediata ({signal}) → REGIME_A")
                continue

    time.sleep(CHECK_INTERVAL)
