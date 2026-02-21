import math
import os
import time
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from eth_account import Account
from dotenv import load_dotenv
from Bot_Hyper_Config import PRICE_DECIMALS

load_dotenv()

HL_PRIVATE_KEY = os.getenv("HL_PRIVATE_KEY")
HL_ADDRESS = os.getenv("HL_ADDRESS")

if not HL_PRIVATE_KEY or not HL_ADDRESS:
    raise ValueError("HL_PRIVATE_KEY ou HL_ADDRESS não definido no .env")

account = Account.from_key(HL_PRIVATE_KEY)

exchange = Exchange(
    account,
    constants.MAINNET_API_URL,
)

# ==============================
# FUNÇÕES
# ==============================

def get_balance():
    state = exchange.info.user_state(HL_ADDRESS)
    return float(state["marginSummary"]["accountValue"])


def get_open_position(symbol):
    positions = exchange.info.user_state(HL_ADDRESS)["assetPositions"]
    for pos in positions:
        if pos["position"]["coin"] == symbol and float(pos["position"]["szi"]) != 0:
            size = float(pos["position"]["szi"])
            return "LONG" if size > 0 else "SHORT"
    return None


def open_position(symbol, side, size, leverage=2):

    is_buy = True if side == "LONG" else False

    exchange.update_leverage(leverage, symbol, False)

    # 1) Abre a posição
    exchange.market_open(symbol, is_buy, size)

    # espera a posição existir
    for _ in range(10):
        time.sleep(0.5)

        positions = exchange.info.user_state(HL_ADDRESS)["assetPositions"]

        for pos in positions:
            if pos["position"]["coin"] == symbol:
                size = float(pos["position"]["szi"])
                if size != 0:
                                        return

    time.sleep(0.5)

def close_position(symbol):
    positions = exchange.info.user_state(HL_ADDRESS)["assetPositions"]

    for pos in positions:
        if pos["position"]["coin"] == symbol:
            size = float(pos["position"]["szi"])
            if size == 0:
                return

            is_buy = False if size > 0 else True

            exchange.market_close(symbol)

def update_stop(symbol, new_stop):

    open_orders = exchange.info.open_orders(HL_ADDRESS)

    # 1️⃣ Cancela apenas trigger stops do mesmo símbolo
    for order in open_orders:
        if order.get("coin") != symbol:
             continue

        oid = order.get("oid")
        if oid:
             exchange.cancel(symbol, oid)

    # 2️⃣ Busca posição ativa
    user_state = exchange.info.user_state(HL_ADDRESS)
    positions = user_state.get("assetPositions", [])

    for pos in positions:
        position = pos.get("position", {})

        if position.get("coin") != symbol:
            continue

        size = float(position.get("szi", 0))

        if size == 0:
            return False

        # LONG → size > 0 → stop será SELL
        is_buy = False if size > 0 else True

       
        exchange.order(
            symbol,
            is_buy,
            abs(size),
            new_stop,
            {
                "trigger": {
                    "triggerPx": new_stop,
                    "isMarket": True,
                    "tpsl": "sl"
                }
            }
        )
        return True

    return False


def truncate(value, decimals=1):
    factor = 10 ** decimals
    return math.floor(value * factor) / factor

def get_hl_position(symbol):
    user_state = exchange.info.user_state(HL_ADDRESS)
    positions = user_state.get("assetPositions", [])

    for pos in positions:
        position = pos.get("position", {})
        if position.get("coin") == symbol:
            size = float(position.get("szi", 0))
            if size > 0:
                return "LONG"
            elif size < 0:
                return "SHORT"

    return None

def get_current_stop(symbol):
    open_orders = exchange.info.open_orders(HL_ADDRESS)

    for order in open_orders:
        if order.get("coin") == symbol:
            trigger = order.get("orderType", {}).get("trigger")
            if trigger and trigger.get("tpsl") == "sl":
                return float(trigger.get("triggerPx"))

    return None



def get_asset_precision(symbol):
    meta = exchange.info.meta()

    for asset in meta["universe"]:
        if asset["name"] == symbol:
            sz_decimals = asset.get("szDecimals", 2)
            px_decimals = PRICE_DECIMALS.get(symbol, 2)
            return sz_decimals, px_decimals

    raise Exception(f"{symbol} não encontrado")




def format_hl_values(price, stop, size, asset):
    px_decimals = asset.get("pxDecimals")
    sz_decimals = asset.get("szDecimals")
    return (f"{price:.{px_decimals}f}", f"{stop:.{px_decimals}f}", f"{size:.{sz_decimals}f}")


