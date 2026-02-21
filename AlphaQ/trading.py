import os
import time
from datetime import datetime
import requests
import MetaTrader5 as mt5
from dotenv import load_dotenv

# ==============================
# CONFIGURAÇÕES
# ==============================
load_dotenv()

# --- API ---
API_KEY = os.getenv("API_KEY")
if API_KEY is None:
    raise ValueError("API_KEY não definido no .env")

# --- MT5 ---
MT5_LOGIN = os.getenv("MT5_LOGIN")
if MT5_LOGIN is None:
    raise ValueError("MT5_LOGIN não definido no .env")
MT5_LOGIN = int(MT5_LOGIN)

MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==============================
# ATIVOS
# ==============================
symbol_map = {
    "XAUUSD": "XAUUSD_",
    "EURUSD": "EURUSD_",
}

def get_mt5_symbol(api_symbol):
    return symbol_map.get(api_symbol, api_symbol)

# ==============================
# FUNÇÕES AUXILIARES
# ==============================
def initialize_mt5():
    if not mt5.initialize():
        print("Erro ao inicializar MT5:", mt5.last_error())
        quit()

def get_signal(symbol, timeframe="4h"):
    payload = {
        "model": "forex",
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

def get_current_position(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if positions:
        return positions[0].type  # 0 = BUY | 1 = SELL
    return None

def close_position(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return

    for pos in positions:
        tick = mt5.symbol_info_tick(symbol)
        price = tick.bid if pos.type == 0 else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "position": pos.ticket,
            "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
            "price": price,
            "deviation": 20,
            "magic": 123456,
            "comment": "Auto Close"
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print("Erro ao fechar posição:", result)

def open_position(symbol, signal, volume, stop):
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print(f"Símbolo não encontrado: {symbol}")
        return

    order_type = mt5.ORDER_TYPE_BUY if signal == 1 else mt5.ORDER_TYPE_SELL
    price = tick.ask if signal == 1 else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": stop,
        "deviation": 20,
        "magic": 123456,
        "comment": "API Signal Trade"
    }

    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print("Erro ao abrir posição:", result)
    else:
        side = "BUY" if signal == 1 else "SELL"
        print(f"{side} aberto em {symbol} @ {price}")

def calculate_volume(symbol, total_symbols, alavancagem, risk_factor):
    account = mt5.account_info()
    if account is None:
        print("Erro ao obter informações da conta")
        return None

    balance = account.balance

    balance_alocado = balance * alavancagem

    capital_per_symbol = (balance_alocado / total_symbols) * risk_factor

    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)

    if tick is None or info is None:
        print(f"Erro ao obter dados do símbolo: {symbol}")
        return None

    price = tick.ask

    raw_volume = capital_per_symbol / price

    # Ajuste para regras do ativo
    volume = max(info.volume_min, raw_volume)
    volume = min(volume, info.volume_max)

    # Ajuste para step
    volume = round(volume / info.volume_step) * info.volume_step

    return round(volume, 2)

def update_stop(symbol, new_stop):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return False

    pos = positions[0]

    # Só atualiza se mudou de verdade
    if pos.sl and abs(pos.sl - new_stop) < 0.00001:
        return False

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": pos.ticket,
        "sl": new_stop,
        "tp": pos.tp
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        return True
    else:
        return False

def send_start_log(symbols, timeframe, alavancagem):
    account = mt5.account_info()
    capital = account.margin_free if account else 0

    lines = []
    lines.append("🤖 BOT INICIADO\n")
    lines.append("Ativos:")

    for api_symbol in symbols:
        mt5_symbol = get_mt5_symbol(api_symbol)
        pos = get_current_position(mt5_symbol)

        if pos is None:
            status = "AGUARDANDO"
        elif pos == 0:
            status = "COMPRA"
        else:
            status = "VENDA"

        lines.append(f"{api_symbol}: {status}")

    lines.append(f"\nCapital disponível para novas ordens: ${capital:.0f}")
    lines.append(f"Alavancagem: {alavancagem}x")
    lines.append(f"Timeframe: {timeframe}\n")

    msg = "\n".join(lines)
    print(msg)

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
    now = now or datetime.now()
    tf_minutes = timeframe_to_minutes(timeframe)

    # Define o último marco das 19:00
    anchor = now.replace(hour=19, minute=0, second=0, microsecond=0)
    if now < anchor:
        anchor = anchor.replace(day=anchor.day - 1)

    # minutos desde o marco
    elapsed_minutes = int((now - anchor).total_seconds() // 60)

    # qual bloco atual estamos
    slot_index = elapsed_minutes // tf_minutes

    return slot_index


def should_check_api(timeframe):
    global last_api_check, force_api_check

    now = datetime.now()
    tf_minutes = timeframe_to_minutes(timeframe)
    entry_delay = get_entry_delay_minutes(timeframe)

    # Marco base das 19:00
    anchor = now.replace(hour=19, minute=0, second=0, microsecond=0)
    if now < anchor:
        anchor = anchor.replace(day=anchor.day - 1)

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


    initialize_mt5()
    print("Bot iniciado...")
    send_start_log(symbols, timeframe, alavancagem)
    position_state = {}
    for s in symbols:
        position_state[s] = None  # LONG | SHORT | None

    while True:
        #send_start_log(symbols, timeframe, alavancagem)
        if not should_check_api(timeframe):
            time.sleep(30)
            continue
        
        for api_symbol in symbols:
            mt5_symbol = get_mt5_symbol(api_symbol)

            # 🔵 SINCRONIZA COM MT5 REAL
            real_pos = get_current_position(mt5_symbol)

            if real_pos is None:
                position_state[api_symbol] = None
            elif real_pos == 0:
                position_state[api_symbol] = "LONG"
            elif real_pos == 1:
                position_state[api_symbol] = "SHORT"

            # chamada segura da API
            result = get_signal(api_symbol, timeframe)
            if result is None:
                continue
            
            signal, stop, price = result

            state = position_state[api_symbol]
          
            # Se já existe posição aberta, sincroniza o stop com a API
            if state is not None:
               updated = update_stop(mt5_symbol, stop)
               if updated:
                   print(f"O stop foi atualizado para {stop}")
                   send_telegram(f"{api_symbol}| STOP atualizado → {stop}")
                        

            # BUY
            if signal == 1:

                if price > stop:
                    if state is None:
                        volume = calculate_volume(mt5_symbol, len(symbols), alavancagem, risk_factor)
                        if volume is None:
                            continue

                        open_position(mt5_symbol, signal, volume, stop)
                        position_state[api_symbol] = "LONG"

                        print(f"🟢 {api_symbol}:   Compra aberta | vol={volume} | price={price} | stop={stop}")
                        send_telegram(f"🟢 {api_symbol}:   Compra aberta | vol={volume} lote | price={price} | stop={stop}")

                    elif state == "LONG":
                        #print(f"{api_symbol}: Já estamos comprados, aguardando próximo sinal.")
                        pass

                    elif state == "SHORT":
                        close_position(mt5_symbol)
                        time.sleep(0.5)

                        volume = calculate_volume(mt5_symbol, len(symbols), alavancagem, risk_factor)
                        if volume is None:
                            continue

                        open_position(mt5_symbol, signal, volume, stop)
                        position_state[api_symbol] = "LONG"

                        print(f"{api_symbol}: 🔁 REVERSÃO | SHORT → LONG | vol={volume} | price={price} | stop={stop}.")
                        send_telegram(f"{api_symbol}: 🔁 REVERSÃO | SHORT → LONG | vol={volume} | price={price} | stop={stop}.")

               # INTERVALO FECHADO → price abaixo ou igual ao stop
                else:
                    if state == "LONG":
                        close_position(mt5_symbol)
                        position_state[api_symbol] = None
                        #print(f"{api_symbol}: ⛔ ANTIGO STOP ATINGINDO.")

                    else:
                        print(f"{api_symbol}: ENTRADA BLOQUEADA | price= {price} | stop={stop} | motivo=stop inválido")
                        send_telegram(f"{api_symbol}: ENTRADA BLOQUEADA | price= {price} | stop={stop} | motivo=stop inválido")
                       

            # SELL
            elif signal == 0:

                # INTERVALO ABERTO → price abaixo do stop
                if price < stop:
                    if state is None:
                        volume = calculate_volume(mt5_symbol, len(symbols), alavancagem, risk_factor)
                        if volume is None:
                            continue

                        open_position(mt5_symbol, signal, volume, stop)
                        position_state[api_symbol] = "SHORT"
                        print(f"🔴 {api_symbol}: VENDA aberta {volume} lote | price={price} | stop={stop}")
                        send_telegram(f"🔴 {api_symbol}:  Venda aberta | vol={volume} lote | price={price} | stop={stop}")

                    elif state == "SHORT":
                        #print(f"{api_symbol}: Já estamos vendidos, aguardando próximo sinal.")
                        pass

                    elif state == "LONG":
                        close_position(mt5_symbol)
                        time.sleep(0.5)

                        volume = calculate_volume(mt5_symbol, len(symbols), alavancagem, risk_factor)
                        if volume is None:
                            continue

                        open_position(mt5_symbol, signal, volume, stop)
                        position_state[api_symbol] = "SHORT"
                        print(f"{api_symbol}: 🔁 REVERSÃO | LONG → SHORT | vol={volume} | price={price} | stop={stop}.")
                        send_telegram(f"{api_symbol}: 🔁 REVERSÃO | LONG → SHORT | vol={volume} | price={price} | stop={stop}.")


                # INTERVALO FECHADO → price acima ou igual ao stop
                else:
                    if state == "SHORT":
                        close_position(mt5_symbol)
                        position_state[api_symbol] = None
                        #print(f"{api_symbol}: Stoploss acionado. Posição de Venda Fechada.")

                    else:
                        print(f"{api_symbol}: ENTRADA BLOQUEADA | price= {price} | stop={stop} | motivo=stop inválido")
                        send_telegram(f"{api_symbol}: ENTRADA BLOQUEADA | price= {price} | stop={stop} | motivo=stop inválido")



            # NEUTRO
            elif signal == 0.5:
                if state is not None:
                    close_position(mt5_symbol)
                    position_state[api_symbol] = None
                    print(f"{api_symbol}: ⚪ NEUTRO | posição encerrada.")

# ==============================
# EXECUÇÃO
# ==============================
if __name__ == "__main__":
    ativos = ["XAUUSD", "EURUSD"]
    alavancagem = (2)
    risk_factor = (0.01)
    timeframe = "4h"
        
    run_trading(ativos, alavancagem=alavancagem, risk_factor = risk_factor, timeframe=timeframe)
