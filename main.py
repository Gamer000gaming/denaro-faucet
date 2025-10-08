import time
import sqlite3
from decimal import Decimal
from fastapi import FastAPI, Request, HTTPException, Form
from starlette.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
import requests
from fastecdsa import keys, curve
import sys

# Project imports
sys.path.insert(0, "/path/to/folder/with/denaro/") #For example /home/<user>/denaro (the root of the repo)
from denaro.constants import CURVE
from denaro.transactions import Transaction, TransactionOutput, TransactionInput
from denaro.wallet.utils import string_to_bytes
from denaro.helpers import point_to_string, sha256, string_to_point

NODE_URL = "<NODE_URL>"
FAUCET_DB = "faucet.db"
FAUCET_PRIVATE_KEY = 0x<PRIVATE_KEY>
FAUCET_ADDRESS = point_to_string(keys.get_public_key(FAUCET_PRIVATE_KEY, curve.P256))
FAUCET_AMOUNT = Decimal("1.0")
FAUCET_COOLDOWN = 86400  # 1 day between claims

app = FastAPI(title="Denaro Faucet", description="Simple crypto faucet powered by Denaro", version="1.0")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")


def init_db():
    con = sqlite3.connect(FAUCET_DB)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            ip TEXT PRIMARY KEY,
            last_claim INTEGER
        )
    """)
    con.commit()
    con.close()


def can_claim(ip: str):
    con = sqlite3.connect(FAUCET_DB)
    cur = con.cursor()
    cur.execute("SELECT last_claim FROM claims WHERE ip = ?", (ip,))
    row = cur.fetchone()
    now = int(time.time())
    if row:
        last_claim = row[0]
        if now - last_claim < FAUCET_COOLDOWN:
            return False, FAUCET_COOLDOWN - (now - last_claim)
    return True, 0


def record_claim(ip: str):
    con = sqlite3.connect(FAUCET_DB)
    cur = con.cursor()
    cur.execute("REPLACE INTO claims (ip, last_claim) VALUES (?, ?)", (ip, int(time.time())))
    con.commit()
    con.close()


def get_address_info(address: str):
    r = requests.get(
        f"{NODE_URL}/get_address_info",
        params={'address': address, 'transactions_count_limit': 0, 'show_pending': True}
    )
    result = r.json()['result']
    inputs = []
    for out in result['spendable_outputs']:
        tx_input = TransactionInput(out['tx_hash'], out['index'])
        tx_input.amount = Decimal(str(out['amount']))
        tx_input.public_key = string_to_point(address)
        tx_input.private_key = FAUCET_PRIVATE_KEY
        inputs.append(tx_input)
    balance = Decimal(result['balance'])
    return balance, inputs


def create_transaction(receiver: str, amount: Decimal):
    balance, inputs = get_address_info(FAUCET_ADDRESS)
    if balance < amount:
        raise Exception("Faucet balance too low.")
    total = Decimal("0")
    used_inputs = []
    for i in inputs:
        used_inputs.append(i)
        total += i.amount
        if total >= amount:
            break
    tx = Transaction(used_inputs, [TransactionOutput(receiver, amount=amount)])
    if total > amount:
        tx.outputs.append(TransactionOutput(FAUCET_ADDRESS, total - amount))
    tx.sign([FAUCET_PRIVATE_KEY])
    requests.post(f"{NODE_URL}/submit_tx", json={'tx_hex': tx.hex()}, timeout=10)
    return sha256(tx.hex())


@app.get("/", response_class=HTMLResponse)
async def index():
    return f"""
    <html>
        <head>
            <title>Denaro Faucet</title>
            <link rel="stylesheet" href="/assets/style.css">
        </head>
        <body>
            <h1>Denaro Faucet</h1>
            <p>Faucet Address:<br><b>{FAUCET_ADDRESS}</b></p>
            <p>Please donate to keep the faucet alive!</p>
            <form action="/claim" method="post">
                <input type="text" name="address" placeholder="Your Denaro address"
                    style="width:400px;padding:10px;" required><br><br>
                <button type="submit" style="padding:10px 20px;">Claim {FAUCET_AMOUNT} Denaro</button>
            </form>
        </body>
    </html>
    """


@app.post("/claim", response_class=HTMLResponse)
async def claim(request: Request, address: str = Form(...)):
    ip = request.client.host
    allowed, wait_time = can_claim(ip)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Wait {wait_time//60} minutes before claiming again.")
    try:
        tx_hash = create_transaction(address, FAUCET_AMOUNT)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    record_claim(ip)
    return f"""
    <html>
        <head>
            <title>Faucet Claim</title>
            <link rel="stylesheet" href="/assets/style.css">
        </head>
        <body>
            <h2>Transaction Sent!</h2>
            <p>To: {address}</p>
            <p>Amount: {FAUCET_AMOUNT} Denaro</p>
            <p>Transaction Hash:<br><b>{tx_hash}</b></p>
            <a href="/">← Back to Faucet</a>
        </body>
    </html>
    """


if __name__ == "__main__":
    init_db()
    print(f"Faucet running with address {FAUCET_ADDRESS}")
    uvicorn.run(app, host="0.0.0.0", port=3333)
