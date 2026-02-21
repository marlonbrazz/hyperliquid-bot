
# =====================================================
# XAUUSD CHANNEL MODEL — STATE MACHINE (PRODUCTION FINAL)
# =====================================================

import time
import os
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

CHECK_INTERVAL = 0.5

BASE_LOW = 1906.88
CHANNEL_SIZE = 5.61

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
    payload = {
        "model": "forex",
        "ticker": symbol,
        "timeframe": timeframe
    }

    headers = {
        "Authorization": f"Api-Key {API_KEY}"
    }

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
        if signal not in (0, 0.5, 1):
            return None

        return signal

    except Exception as e:
        log(f"[API ERROR] {e}")
        return None

def interpret_signal(signal):
    if signal == 1:
        return "BUY"
    if signal == 0:
        return "SELL"
    if signal == 0.5:
        return "NEUTRAL"
    return None

# =====================================================
# MT5
# =====================================================
if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
    raise RuntimeError("MT5 init failed")

mt5.symbol_select(tick_demo, True)

def send_order(order_type):
    tick = mt5.symbol_info_tick(tick_demo)
    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": tick_demo,
        "volume": VOLUME,
        "type": order_type,
        "price": price,
        "deviation": SLIPPAGE,
        "magic": MAGIC_NUMBER,
        "comment": ORDER_COMMENT
    }

    result = mt5.order_send(request)
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

# =====================================================
# TIME CONTROL — QUARTER HOURS
# =====================================================
last_api_check = None
force_api_check = True

def get_current_slot(now=None):
    if now is None:
        now = datetime.now()
    minute = (now.minute // API_CHECK_MINUTES) * API_CHECK_MINUTES
    return now.replace(minute=minute, second=0, microsecond=0)

def should_check_api():
    global last_api_check, force_api_check

    now = datetime.now()
    current_slot = get_current_slot(now)

    # força checagem imediata (entrada, stop, reset)
    if force_api_check:
        force_api_check = False
        last_api_check = current_slot
        return True

    # nova janela de 15m
    if last_api_check is None or current_slot > last_api_check:
        last_api_check = current_slot
        return True

    return False


# =====================================================
# STATE MACHINE
# =====================================================
state = STATE_IDLE
last_signal = None
entry_channel = None
trailing_channel = None

# force_api_check = True - não sei se precisa, pois ja tem uma linha dessa  acima.

log("[SYSTEM] STARTED — STATE MACHINE ACTIVE")

while True:

    positions = mt5.positions_get(symbol=tick_demo)

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
                send_order(mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL)
                last_signal = signal
                state = STATE_REGIME_A
                entry_channel = None
                log(f"[STATE] IDLE → REGIME_A ({signal})")
    
    # =================================================
    # REGIME A
    # =================================================

    elif state == STATE_REGIME_A and positions:
        pos = positions[0]
        tick = mt5.symbol_info_tick(tick_demo)
        price = tick.ask if pos.type == mt5.POSITION_TYPE_BUY else tick.bid

        if entry_channel is None:
            entry_channel = get_channel_index(price)
    
        if should_check_api():
            raw = get_api_signal(tick_api, TIMEFRAME_API)
            signal = interpret_signal(raw)
            if signal is None:
                pass

            # 1️⃣ NEUTRO → fecha e volta para IDLE
            if signal == "NEUTRAL":
                close_position(pos)
                state = STATE_IDLE
                entry_channel = None
                trailing_channel = None
                last_signal = None
                force_api_check = True
                log("[STATE] REGIME_A → IDLE (NEUTRAL)")
                continue

            # 2️⃣ MESMO SINAL → mantém posição
            elif signal == last_signal:
                log(f"[REGIME_A] Mesmo sinal ({signal}) — mantém")
            
            # 3️⃣ SINAL OPOSTO → reversão imediata
            elif signal in ("BUY", "SELL") and signal != last_signal:
                close_position(pos)
                send_order(mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL)
                last_signal = signal
                entry_channel = None
                log("[STATE] REGIME_A → REGIME_A (REVERSAL)")
                continue
        if entry_channel is None:
            continue

        c2 = entry_channel + (2 if pos.type == mt5.POSITION_TYPE_BUY else -2)
        low, high = get_channel_bounds(c2)

        if (pos.type == mt5.POSITION_TYPE_BUY and price >= high) or            (pos.type == mt5.POSITION_TYPE_SELL and price <= low):
            trailing_channel = entry_channel + (1 if pos.type == mt5.POSITION_TYPE_BUY else -1)
            state = STATE_REGIME_B
            log("[STATE] REGIME_A → REGIME_B")

    # =================================================
    # REGIME B — TRAILING TÉCNICO MÓVEL
    # =================================================

    elif state == STATE_REGIME_B and positions:
        pos = positions[0]
        tick = mt5.symbol_info_tick(tick_demo)
        price = tick.ask if pos.type == mt5.POSITION_TYPE_BUY else tick.bid

        # =========================
        # BUY
        # =========================

        if pos.type == mt5.POSITION_TYPE_BUY:

            # 1️⃣ AVANÇO DO TRAILING
            next_channel = trailing_channel + 2
            _, high_next = get_channel_bounds(next_channel)

            if price >= high_next:
                trailing_channel = next_channel - 1
                low, _ = get_channel_bounds(trailing_channel)
                log(f"[TRAIL BUY] Stop sobe para canal {trailing_channel} | {low:.2f}")

            # 2️⃣ STOP TÉCNICO
            stop_low, _ = get_channel_bounds(trailing_channel)
            if price <= stop_low:
                close_position(pos)
                state = STATE_POST_STOP
                entry_channel = None
                trailing_channel = None
                force_api_check = True
                log("[EXIT BUY] STOP — vai para POST_STOP")
                continue

        # =========================
        # SELL
        # =========================
        else:
            # 1️⃣ AVANÇO DO TRAILING
            next_channel = trailing_channel - 2
            low_next, _ = get_channel_bounds(next_channel)

            if price <= low_next:
                trailing_channel = next_channel + 1
                _, high = get_channel_bounds(trailing_channel)
                log(f"[TRAIL SELL] Stop desce para canal {trailing_channel} | {high:.2f}")

            # 2️⃣ STOP TÉCNICO
            _, stop_high = get_channel_bounds(trailing_channel)
            if price >= stop_high:
                close_position(pos)
                state = STATE_POST_STOP
                entry_channel = None
                trailing_channel = None
                force_api_check = True
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
                force_api_check = True
                log("[POST_STOP] NEUTRAL → IDLE (reset total)")
                continue

            # 2️⃣ MESMO SINAL → NÃO entra, apenas aguarda próximos 15m
            elif signal == last_signal:
                log(f"[POST_STOP] Mesmo sinal ({signal}) — aguardando novo slot")
                # não muda estado, não entra
                continue
                
            # 3️⃣ SINAL OPOSTO → ENTRA IMEDIATAMENTE
            elif signal in ("BUY", "SELL") and signal != last_signal:
                send_order(mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL)
                last_signal = signal
                state = STATE_REGIME_A
                entry_channel = None
                log(f"[POST_STOP] Reentrada imediata ({signal}) → REGIME_A")
                continue


    time.sleep(CHECK_INTERVAL)
