import time
import MetaTrader5 as mt5
from datetime import datetime
import logging
import os
import requests
from dotenv import load_dotenv

# =====================================================
# CONFIGURAÇÕES GERAIS
# =====================================================
load_dotenv()

# --- API ---
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY não definido no .env")

# --- MT5 ---
MT5_LOGIN = os.getenv("MT5_LOGIN")
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")

if not MT5_LOGIN or not MT5_PASSWORD or not MT5_SERVER:
    raise ValueError("Credenciais MT5 incompletas no .env")

MT5_LOGIN = int(MT5_LOGIN)

# =====================================================
# CONFIGURAÇÕES DE TRADING
# =====================================================
SYMBOL = "XAUUSD_"
VOLUME = 0.01
SLIPPAGE = 30
MAGIC_NUMBER = 12345671
ORDER_COMMENT = "CHANNEL_MODEL_V1"

CHECK_INTERVAL = 0.5  # segundos
API_TIMEOUT = 3     # segundos

# =====================================================
# CONFIGURAÇÕES DO CANAL
# =====================================================
BASE_LOW = 1906.88
CHANNEL_SIZE = 5.61

# =====================================================
# LOGGING PROFISSIONAL
# =====================================================
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(
    LOG_DIR,
    f"xau_live_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
)

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def log(msg):
    print(msg)
    logging.info(msg)

# =====================================================
# FUNÇÕES DE CANAL
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
# API EXTERNA
# =====================================================
def get_api_signal(symbol=SYMBOL, timeframe="15m"):
    payload = {
        "model": "forex",
        "ticker": symbol,
        "timeframe": timeframe
    }

    headers = {
        "Authorization": f"Api-Key {API_KEY}",
        "Content-Type": "application/json"
    }

    url = "https://om-qs.com/api/v1/models/"

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
        )

        if response.status_code != 200:
            log(f"[API] HTTP {response.status_code}")
            return "ERROR"

        data = response.json()
        log(f"[API RAW RESPONSE] {data}")
        
        signal = data.get("data", {}).get("signal")

        if signal not in (0, 0.5,1):
            log(f"[API] Sinal inválido: {signal}")
            return None

        return signal

    except requests.exceptions.Timeout:
        log("[API] Timeout")
        return None

    except Exception as e:
        log(f"[API] Erro inesperado: {e}")
        return None
    
# Interpreta o sinal da API

def interpret_signal(raw_signal):
    if raw_signal == 1:
        return "BUY"
    elif raw_signal == 0:
        return "SELL"
    elif raw_signal == 0.5:
        return "NEUTRAL"
    else:
        return None


# =====================================================
# MT5 SETUP
# =====================================================
if not mt5.initialize():
    log("[SYSTEM] Erro ao inicializar MT5")
    quit()

if not mt5.symbol_select(SYMBOL, True):
    log("[SYSTEM] Símbolo não disponível")
    quit()

# =====================================================
# FUNÇÕES DE EXECUÇÃO
# =====================================================

def send_order(order_type):
    tick = mt5.symbol_info_tick(SYMBOL)
    if not tick:
        log("[SYSTEM] Tick inválido")
        return None

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

    result = mt5.order_send(request)

    if result is None:
        log("[SYSTEM] order_send retornou None")
        log(f"[MT5 LAST ERROR] {mt5.last_error()}")
        log(f"[REQUEST] {request}")
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log("[SYSTEM] Falha na ordem")
        log(f"[RET_CODE] {result.retcode}")
        log(f"[COMMENT] {result.comment}")
        log(f"[REQUEST] {request}")
        return None

    log("[SYSTEM] Ordem executada com sucesso")
    log(f"[ORDER] Ticket={result.order} | Preço={result.price}")

    return result


def close_position(position):
    tick = mt5.symbol_info_tick(position.symbol)
    if not tick:
        log("[SYSTEM] Tick inválido no fechamento")
        return None

    close_type = (
        mt5.ORDER_TYPE_SELL
        if position.type == mt5.POSITION_TYPE_BUY
        else mt5.ORDER_TYPE_BUY
    )

    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": close_type,
        "position": position.ticket,
        "price": price,
        "deviation": SLIPPAGE,
        "magic": MAGIC_NUMBER,
        "comment": "CLOSE_" + ORDER_COMMENT
    }

    result = mt5.order_send(request)

    if result is None:
        log("[SYSTEM] Erro ao fechar posição — None")
        log(f"[MT5 LAST ERROR] {mt5.last_error()}")
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log("[SYSTEM] Falha ao fechar posição")
        log(f"[RET_CODE] {result.retcode}")
        log(f"[COMMENT] {result.comment}")
        return None

    log("[SYSTEM] Posição fechada com sucesso")
    log(f"[CLOSE] Ticket={position.ticket} | Preço={result.price}")

    return result


# =====================================================
# ENTRADA INICIAL
# =====================================================
log("[SYSTEM] Aguardando sinal válido da API para entrada inicial...")

