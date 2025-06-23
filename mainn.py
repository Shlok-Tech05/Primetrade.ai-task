from keys import api, secret
from binance.um_futures import UMFutures
import ta
import pandas as pd
from time import sleep
from binance.error import ClientError
import logging
import argparse

# Setup logging
logging.basicConfig(filename='bot.log', level=logging.INFO,
                    format='%(asctime)s %(levelname)s:%(message)s')

client = UMFutures(key=api, secret=secret)

tp = 0.012
sl = 0.009
volume = 10
leverage = 10
type = 'ISOLATED'
qty = 100

# CLI parser
parser = argparse.ArgumentParser(description='Binance Futures Trading Bot')
parser.add_argument('--strategy', type=str, default='rsi', choices=['rsi', 'macd', 'ema'], help='Strategy to use')
args = parser.parse_args()


def get_balance_usdt():
    try:
        response = client.balance(recvWindow=6000)
        for elem in response:
            if elem['asset'] == 'USDT':
                return float(elem['balance'])
    except ClientError as error:
        logging.error("API Error: %s", error.error_message)


def get_tickers_usdt():
    tickers = []
    try:
        resp = client.ticker_price()
        for elem in resp:
            if 'USDT' in elem['symbol']:
                tickers.append(elem['symbol'])
    except ClientError as error:
        logging.error("Error fetching tickers: %s", error.error_message)
    return tickers


def klines(symbol):
    try:
        resp = pd.DataFrame(client.klines(symbol, '15m'))
        resp = resp.iloc[:, :6]
        resp.columns = ['Time', 'Open', 'High', 'Low', 'Close', 'Volume']
        resp = resp.set_index('Time')
        resp.index = pd.to_datetime(resp.index, unit='ms')
        resp = resp.astype(float)
        return resp
    except ClientError as error:
        logging.error("Klines error: %s", error.error_message)


def set_leverage(symbol, level):
    try:
        response = client.change_leverage(symbol=symbol, leverage=level, recvWindow=6000)
        logging.info("Set leverage for %s: %s", symbol, response)
    except ClientError as error:
        logging.error("Leverage error: %s", error.error_message)


def set_mode(symbol, type):
    try:
        response = client.change_margin_type(symbol=symbol, marginType=type, recvWindow=6000)
        logging.info("Set margin mode for %s: %s", symbol, response)
    except ClientError as error:
        logging.error("Margin mode error: %s", error.error_message)


def get_price_precision(symbol):
    resp = client.exchange_info()['symbols']
    for elem in resp:
        if elem['symbol'] == symbol:
            return elem['pricePrecision']


def get_qty_precision(symbol):
    resp = client.exchange_info()['symbols']
    for elem in resp:
        if elem['symbol'] == symbol:
            return elem['quantityPrecision']


def open_order(symbol, side):
    price = float(client.ticker_price(symbol)['price'])
    qty_precision = get_qty_precision(symbol)
    price_precision = get_price_precision(symbol)
    qty = round(volume / price, qty_precision)
    
    try:
        order_side = 'BUY' if side == 'buy' else 'SELL'
        resp1 = client.new_order(symbol=symbol, side=order_side, type='LIMIT', quantity=qty, timeInForce='GTC', price=price)
        logging.info("Placed limit %s order: %s", side, resp1)
        sleep(2)

        sl_price = round(price * (1 - sl) if side == 'buy' else price * (1 + sl), price_precision)
        tp_price = round(price * (1 + tp) if side == 'buy' else price * (1 - tp), price_precision)

        stop_side = 'SELL' if side == 'buy' else 'BUY'
        resp2 = client.new_order(symbol=symbol, side=stop_side, type='STOP_MARKET', quantity=qty, timeInForce='GTC', stopPrice=sl_price)
        logging.info("Placed SL order: %s", resp2)
        sleep(2)

        resp3 = client.new_order(symbol=symbol, side=stop_side, type='TAKE_PROFIT_MARKET', quantity=qty, timeInForce='GTC', stopPrice=tp_price)
        logging.info("Placed TP order: %s", resp3)
    except ClientError as error:
        logging.error("Order error for %s: %s", symbol, error.error_message)


def get_pos():
    try:
        resp = client.get_position_risk()
        return [elem['symbol'] for elem in resp if float(elem['positionAmt']) != 0]
    except ClientError as error:
        logging.error("Position fetch error: %s", error.error_message)
        return []


def check_orders():
    try:
        response = client.get_orders(recvWindow=6000)
        return [elem['symbol'] for elem in response]
    except ClientError as error:
        logging.error("Order check error: %s", error.error_message)
        return []


def close_open_orders(symbol):
    try:
        response = client.cancel_open_orders(symbol=symbol, recvWindow=6000)
        logging.info("Closed open orders for %s: %s", symbol, response)
    except ClientError as error:
        logging.error("Close order error for %s: %s", symbol, error.error_message)

# Strategy Definitions

def rsi_signal(symbol):
    kl = klines(symbol)
    rsi = ta.momentum.RSIIndicator(kl.Close).rsi()
    if rsi.iloc[-2] < 30 and rsi.iloc[-1] > 30:
        return 'up'
    if rsi.iloc[-2] > 70 and rsi.iloc[-1] < 70:
        return 'down'
    return 'none'


def macd_ema(symbol):
    kl = klines(symbol)
    macd = ta.trend.macd_diff(kl.Close)
    ema = ta.trend.ema_indicator(kl.Close, window=200)
    if macd.iloc[-3] < 0 < macd.iloc[-1] and ema.iloc[-1] < kl.Close.iloc[-1]:
        return 'up'
    if macd.iloc[-3] > 0 > macd.iloc[-1] and ema.iloc[-1] > kl.Close.iloc[-1]:
        return 'down'
    return 'none'


def ema200_50(symbol):
    kl = klines(symbol)
    ema200 = ta.trend.ema_indicator(kl.Close, window=200)
    ema50 = ta.trend.ema_indicator(kl.Close, window=50)
    if ema50.iloc[-3] < ema200.iloc[-3] and ema50.iloc[-1] > ema200.iloc[-1]:
        return 'up'
    if ema50.iloc[-3] > ema200.iloc[-3] and ema50.iloc[-1] < ema200.iloc[-1]:
        return 'down'
    return 'none'


strategy_map = {
    'rsi': rsi_signal,
    'macd': macd_ema,
    'ema': ema200_50
}

symbols = get_tickers_usdt()

while True:
    balance = get_balance_usdt()
    sleep(1)
    if balance is None:
        logging.warning("Cannot fetch balance.")
        continue
    logging.info("Balance: %.2f USDT", balance)

    pos = get_pos()
    logging.info("Open positions: %s", pos)
    
    ord = check_orders()
    for sym in ord:
        if sym not in pos:
            close_open_orders(sym)

    if len(pos) < qty:
        for sym in symbols:
            if sym == 'USDCUSDT' or sym in pos or sym in ord:
                continue
            signal = strategy_map[args.strategy](sym)
            if signal in ['up', 'down']:
                set_mode(sym, type)
                sleep(1)
                set_leverage(sym, leverage)
                sleep(1)
                open_order(sym, 'buy' if signal == 'up' else 'sell')
                sleep(10)
    sleep(180)
