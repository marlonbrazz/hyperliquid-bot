import os
from dotenv import load_dotenv
from hyperliquid.info import Info

load_dotenv()

address = os.getenv("WALLET_ADDRESS")

info = Info("https://api.hyperliquid.xyz", skip_ws=True)

state = info.user_state(address)

print(state)
