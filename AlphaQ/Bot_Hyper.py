import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from Bot_Hyper_executor import (
    get_balance,
    get_open_position,
    open_position,
    close_position,
    update_stop, 
    get_hl_position,
    get_current_stop,
    get_asset_precision,
    format_hl_values
   )
from Bot_Hyper_Config import (
    symbol_map,
    PRICE_DECIMALS,
)       

from dotenv import load_dotenv

# ==============================
# CONFIGURAÇÕES
# ==============================
load_dotenv()

# --- API ---
API_KEY = os.getenv("API_KEY")
if API_KEY is None:
    raise ValueError("API_KEY não definido no .env")


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")



# ==============================
# FUNÇÕES AUXILIARES
# ==============================
def get_current_position(symbol):
    pos = get_open_position(symbol)

    if pos is None:
        return None
    
    if pos["side"] == "long":
        return 0
    
    if pos["side"] == "short":
        return 1

def ensure_stop(symbol, stop, api_symbol):
    current_stop = get_current_stop(symbol)

    if current_stop is None or abs(current_stop - stop) > 1e-6:
        if update_stop(symbol, stop):
            print(f"{api_symbol} | STOP atualizado → {stop}")
            send_telegram(f"{api_symbol} | STOP atualizado → {stop}")

def get_signal(symbol, timeframe="4h"):
    payload = {
        "model": "crypto",
        "ticker": symbol,
        "timeframe": timeframe
    }

    headers = {
        "Authorization": f"Api-Key {API_KEY}"
    }

    url = "https://om-qs.com/api/v1/models/"

    try:
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()

        signal = data["data"]["signal"]
        stop = data["data"]["stop"]
        price = data["data"]["price"]

        return signal, stop, price
    
    except Exception as e:
        print("Erro ao obter sinal:", e)
        return None

def calculate_size(symbol_price, total_symbols, leverage, risk_factor):
    balance = get_balance() 
    capital_total = balance * leverage
    capital_por_trade = (capital_total / total_symbols) * risk_factor

    size_coin = capital_por_trade / symbol_price # comentei para testar
    #size_coin = 0.05 # ainda sim, não abre ordem
    return size_coin

def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        })
    except:
        pass

def get_entry_delay_minutes(timeframe: str):
    delay_map = {
        "15m": 1,
        "1h": 2,
        "4h": 2,
        "1d": 2
    }
    return delay_map.get(timeframe, 2)

# =====================================================
# TIME CONTROL
# =====================================================
BRASIL = ZoneInfo("America/Sao_Paulo")

last_api_check = None
force_api_check = True

def timeframe_to_minutes(tf: str):
    mapping = {
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440
    }
    return mapping.get(tf, 60)

