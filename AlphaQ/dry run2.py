import time
import os
import logging
import MetaTrader5 as mt5
from datetime import datetime

# ==============================
# CONFIGURAÇÕES DO CANAL
# ==============================
BASE_LOW = 1906.88
BASE_HIGH = 1912.49
CHANNEL_SIZE = 5.61

SYMBOL = "XAUUSD_"
CHECK_INTERVAL = 2  # segundos (dry-run)

# ==============================
# SETUP DE LOG
# ==============================
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(
    LOG_DIR,
    f"xau_dryrun_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
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

# ==============================
# FUNÇÕES DE CANAL
# ==============================
def get_channel_index(price):
    if price >= BASE_LOW:
        return int((price - BASE_LOW) // CHANNEL_SIZE)
    else:
        return int((price - BASE_LOW) // CHANNEL_SIZE) - 1


def get_channel_bounds(channel_index):
    low = BASE_LOW + channel_index * CHANNEL_SIZE
    high = low + CHANNEL_SIZE
    return low, high

# ==============================
# INICIALIZAÇÃO MT5
# ==============================
if not mt5.initialize():
    log("Erro ao inicializar MT5")
    quit()

tick = mt5.symbol_info_tick(SYMBOL)
if not tick:
    log("Símbolo não encontrado")
    quit()

# ==============================
# ENTRADA MANUAL (BUY - DRY RUN)
# ==============================
entry_price = tick.ask
entry_channel = get_channel_index(entry_price)

position_state = {
    "active": True,
    "side": "BUY",
    "entry_price": entry_price,
    "entry_channel": entry_channel,
    "regime": "A",
    "trailing_channel": None
}

log("=" * 60)
log(f"[ENTRY] BUY simulado em {entry_price:.2f}")
log(f"[ENTRY] Canal de entrada: {entry_channel}")
log("=" * 60)

# ==============================
# LOOP DRY-RUN
# ==============================
while position_state["active"]:
    tick = mt5.symbol_info_tick(SYMBOL)
    price = tick.bid
    current_channel = get_channel_index(price)

    now = datetime.now().strftime("%H:%M:%S")
    log(f"[{now}] PRICE={price:.2f} | Canal={current_channel} | Regime={position_state['regime']}")

    # ==========================
    # REGIME A → ATIVA TRAILING
    # ==========================
    if position_state["regime"] == "A":
        if current_channel >= position_state["entry_channel"] + 2:
            trailing_channel = current_channel - 2
            position_state["trailing_channel"] = trailing_channel
            position_state["regime"] = "B"

            low, _ = get_channel_bounds(trailing_channel)
            log("[REGIME B] Trailing ATIVADO")
            log(f"[STOP] Canal {trailing_channel} | Linha {low:.2f}")

    # ==========================
    # REGIME B → TRAILING ATIVO
    # ==========================
    elif position_state["regime"] == "B":
        expected_trailing = current_channel - 2

        if expected_trailing > position_state["trailing_channel"]:
            position_state["trailing_channel"] = expected_trailing
            low, _ = get_channel_bounds(expected_trailing)
            log(f"[TRAILING] Stop sobe para canal {expected_trailing} | Linha {low:.2f}")

        # STOP por toque exato
        stop_low, _ = get_channel_bounds(position_state["trailing_channel"])
        if price <= stop_low:
            log("=" * 60)
            log(f"[EXIT] STOP ATINGIDO em {price:.2f}")
            log(f"[EXIT] Canal do stop: {position_state['trailing_channel']}")
            log("=" * 60)
            position_state["active"] = False
            break

    time.sleep(CHECK_INTERVAL)

mt5.shutdown()
log("MT5 finalizado")
