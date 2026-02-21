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
CHECK_INTERVAL = 2  # segundos (dry-run)

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
    "regime": "A",
    "trailing_channel": None
}

print("=" * 60)
print(f"[ENTRY] BUY simulado em {entry_price:.2f}")
print(f"[ENTRY] Canal de entrada (C0): {entry_channel}")
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

    # ======================================================
    # REGIME A → ATIVA REGIME B SOMENTE NO HIGH DO C2
    # ======================================================
    if position_state["regime"] == "A":
        c2 = position_state["entry_channel"] + 2
        _, high_c2 = get_channel_bounds(c2)

        if price >= high_c2:
            trailing_channel = position_state["entry_channel"] + 1  # C1
            position_state["trailing_channel"] = trailing_channel
            position_state["regime"] = "B"

            low, _ = get_channel_bounds(trailing_channel)
            print(f"[REGIME B] Ativado por toque no HIGH do {c2}")
            print(f"[STOP INICIAL] Canal {trailing_channel} | Linha {low:.2f}")

    # ======================================================
    # REGIME B → TRAILING POR TOQUE EM HIGH
    # ======================================================
    elif position_state["regime"] == "B":
        trailing_channel = position_state["trailing_channel"]

        # Próximo canal acima do stop
        next_channel = trailing_channel + 1
        _, high_next = get_channel_bounds(next_channel)

        # 🔼 Libera subida do stop
        if price >= high_next:
            position_state["trailing_channel"] = next_channel
            low, _ = get_channel_bounds(next_channel)
            print(f"[TRAILING] Stop sobe para canal {next_channel} | Linha {low:.2f}")

        # 🔻 Verifica STOP
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