while True:
    raw_signal = get_api_signal(SYMBOL, timeframe="15m")

    if raw_signal in ("ERROR", "TIMEOUT", None):
        log("[API] Sem resposta válida — tentando novamente...")
        time.sleep(2)
        continue

    signal = interpret_signal(raw_signal)

    if signal == "ERROR":
        log(f"[API] Sinal inválido recebido: {raw_signal}")
        time.sleep(2)
        continue


    if signal in ("BUY", "SELL"):
        log(f"[SYSTEM] Sinal inicial confirmado: {signal}")
        break

    log(f"[SYSTEM] Sinal neutro ({raw_signal}) — aguardando...")
    time.sleep(2)


order_type = (
    mt5.ORDER_TYPE_BUY
    if signal == "BUY"
    else mt5.ORDER_TYPE_SELL
)

entry_result = send_order(order_type)
if entry_result is None:
    log("[FATAL] Falha na entrada inicial — abortando")
    mt5.shutdown()
    quit()


position_state = {
    "regime": "A",
    "entry_channel": None,
    "trailing_channel": None
}

tick = mt5.symbol_info_tick(SYMBOL)
entry_price = tick.ask if signal == "BUY" else tick.bid

log("=" * 60)
log(f"[ENTRY] {signal} executado em {entry_price:.2f}")
log(f"[ENTRY] Canal de entrada (C0): {get_channel_index(entry_price)}")
log("=" * 60)
# =====================================================
# LOOP PRINCIPAL
# =====================================================
while True:
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions or len(positions) != 1:
        log("[SYSTEM] Estado inválido de posições — encerrando")
        break

    pos = positions[0]
    tick = mt5.symbol_info_tick(SYMBOL)
    if not tick:
        log("[SYSTEM] Tick inválido — encerrando por segurança")
        close_position(pos)
        break
    price = tick.bid
    channel = get_channel_index(price)

    if position_state["entry_channel"] is None:
        position_state["entry_channel"] = channel

    log(f"[PRICE] {price:.2f} | Canal={channel} | Regime={position_state['regime']}")

    # =================================================
    # REGIME A — API TEM AUTORIDADE
    # =================================================
    if position_state["regime"] == "A":
        c2 = position_state["entry_channel"] + 2
        low_c2, high_c2 = get_channel_bounds(c2)

        api_signal = get_api_signal(SYMBOL, timeframe="15m")

        if api_signal in ("ERROR", "TIMEOUT"):
            log("[EMERGENCY] API indisponível — fechando posição")
            close_position(pos)
            break

        if api_signal == "SELL":
            log("[API] Reversão autorizada — SELL")
            close_position(pos)
            send_order(mt5.ORDER_TYPE_SELL)
            break

        if api_signal == "NEUTRAL":
            log("[API] Neutral — fechando posição")
            close_position(pos)
            break

        if pos.type == mt5.POSITION_TYPE_BUY:
            if price >= high_c2:
                trailing_channel = position_state["entry_channel"] + 1
                position_state["trailing_channel"] = trailing_channel
                position_state["regime"] = "B"

                low, _ = get_channel_bounds(trailing_channel)
                log(f"[REGIME B] BUY ativado | Stop no canal {trailing_channel} ({low:.2f})")

        else:  # SELL
            low_c2, _ = get_channel_bounds(c2)
        if price <= low_c2:
            trailing_channel = position_state["entry_channel"] - 1
            position_state["trailing_channel"] = trailing_channel
            position_state["regime"] = "B"

            _, high = get_channel_bounds(trailing_channel)
            log(f"[REGIME B] SELL ativado | Stop no canal {trailing_channel} ({high:.2f})")

            low, _ = get_channel_bounds(trailing_channel)
            log(f"[REGIME B] Ativado por toque no HIGH ({high_c2}) do {c2}")
            log(f"[STOP_INICIAL] Canal={trailing_channel} | Linha={low:.2f}")

    # =================================================
    # REGIME B — TRAILING TÉCNICO
    # =================================================
    elif position_state["regime"] == "B":
        tc = position_state["trailing_channel"]

        if pos.type == mt5.POSITION_TYPE_BUY:
            next_channel = tc + 2
            _, high_next = get_channel_bounds(next_channel)

            if price >= high_next:
                position_state["trailing_channel"] = next_channel - 1
                low, _ = get_channel_bounds(position_state["trailing_channel"])
                log(f"[TRAIL BUY] Stop sobe para canal {position_state['trailing_channel']} ({low:.2f})")

            stop_low, _ = get_channel_bounds(position_state["trailing_channel"])
            if price <= stop_low:
                log("[EXIT BUY] Stop técnico atingido")
                close_position(pos)
                break

        else:  # SELL
            next_channel = tc - 2
            low_next, _ = get_channel_bounds(next_channel)

            if price <= low_next:
                position_state["trailing_channel"] = next_channel + 1
                _, high = get_channel_bounds(position_state["trailing_channel"])
                log(f"[TRAIL SELL] Stop desce para canal {position_state['trailing_channel']} ({high:.2f})")

            _, stop_high = get_channel_bounds(position_state["trailing_channel"])
            if price >= stop_high:
                log("[EXIT SELL] Stop técnico atingido")
                close_position(pos)
                break


    time.sleep(CHECK_INTERVAL)

mt5.shutdown()
log("[SYSTEM] Execução encerrada")
