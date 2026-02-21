
# =====================================================
# XAUUSD CHANNEL MODEL — STATE MACHINE (PRODUCTION)
# =====================================================

import time
import os
import logging
from datetime import datetime, timedelta
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

SYMBOL = "XAUUSD_"
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
STATE_IDLE = "IDLE"          # sem posição
STATE_REGIME_A = "REGIME_A"  # API ativa
STATE_REGIME_B = "REGIME_B"  # trailing técnico

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
def get_api_signal(simbol, timeframe='15m'):
    payload = {
        "model": "forex",
        "ticker": simbol,
        "timeframe": timeframe
    }

    headers = {
        "Authorization": f"Api-Key {API_KEY}"}

    try:
        r = requests.post(
            "https://om-qs.com/api/v1/models/",
            headers=headers,
            json=payload
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

mt5.symbol_select(SYMBOL, True)

def send_order(order_type):
    tick = mt5.symbol_info_tick(SYMBOL)
    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": VOLUME,
        "type": order_type,
        "price": price,
        "deviation": SLIPPAGE,
        "magic": MAGIC_NUMBER,
        "comment": ORDER_COMMENT
    }
    if mt5.order_send(request).retcode != mt5.TRADE_RETCODE_DONE:
        print("Erro ao abrir posição:", mt5.order_send(request))
    else:
        return mt5.order_send(request)
    # se der ruim voltar e deixar somente o return na linha normal

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
# TIME CONTROL
# =====================================================
def is_api_time(last_check):
    if last_check is None:
        return True
    return datetime.now() >= last_check + timedelta(minutes=API_CHECK_MINUTES)

# =====================================================
# STATE MACHINE
# =====================================================
state = STATE_IDLE
last_api_check = None
last_signal = None

entry_channel = None
trailing_channel = None

log("[SYSTEM] STARTED — STATE MACHINE ACTIVE")

while True:

    positions = mt5.positions_get(symbol=SYMBOL)

    # =================================================
    # IDLE STATE
    # =================================================
    if state == STATE_IDLE:
        if is_api_time(last_api_check):
            raw = get_api_signal()
            last_api_check = datetime.now()

            signal = interpret_signal(raw)
            if signal in ("BUY", "SELL"):
                send_order(mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL)
                last_signal = signal
                state = STATE_REGIME_A
                log(f"[STATE] IDLE → REGIME_A ({signal})")

    # =================================================
    # REGIME A
    # =================================================
    elif state == STATE_REGIME_A and positions:
        pos = positions[0]
        tick = mt5.symbol_info_tick(SYMBOL)
        price = tick.bid

        if entry_channel is None:
            entry_channel = get_channel_index(price)

        if is_api_time(last_api_check):
            raw = get_api_signal()
            last_api_check = datetime.now()
            signal = interpret_signal(raw)

            if signal == "NEUTRAL":
                close_position(pos)
                state = STATE_IDLE
                entry_channel = None
                log("[STATE] REGIME_A → IDLE (NEUTRAL)")

            elif signal and signal != last_signal:
                close_position(pos)
                send_order(mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL)
                last_signal = signal
                entry_channel = None
                log("[STATE] REGIME_A → REGIME_A (REVERSAL)")

        c2 = entry_channel + (2 if pos.type == mt5.POSITION_TYPE_BUY else -2)
        low, high = get_channel_bounds(c2)

        if (pos.type == mt5.POSITION_TYPE_BUY and price >= high) or \
           (pos.type == mt5.POSITION_TYPE_SELL and price <= low):
            trailing_channel = entry_channel + (1 if pos.type == mt5.POSITION_TYPE_BUY else -1)
            state = STATE_REGIME_B
            log("[STATE] REGIME_A → REGIME_B")

    # =================================================
    # REGIME B — TRAILING TÉCNICO MÓVEL
    # =================================================
    elif state == STATE_REGIME_B and positions:
        pos = positions[0]
        tick = mt5.symbol_info_tick(SYMBOL)
        if not tick:
            log("[SYSTEM] Tick inválido no REGIME B")
            close_position(pos)
            state = STATE_IDLE
            entry_channel = None
            trailing_channel = None
            continue

        price = tick.bid

        # =========================
        # BUY
        # =========================
        if pos.type == mt5.POSITION_TYPE_BUY:
            tc = trailing_channel

            # 1️⃣ AVANÇO DO TRAILING
            next_channel = tc + 2
            _, high_next = get_channel_bounds(next_channel)

            if price >= high_next:
                trailing_channel = next_channel - 1
                low, _ = get_channel_bounds(trailing_channel)

                log(f"[TRAIL BUY] Preço rompeu HIGH do canal {next_channel} ({high_next:.2f}) | "
                    f"Stop sobe para canal {trailing_channel} | Linha {low:.2f}"
                )

            # 2️⃣ STOP TÉCNICO
            stop_low, _ = get_channel_bounds(trailing_channel)
            if price <= stop_low:
                log("=" * 60)
                log(f"[EXIT BUY] STOP ATINGIDO em {price:.2f}")
                log(f"[EXIT BUY] Canal do Stop = {trailing_channel}")
                log("=" * 60)

                close_position(pos)
                state = STATE_IDLE
                entry_channel = None
                trailing_channel = None
                last_signal = None
                continue

        # =========================
        # SELL
        # =========================
        else:
            tc = trailing_channel

            # 1️⃣ AVANÇO DO TRAILING
            next_channel = tc - 2
            low_next, _ = get_channel_bounds(next_channel)

            if price <= low_next:
                trailing_channel = next_channel + 1
                _, high = get_channel_bounds(trailing_channel)

                log(
                    f"[TRAIL SELL] Preço rompeu LOW do canal {next_channel} ({low_next:.2f}) | "
                    f"Stop desce para canal {trailing_channel} | Linha {high:.2f}"
                )

            # 2️⃣ STOP TÉCNICO
            _, stop_high = get_channel_bounds(trailing_channel)
            if price >= stop_high:
                log("=" * 60)
                log(f"[EXIT SELL] STOP ATINGIDO em {price:.2f}")
                log(f"[EXIT SELL] Canal do Stop = {trailing_channel}")
                log("=" * 60)

                close_position(pos)
                state = STATE_IDLE
                entry_channel = None
                trailing_channel = None
                last_signal = None
                continue