def get_current_slot(timeframe, now=None):
    now = now or datetime.now(BRASIL)
    tf_minutes = timeframe_to_minutes(timeframe)

    # Define o último marco das 19:00
    anchor = now.replace(hour=19, minute=0, second=0, microsecond=0)
    if now < anchor:
        anchor = anchor - timedelta(days=1)

    # minutos desde o marco
    elapsed_minutes = int((now - anchor).total_seconds() // 60)

    # qual bloco atual estamos
    slot_index = elapsed_minutes // tf_minutes

    return slot_index

def should_check_api(timeframe):
    global last_api_check, force_api_check

    now = datetime.now(BRASIL)
    tf_minutes = timeframe_to_minutes(timeframe)
    entry_delay = get_entry_delay_minutes(timeframe)

    # Marco base das 19:00
    anchor = now.replace(hour=19, minute=0, second=0, microsecond=0)
    if now < anchor:
        anchor = anchor - timedelta(days=1)

    # minutos totais desde o anchor
    elapsed_minutes = int((now - anchor).total_seconds() // 60)

    # slot atual
    slot = elapsed_minutes // tf_minutes

    # minutos dentro do slot atual
    minutes_into_slot = elapsed_minutes % tf_minutes

    # Primeira execução do bot
    if force_api_check:
        force_api_check = False
        last_api_check = slot
        return True

    # Executa apenas se:
    # 1) mudou o slot
    # 2) já passaram X minutos dentro do novo slot
    if (last_api_check is None or slot != last_api_check) and minutes_into_slot >= entry_delay:
        last_api_check = slot
        return True

    return False

# ==============================
# LOOP PRINCIPAL
# ==============================
def run_trading(symbols, timeframe="4h", alavancagem = 1,  risk_factor = 0.9):

    # LIMITES DE SEGURANÇA
    if risk_factor > 0.95:
        print("⚠️  Risk factor acima do limite permitido.")
        print(" Valor ajustado automaticamente para 0.95 (95% do capital alocado).")
        print("   Nota:\n"
              "       Qual a minha exposição total? (%)\n"    )
        risk_factor = 0.95

    if alavancagem > 3:
        print("⚠️  Alavancagem muito alta detectada.")
        print(" Por segurança, o valor foi ajustado automaticamente para 1x.")
        print(
            "   Níveis de referência:\n"
            "   1x → Recomendado (maior controle de risco)\n"
            "   2x → Exposição moderada (maior potencial de lucro e perda)\n"
            "   3x → Alto risco (use apenas com plena consciência da exposição)\n"
        )
        alavancagem = 1
    
    print("Bot iniciado...")

    position_state = {}
    last_stop = {}

    for s in symbols:
        position_state[s] = None  # LONG | SHORT | None
        last_stop[s] = None
    
    while True:
        #send_start_log(symbols, timeframe, alavancagem)
        if not should_check_api(timeframe):
            time.sleep(30)
            continue
        
        for api_symbol in symbols:
            hl_symbol = symbol_map[api_symbol]

            # chamada segura da API
            result = get_signal(api_symbol, timeframe)
            if result is None:
                continue
            
            signal, stop, price = result

            state = get_hl_position(hl_symbol)
            position_state[api_symbol] = state             

            # BUY
            if signal == 1:
                side = "LONG"

                if price > stop:
                    if state is None:
                        size = calculate_size(price, len(symbols), alavancagem, risk_factor)
                        if size is None:
                            continue

                        # Formatação Precisa da API → HL
                        sz_decimals, px_decimals = get_asset_precision(hl_symbol)
                        price = float(f"{price:.{px_decimals}f}")
                        stop  = float(f"{stop:.{px_decimals}f}")
                        size  = float(f"{size:.{sz_decimals}f}")

                        open_position(hl_symbol, side, size, leverage=alavancagem)
                        time.sleep(0.5)

                        current_stop = get_current_stop(hl_symbol)

                        if current_stop is None or abs(current_stop - stop) > 1e-6:
                            if update_stop(hl_symbol, stop):
                                print(f"{api_symbol} | STOP colocado → {stop}")
                                send_telegram(f"{api_symbol} | STOP colocado → {stop}")

                        position_state[api_symbol] = "LONG"

                        print(f"🟢 {api_symbol}:   Compra aberta | vol={size} | price={price} | stop={stop}")
                        send_telegram(f"🟢 {api_symbol}:   Compra aberta | vol={size} lote | price={price} | stop={stop}")

                    elif state == "LONG":
                        size = calculate_size(price, len(symbols), alavancagem, risk_factor)
                        if size is None:
                            continue
                        # Formatação Precisa da API → HL
                        sz_decimals, px_decimals = get_asset_precision(hl_symbol)
                        price = float(f"{price:.{px_decimals}f}")
                        stop  = float(f"{stop:.{px_decimals}f}")
                        size  = float(f"{size:.{sz_decimals}f}")

                        ensure_stop(hl_symbol, stop, api_symbol)
                        #print(f"{api_symbol}: Já estamos comprados, aguardando próximo sinal.")
                        pass

                    elif state == "SHORT":
                        close_position(hl_symbol)
                        time.sleep(0.5)

                        size = calculate_size(price, len(symbols), alavancagem, risk_factor)
                        if size is None:
                            continue
                        
                        # Formatação Precisa da API → HL
                        sz_decimals, px_decimals = get_asset_precision(hl_symbol)
                        price = float(f"{price:.{px_decimals}f}")
                        stop  = float(f"{stop:.{px_decimals}f}")
                        size  = float(f"{size:.{sz_decimals}f}")

                        open_position(hl_symbol, side, size, leverage=alavancagem)
                        time.sleep(0.5)
                        print(f"{api_symbol}: 🔁 REVERSÃO | SHORT → LONG | vol={size} | price={price} | stop={stop}.")
                        send_telegram(f"{api_symbol}: 🔁 REVERSÃO | SHORT → LONG | vol={size} | price={price} | stop={stop}.")

                        ensure_stop(hl_symbol, stop, api_symbol)

                        position_state[api_symbol] = "LONG"


               # INTERVALO FECHADO → price abaixo ou igual ao stop
                else:
                    if state == "LONG":
                        close_position(hl_symbol)
                        position_state[api_symbol] = None
                        #print(f"{api_symbol}: ⛔ ANTIGO STOP ATINGINDO.")

                    else:
                        print(f"{api_symbol}: ENTRADA BLOQUEADA | price= {price} | stop={stop} | motivo=stop inválido")
                        send_telegram(f"{api_symbol}: ENTRADA BLOQUEADA | price= {price} | stop={stop} | motivo=stop inválido")
                       

            # SELL
            elif signal == 0:
                side = "SHORT"
                # INTERVALO ABERTO → price abaixo do stop
                if price < stop:
                    if state is None:
                        size = calculate_size(price, len(symbols), alavancagem, risk_factor)
                        if size is None:
                            continue
                        
                        # Formatação Precisa da API → HL
                        sz_decimals, px_decimals = get_asset_precision(hl_symbol)
                        price = float(f"{price:.{px_decimals}f}")
                        stop  = float(f"{stop:.{px_decimals}f}")
                        size  = float(f"{size:.{sz_decimals}f}")

                        open_position(hl_symbol, side, size, leverage=alavancagem)
                        print(f"🔴 {api_symbol}: VENDA aberta {size} lote | price={price} | stop={stop}")
                        send_telegram(f"🔴 {api_symbol}:  Venda aberta | vol={size} lote | price={price} | stop={stop}")
                        time.sleep(0.5)

                        ensure_stop(hl_symbol, stop, api_symbol)

                        position_state[api_symbol] = "SHORT"
                        

                    elif state == "SHORT":
                        # Formatação Precisa da API → HL
                        size = calculate_size(price, len(symbols), alavancagem, risk_factor)
                        if size is None:
                            continue

                        sz_decimals, px_decimals = get_asset_precision(hl_symbol)
                        price = float(f"{price:.{px_decimals}f}")
                        stop  = float(f"{stop:.{px_decimals}f}")
                        size  = float(f"{size:.{sz_decimals}f}")

                        ensure_stop(hl_symbol, stop, api_symbol)
                        #print(f"{api_symbol}: Já estamos vendidos, aguardando próximo sinal.")
                        pass

                    elif state == "LONG":
                        close_position(hl_symbol)
                        time.sleep(0.5)

                        size = calculate_size(price, len(symbols), alavancagem, risk_factor)
                        if size is None:
                            continue

                        # Formatação Precisa da API → HL
                        sz_decimals, px_decimals = get_asset_precision(hl_symbol)
                        price = float(f"{price:.{px_decimals}f}")
                        stop  = float(f"{stop:.{px_decimals}f}")
                        size  = float(f"{size:.{sz_decimals}f}")
                        
                        open_position(hl_symbol, side, size, leverage=alavancagem)
                        time.sleep(0.5)
                        print(f"{api_symbol}: 🔁 REVERSÃO | LONG → SHORT | vol={size} | price={price} | stop={stop}.")
                        send_telegram(f"{api_symbol}: 🔁 REVERSÃO | LONG → SHORT | vol={size} | price={price} | stop={stop}.")
                        
                        ensure_stop(hl_symbol, stop, api_symbol)
                        
                        position_state[api_symbol] = "SHORT"
                        


                # INTERVALO FECHADO → price acima ou igual ao stop
                else:
                    if state == "SHORT":
                        close_position(hl_symbol)
                        position_state[api_symbol] = None
                        #print(f"{api_symbol}: Stoploss acionado. Posição de Venda Fechada.")

                    else:
                        print(f"{api_symbol}: ENTRADA BLOQUEADA | price= {price} | stop={stop} | motivo=stop inválido")
                        send_telegram(f"{api_symbol}: ENTRADA BLOQUEADA | price= {price} | stop={stop} | motivo=stop inválido")



            # NEUTRO
            elif signal == 0.5:
                if state is not None:
                    close_position(hl_symbol)
                    position_state[api_symbol] = None
                    print(f"{api_symbol}: ⚪ NEUTRO | posição encerrada.")
                       
# ==============================
# EXECUÇÃO
# ==============================
if __name__ == "__main__":
    ativos = ["SOLUSDT", "ETHUSDT", "BTCUSDT", "XRPUSDT"]
    alavancagem = (2)
    risk_factor = (0.95)
    timeframe = "4h"
        
    run_trading(ativos, alavancagem=alavancagem, risk_factor = risk_factor, timeframe=timeframe)
