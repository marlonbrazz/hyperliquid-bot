import time
import MetaTrader5 as mt5
from datetime import datetime

# ==============================
# CONFIGURAÇÕES DO CANAL
# ==============================
BASE_LOW = 1906.88
BASE_HIGH = 1912.49
CHANNEL_SIZE = 5.61

SYMBOL = "XAUUSD_"
CHECK_INTERVAL = 2  # segundos (dry-run rápido)

# ==============================
# FUNÇÕES DE CANAL
# ==============================
def get_channel_index(price):
    """
    Retorna o índice inteiro do canal onde o preço está.
    Canal base = índice 0
    """
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
    print("Erro ao inicializar MT5")
    quit()

tick = mt5.symbol_info_tick(SYMBOL)
if not tick:
    print("Símbolo não encontrado")
    quit()

# ==============================
# ENTRADA MANUAL (BUY)
# ==============================
entry_price = tick.ask
entry_channel = get_channel_index(entry_price)

position_state = {
    "active": True,
    "side": "BUY",
    "entry_price": entry_price,
    "entry_channel": entry_channel,
    "regime": "A",  # A = sinal | B = trailing
    "trailing_channel": None
}

print("=" * 60)
print(f"[ENTRY] BUY simulado em {entry_price:.2f}")
print(f"[ENTRY] Canal de entrada: {entry_channel}")
print("=" * 60)

# ==============================
# LOOP DRY-RUN
# ==============================
while position_state["active"]:
    tick = mt5.symbol_info_tick(SYMBOL)
    price = tick.bid
    current_channel = get_channel_index(price)

    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] PRICE={price:.2f} | Canal={current_channel} | Regime={position_state['regime']}")

    # ==========================
    # REGIME A → ATIVA TRAILING
    # ==========================
    if position_state["regime"] == "A":
        if current_channel >= position_state["entry_channel"] + 1:
            trailing_channel = position_state["entry_channel"] + 1
            position_state["trailing_channel"] = trailing_channel
            position_state["regime"] = "B"

            low, high = get_channel_bounds(trailing_channel)
            print(f"[REGIME B] Trailing ATIVADO")
            print(f"[STOP] Canal {trailing_channel} | Linha {low:.2f}")

    # ==========================
    # REGIME B → TRAILING ATIVO
    # ==========================
    elif position_state["regime"] == "B":
        # sobe trailing canal por canal
        expected_trailing = current_channel - 1

        if expected_trailing > position_state["trailing_channel"]:
            position_state["trailing_channel"] = expected_trailing
            low, _ = get_channel_bounds(expected_trailing)
            print(f"[TRAILING] Stop sobe para canal {expected_trailing} | Linha {low:.2f}")

        # verifica stop
        stop_low, _ = get_channel_bounds(position_state["trailing_channel"])
        if price <= stop_low:
            print("=" * 60)
            print(f"[EXIT] STOP ATINGIDO em {price:.2f}")
            print(f"[EXIT] Canal do stop: {position_state['trailing_channel']}")
            print("=" * 60)
            position_state["active"] = False
            break

    time.sleep(CHECK_INTERVAL)

mt5.shutdown()
