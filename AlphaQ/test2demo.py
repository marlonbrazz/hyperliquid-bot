# VERSÃO REFATORADA – FOCO EM MÁQUINA DE ESTADOS
# Objetivo: solidificar lógica de regime, tempo de consulta da API e responsabilidades claras

import time
import MetaTrader5 as mt5
from datetime import datetime, timedelta
import logging
import os
import requests
from dotenv import load_dotenv

# =====================================================
# CONFIGURAÇÕES
# =====================================================
load_dotenv()
API_KEY = os.getenv("API_KEY")
MT5_LOGIN = int(os.getenv("MT5_LOGIN"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")

SYMBOL = "XAUUSD_"
VOLUME = 0.01
TIMEFRAME_MINUTES = 15
CHECK_INTERVAL = 0.5

BASE_LOW = 1906.88
CHANNEL_SIZE = 5.61

# =====================================================
# LOG
# =====================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.info

# =====================================================
# ENUM DE ESTADOS
# =====================================================
STATE_IDLE = "IDLE"                 # sem posição, esperando primeiro sinal
STATE_REGIME_A = "REGIME_A"         # API manda
STATE_REGIME_B = "REGIME_B"         # Trailing técnico

# =====================================================
# UTILIDADES DE TEMPO
# =====================================================
def is_api_bar_close(now: datetime):
    return now.minute % TIMEFRAME_MINUTES == 0 and now.second < 2

# =====================================================
# CANAL
# =====================================================
def channel(price):
    return int((price - BASE_LOW) // CHANNEL_SIZE)

def bounds(ch):
    low = BASE_LOW + ch * CHANNEL_SIZE
    return low, low + CHANNEL_SIZE

# =====================================================
# API
# =====================================================
def fetch_api_signal():
    payload = {"model": "forex", "ticker": SYMBOL.replace("_", ""), "timeframe": "15m"}
    headers = {"Authorization": f"Api-Key {API_KEY}"}
    r = requests.post("https://om-qs.com/api/v1/models/", json=payload, headers=headers)
    if r.status_code != 200:
        return None
    return r.json().get("data", {}).get("signal")

# =====================================================
# MT5
# =====================================================
def mt5_init():
    mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    mt5.symbol_select(SYMBOL, True)

def open_trade(signal):
    tick = mt5.symbol_info_tick(SYMBOL)
    price = tick.ask if signal == 1 else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if signal == 1 else mt5.ORDER_TYPE_SELL
    mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": VOLUME,
                     "type": order_type, "price": price, "deviation": 20})

# =====================================================
# LOOP PRINCIPAL – FSM
# =====================================================
mt5_init()
state = STATE_IDLE
entry_channel = None
trailing_channel = None
last_api_bar = None

while True:
    tick = mt5.symbol_info_tick(SYMBOL)
    if not tick:
        time.sleep(1)
        continue

    price = tick.bid
    ch = channel(price)
    now = datetime.utcnow()

    # ================================
    # STATE: IDLE
    # ================================
    if state == STATE_IDLE:
        signal = fetch_api_signal()
        if signal in (0, 1):
            open_trade(signal)
            entry_channel = ch
            state = STATE_REGIME_A
            log(f"[ENTRY] {signal} @ {price}")

    # ================================
    # STATE: REGIME A (API)
    # ================================
    elif state == STATE_REGIME_A:
        if is_api_bar_close(now):
            if last_api_bar != now.minute:
                last_api_bar = now.minute
                signal = fetch_api_signal()

                if signal == 0.5:
                    log("[API] Neutral → exit")
                    mt5.positions_close(SYMBOL)
                    state = STATE_IDLE

                elif signal in (0, 1):
                    log("[API] Reversal")
                    mt5.positions_close(SYMBOL)
                    open_trade(signal)
                    entry_channel = ch

        # transição para regime B
        if abs(ch - entry_channel) >= 2:
            trailing_channel = entry_channel + (1 if ch > entry_channel else -1)
            state = STATE_REGIME_B
            log("[REGIME B] Trailing técnico ativo")

    # ================================
    # STATE: REGIME B (TRAIL)
    # ================================
    elif state == STATE_REGIME_B:
        low, high = bounds(trailing_channel)
        if price <= low or price >= high:
            log("[STOP] Trailing atingido")
            mt5.positions_close(SYMBOL)
            state = STATE_IDLE

    time.sleep(CHECK_INTERVAL)
