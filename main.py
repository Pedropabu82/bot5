import pandas as pd
import pandas_ta as ta
import logging
import traceback
from datetime import datetime, timedelta
import ccxt.async_support as ccxt
import asyncio
import json
import os

# Load configuration from JSON
config_path = 'config.json'
if os.path.exists(config_path):
    with open(config_path) as f:
        cfg = json.load(f)
    strategy_cfg = cfg.get('strategy', {})
    timeframes = cfg.get('timeframes', ['15m', '30m', '1h', '2h', '4h', '6h'])
    symbols = cfg.get('symbols', ['BTC/USDT'])
else:
    # Defaults
    strategy_cfg = {
        'leverage': 10,
        'fixed_size_usd': 50,
        'sl_pct': 0.025,
        'tp_pct': 0.07,
        'ob_level': 20,
        'os_level': -20,
        'os_level3': -75,
        'wt_div_ob': 45,
        'wt_div_os': -65,
        'commission_pct': 0.0004  # 0.04% per side
    }
    timeframes = ['15m', '30m', '1h', '2h', '4h', '6h']
    symbols = ['BTC/USDT']

# Extract parameters
leverage = strategy_cfg['leverage']
fixed_position_size_usd = strategy_cfg['fixed_size_usd']
sl_pct = strategy_cfg['sl_pct']
tp_pct = strategy_cfg['tp_pct']
ob_level = strategy_cfg['ob_level']
os_level = strategy_cfg['os_level']
os_level3 = strategy_cfg['os_level3']
wt_div_ob = strategy_cfg['wt_div_ob']
wt_div_os = strategy_cfg['wt_div_os']
commission_pct = strategy_cfg.get('commission_pct', 0.0004)

# WaveTrend and MFI/RSI parameters
wt_channel_len = 9
wt_average_len = 12
wt_ma_len = 3
rsi_length = 14
rsi_oversold = 30
mfi_period = 60
mfi_multiplier = 150

# Setup logging
datetime_fmt = '%Y-%m-%d %H:%M:%S'
logging.basicConfig(filename='trades.log', level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s', datefmt=datetime_fmt)
print(f"Starting bot at {datetime.now().strftime(datetime_fmt)}\n")

# Utility: retry wrapper
async def retry(coro, *args, retries=3, delay=2, **kwargs):
    for i in range(retries):
        try:
            return await coro(*args, **kwargs)
        except Exception as e:
            if i < retries - 1:
                logging.warning(f"Retry {i+1}/{retries} failed: {str(e)}")
                await asyncio.sleep(delay)
            else:
                raise

# Utility: round price to tick size
def round_to_tick(price, tick_size):
    return round(price / tick_size) * tick_size

# Binance client using CCXT
class BinanceClient:
    def __init__(self, api_key, api_secret, sandbox_mode=True):
        self.exchange = ccxt.binanceusdm({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
        })
        self.exchange.set_sandbox_mode(sandbox_mode)
        self.markets = None
        # Separate instance for live price data
        self.live_exchange = ccxt.binanceusdm({'enableRateLimit': True})
        self.live_exchange.set_sandbox_mode(False)

    async def load_markets(self):
        if not self.markets:
            self.markets = await retry(self.exchange.load_markets)

    async def get_market_info(self, symbol):
        await self.load_markets()
        market = self.markets.get(symbol)
        if not market:
            raise ValueError(f"Market {symbol} not found")
        return {
            'price_precision': market['precision']['price'],
            'quantity_precision': market['precision']['amount'],
            'tick_size': market['limits']['price']['min'],
            'min_quantity': market['limits']['amount']['min']
        }

    async def fetch_balance(self):
        try:
            bal = await retry(self.exchange.fetch_balance)
            return bal.get('total', {}).get('USDT', 0.0)
        except Exception as e:
            logging.error(f"Failed to fetch balance: {str(e)}")
            return 0.0

    async def fetch_ohlcv(self, symbol, timeframe='4h', limit=100):
        try:
            # Use live exchange with klines endpoint
            symbol_formatted = symbol.replace('/', '')  # e.g., BTC/USDT -> BTCUSDT
            klines = await retry(self.live_exchange.fapiPublicGetKlines, params={
                'symbol': symbol_formatted,
                'interval': timeframe,
                'limit': limit
            })
            # Process klines into DataFrame with explicit float conversion
            data = [{
                'timestamp': pd.to_datetime(int(kline[0]), unit='ms'),
                'open': float(kline[1]),
                'high': float(kline[2]),
                'low': float(kline[3]),
                'close': float(kline[4]),
                'volume': float(kline[5])
            } for kline in klines]
            df = pd.DataFrame(data)
            # Validate price data
            latest_price = df['close'].iloc[-1]
            if latest_price < 50000:  # Threshold for BTC/USDT in 2025
                logging.warning(f"Unrealistic price detected: {latest_price} for {symbol} on {timeframe}")
            return df
        except Exception as e:
            logging.error(f"Failed to fetch OHLCV for {symbol} on {timeframe}: {str(e)}")
            return pd.DataFrame()

    async def get_position_amt(self, symbol):
        try:
            symbol_formatted = symbol.replace('/', '')  # e.g., BTC/USDT -> BTCUSDT
            positions = await retry(self.exchange.fetch_positions, [symbol_formatted])
            for p in positions:
                if p['symbol'] == symbol_formatted:
                    return float(p['contracts']) if p['contracts'] else 0.0
            return 0.0
        except Exception as e:
            logging.error(f"Failed to fetch position for {symbol}: {str(e)}")
            return 0.0

    async def confirm_position(self, symbol):
        pos_amt = await self.get_position_amt(symbol)
        return pos_amt > 0

    async def create_market_order(self, symbol, side, amount):
        try:
            market_info = await self.get_market_info(symbol)
            amount = round(amount, market_info['quantity_precision'])
            if amount < market_info['min_quantity']:
                logging.error(f"Quantity {amount} below minimum {market_info['min_quantity']} for {symbol}")
                return None
            logging.info(f"Sending {side.upper()} market order: {amount} {symbol}")
            order = await retry(self.exchange.create_order, symbol, 'market', side, amount)
            return order
        except ccxt.InvalidOrder as e:
            logging.error(f"Invalid market order for {symbol}: {str(e)}")
            return None
        except ccxt.InsufficientFunds as e:
            logging.error(f"Insufficient funds for {symbol}: {str(e)}")
            return None

    async def create_stop_loss(self, symbol, side, quantity, stop_price):
        try:
            market_info = await self.get_market_info(symbol)
            stop_price = round_to_tick(stop_price, market_info['tick_size'])
            quantity = round(quantity, market_info['quantity_precision'])
            opposite = 'sell' if side == 'buy' else 'buy'
            logging.info(f"Creating STOP_MARKET @ {stop_price:.2f} for {symbol}, qty={quantity}")
            print(f"{symbol}: Creating SL at {stop_price:.2f}")
            params = {
                'stopPrice': stop_price,
                'reduceOnly': True,
                'timeInForce': 'GTC'
            }
            await retry(self.exchange.create_order, symbol, 'stop_market', opposite, quantity, None, params)
        except ccxt.InvalidOrder as e:
            logging.error(f"Failed to create SL for {symbol}: Invalid order - {str(e)}")
        except ccxt.NetworkError as e:
            logging.error(f"Failed to create SL for {symbol}: Network error - {str(e)}")
        except Exception as e:
            logging.error(f"Failed to create SL for {symbol}: {str(e)}")

    async def create_take_profit(self, symbol, side, quantity, tp_price):
        try:
            market_info = await self.get_market_info(symbol)
            tp_price = round_to_tick(tp_price, market_info['tick_size'])
            quantity = round(quantity, market_info['quantity_precision'])
            opposite = 'sell' if side == 'buy' else 'buy'
            # Cancel existing SL/TP orders
            open_orders = await retry(self.exchange.fetch_open_orders, symbol)
            for o in open_orders:
                if o['type'] in ['stop_market', 'take_profit_market'] and o['symbol'] == symbol.replace('/', ''):
                    await retry(self.exchange.cancel_order, o['id'], symbol)
                    logging.info(f"Cancelled existing order {o['id']} for {symbol}")
            logging.info(f"Creating TAKE_PROFIT_MARKET @ {tp_price:.2f} for {symbol}, qty={quantity}")
            print(f"{symbol}: Creating TP at {tp_price:.2f}")
            params = {
                'stopPrice': tp_price,
                'reduceOnly': True,
                'timeInForce': 'GTC'
            }
            await retry(self.exchange.create_order, symbol, 'take_profit_market', opposite, quantity, None, params)
        except ccxt.InvalidOrder as e:
            logging.error(f"Failed to create TP for {symbol}: Invalid order - {str(e)}")
        except ccxt.NetworkError as e:
            logging.error(f"Failed to create TP for {symbol}: Network error - {str(e)}")
        except Exception as e:
            logging.error(f"Failed to create TP for {symbol}: {str(e)}")

    async def close(self):
        await self.exchange.close()
        await self.live_exchange.close()

# Strategy functions
def calculate_wavetrend(df, channel_len=9, avg_len=12, ma_len=3):
    df['hlc3'] = (df['high'] + df['low'] + df['close']) / 3
    src = df['hlc3']
    esa = ta.ema(src, length=channel_len)
    de = ta.ema(abs(src - esa), length=channel_len)
    ci = (src - esa) / (0.015 * de)
    wt1 = ta.ema(ci, length=avg_len)
    wt2 = ta.sma(wt1, length=ma_len)
    wt_vwap = wt1 - wt2
    return wt1, wt2, wt_vwap

def find_divergences(series, price, ob_level, os_level):
    fractal_top = (
        (series.shift(4) < series.shift(2))
        & (series.shift(3) < series.shift(2))
        & (series.shift(2) > series.shift(1))
        & (series.shift(2) > series)
    )
    fractal_bot = (
        (series.shift(4) > series.shift(2))
        & (series.shift(3) > series.shift(2))
        & (series.shift(2) < series.shift(1))
        & (series.shift(2) < series)
    )
    bear_div = (
        fractal_top
        & (price.shift(2) > price.shift(4))
        & (series.shift(2) < series.shift(4))
        & (series.shift(2) >= ob_level)
    )
    bull_div = (
        fractal_bot
        & (price.shift(2) < price.shift(4))
        & (series.shift(2) > series.shift(4))
        & (series.shift(2) <= os_level)
    )
    return bear_div, bull_div

async def set_sl(symbol):
    for _ in range(2):
        if not last_trade.get(symbol) or not last_trade[symbol].get('quantity'):
            logging.error(f"No trade data for {symbol} to set SL")
            return
        try:
            entry_price = last_trade[symbol]['entry_price']
            side = last_trade[symbol]['side']
            qty = last_trade[symbol]['quantity']
            tp, sl = calculate_tp_sl(symbol)
            # Validate SL price
            if abs(sl - entry_price) / entry_price > 0.05:
                sl = entry_price * (0.95 if side == 'buy' else 1.05)
                logging.warning(f"Adjusted SL for {symbol} to {sl:.2f} due to excessive distance")
            if sl < 50000:  # Ensure SL is realistic
                logging.error(f"Invalid SL price {sl:.2f} for {symbol}, skipping")
                return
            await client.create_stop_loss(symbol, side, qty, sl)
            return
        except Exception as e:
            logging.error(f"Failed to set SL for {symbol}: {str(e)}")
            await asyncio.sleep(2)

async def set_tp(symbol):
    for _ in range(2):
        if not last_trade.get(symbol) or not last_trade[symbol].get('quantity'):
            logging.error(f"No trade data for {symbol} to set TP")
            return
        try:
            entry_price = last_trade[symbol]['entry_price']
            side = last_trade[symbol]['side']
            qty = last_trade[symbol]['quantity']
            tp, sl = calculate_tp_sl(symbol)
            # Validate TP price
            if abs(tp - entry_price) / entry_price > 0.05:
                tp = entry_price * (1.05 if side == 'buy' else 0.95)
                logging.warning(f"Adjusted TP for {symbol} to {tp:.2f} due to excessive distance")
            if tp < 50000:  # Ensure TP is realistic
                logging.error(f"Invalid TP price {tp:.2f} for {symbol}, skipping")
                return
            await client.create_take_profit(symbol, side, qty, tp)
            return
        except Exception as e:
            logging.error(f"Failed to set TP for {symbol}: {str(e)}")
            await asyncio.sleep(2)

def calculate_tp_sl(symbol):
    entry = last_trade[symbol]['entry_price']
    side = last_trade[symbol]['side']
    lev = leverage
    tp = entry * (1 + tp_pct / lev) if side == 'buy' else entry * (1 - tp_pct / lev)
    sl = entry * (1 - sl_pct / lev) if side == 'buy' else entry * (1 + sl_pct / lev)
    return tp, sl

async def trading_loop():
    api_key = '932becea53220bb9244f779bde17b5c594ba0ab1eb4ceb925ec85ea9a446a6fc'
    api_secret = (
        '9a41ddf72bbab3a932a61988e588a9b83cdc2a2672c471db8f9d0c359748e9c0'
    )
    global client, last_trade
    client = BinanceClient(api_key, api_secret, sandbox_mode=True)

    # Track cooldown and open positions per symbol
    cooling_until = {symbol: None for symbol in symbols}
    position_open = {symbol: False for symbol in symbols}
    last_trade = {}  # {'entry_price', 'side', 'quantity'}

    try:
        balance = await client.fetch_balance()
        print(f"✅ Conexão verificada! Saldo disponível: {balance}\n")

        while True:
            now = datetime.now()

            for symbol in symbols:
                # Skip if cooling down
                if cooling_until[symbol] and now < cooling_until[symbol]:
                    print(f"{symbol}: Cooling down until {cooling_until[symbol]}. Skipping.")
                    continue

                # Update position status
                pos_amt = await client.get_position_amt(symbol)
                if pos_amt > 0:
                    position_open[symbol] = True
                elif pos_amt == 0 and position_open[symbol]:
                    # Position closed; compute PnL and set cooldown
                    one_min_df = await client.fetch_ohlcv(symbol, '1m', limit=1)
                    if one_min_df.empty:
                        logging.error(f"Failed to fetch exit price for {symbol}")
                        position_open[symbol] = False
                        continue
                    exit_price = one_min_df['close'].iloc[-1]
                    info = last_trade.get(symbol, {})
                    if not info:
                        logging.error(f"No trade info for closed position {symbol}")
                        position_open[symbol] = False
                        continue
                    entry_price = info['entry_price']
                    side = info['side']
                    qty = info['quantity']
                    pnl = ((exit_price - entry_price) if side == 'buy' else (entry_price - exit_price)) * qty
                    commission = (entry_price * qty + exit_price * qty) * commission_pct
                    pnl_net = pnl - commission
                    pnl_pct = (pnl_net / (entry_price * qty)) * 100
                    logging.info(
                        f"{symbol} Trade closed: side={side}, entry={entry_price}, "
                        f"exit={exit_price}, qty={qty}, PnL_net={pnl_net:.2f} USDT ({pnl_pct:.2f}%), "
                        f"Commission={commission:.2f}"
                    )
                    cooling_until[symbol] = now + timedelta(minutes=30)
                    position_open[symbol] = False
                    last_trade.pop(symbol, None)
                    print(f"{symbol}: Position closed. Cooling down until {cooling_until[symbol]}.\n")
                    continue

                # If no open position, scan timeframes for signals
                if not position_open[symbol]:
                    # Check balance before trading
                    balance = await client.fetch_balance()
                    if balance < fixed_position_size_usd:
                        logging.error(f"Insufficient balance: {balance} USDT for {symbol}")
                        continue

                    for timeframe in timeframes:
                        try:
                            df = await client.fetch_ohlcv(symbol, timeframe)
                            if df.empty:
                                logging.warning(f"No data returned for {symbol} on {timeframe}")
                                continue
                            df['rsi'] = ta.rsi(df['close'], length=rsi_length)
                            df['mfi'] = (
                                ta.mfi(df['high'], df['low'], df['close'], df['volume'], length=mfi_period)
                                * mfi_multiplier - 2.5
                            )
                            wt1, wt2, wt_vwap = calculate_wavetrend(
                                df, wt_channel_len, wt_average_len, wt_ma_len
                            )
                            df['wt1'] = wt1
                            df['wt2'] = wt2
                            df['wt_vwap'] = wt_vwap

                            wt_cross = (
                                (df['wt1'].shift(1) < df['wt2'].shift(1))
                                & (df['wt1'] > df['wt2'])
                            )
                            wt_cross_up = wt_cross & (df['wt2'] <= os_level)
                            wt_cross_down = (
                                (df['wt1'].shift(1) > df['wt2'].shift(1))
                                & (df['wt1'] < df['wt2'])
                                & (df['wt2'] >= ob_level)
                            )
                            wt_bear_div, wt_bull_div = find_divergences(
                                df['wt2'], df['close'], wt_div_ob, wt_div_os
                            )

                            last_rsi = df['rsi'].shift(2)
                            wt_gold = (
                                wt_bull_div
                                & (df['wt2'].shift(2) <= os_level3)
                                & (df['wt2'] > os_level3)
                                & (last_rsi < 30)
                            )

                            buySignal = wt_cross_up & ~wt_gold
                            sellSignal = wt_cross_down

                            # Debug prints
                            print(f"[{symbol}@{timeframe}]")
                            print(f"  Price: {df['close'].iloc[-1]}")
                            print(f"  WT2: {df['wt2'].iloc[-1]}, WT1: {df['wt1'].iloc[-1]}"
                                  f" (cross_up: {wt_cross_up.iloc[-1]}, cross_down: {wt_cross_down.iloc[-1]})")
                            print(f"  Divergências - Bull: {wt_bull_div.iloc[-1]}, Bear: {wt_bear_div.iloc[-1]}")
                            print(f"  Gold: {wt_gold.iloc[-1]}")
                            print(f"  RSI: {df['rsi'].iloc[-1]}")
                            print(f"  MFI: {df['mfi'].iloc[-1]}\n")

                            pos_amt_check = await client.get_position_amt(symbol)
                            if pos_amt_check == 0:
                                entry_price = df['close'].iloc[-1]
                                # Validate entry price
                                if entry_price < 50000:
                                    logging.warning(f"Skipping trade for {symbol} on {timeframe}: Unrealistic price {entry_price}")
                                    continue
                                market_info = await client.get_market_info(symbol)
                                quantity = round((fixed_position_size_usd * leverage) / entry_price, market_info['quantity_precision'])
                                if buySignal.iloc[-1] or buySignal.iloc[-2]:
                                    logging.info(f"🔔 {symbol} Long signal detected on {timeframe}")
                                    order = await client.create_market_order(symbol, 'buy', quantity)
                                    if order and order.get('status') == 'closed':
                                        await asyncio.sleep(1)  # Wait for position to register
                                        if await client.confirm_position(symbol):
                                            last_trade[symbol] = {
                                                'entry_price': entry_price,
                                                'side': 'buy',
                                                'quantity': quantity
                                            }
                                            position_open[symbol] = True
                                            await set_sl(symbol)
                                            await set_tp(symbol)
                                            break
                                        else:
                                            logging.error(f"Failed to confirm position for {symbol} after buy order")
                                            continue
                                    else:
                                        logging.error(f"Failed to create buy order for {symbol}")
                                        continue

                                elif sellSignal.iloc[-1] or sellSignal.iloc[-2]:
                                    logging.info(f"🔔 {symbol} Short signal detected on {timeframe}")
                                    order = await client.create_market_order(symbol, 'sell', quantity)
                                    if order and order.get('status') == 'closed':
                                        await asyncio.sleep(1)  # Wait for position to register
                                        if await client.confirm_position(symbol):
                                            last_trade[symbol] = {
                                                'entry_price': entry_price,
                                                'side': 'sell',
                                                'quantity': quantity
                                            }
                                            position_open[symbol] = True
                                            await set_sl(symbol)
                                            await set_tp(symbol)
                                            break
                                        else:
                                            logging.error(f"Failed to confirm position for {symbol} after sell order")
                                            continue
                                    else:
                                        logging.error(f"Failed to create sell order for {symbol}")
                                        continue

                                else:
                                    logging.info(f"[{symbol}@{timeframe}] 🔍 No valid signal.")
                            else:
                                info = last_trade.get(symbol)
                                if info:
                                    current_price = df['close'].iloc[-1]
                                    tp, _ = calculate_tp_sl(symbol)
                                    if (
                                        info['side'] == 'buy'
                                        and current_price >= tp
                                    ) or (
                                        info['side'] == 'sell'
                                        and current_price <= tp
                                    ):
                                        await set_tp(symbol)
                                        position_open[symbol] = False
                                        cooling_until[symbol] = now + timedelta(
                                            minutes=30
                                        )
                                        logging.info(
                                            f"{symbol} TP order placed at {tp:.2f}. Entering cooldown."
                                        )
                                        last_trade.pop(symbol, None)
                                else:
                                    logging.info(f"[{symbol}@{timeframe}] 🔄 Position already open. Skipping.")

                        except Exception as tf_e:
                            logging.warning(f"Erro ao processar {symbol} no timeframe {timeframe}: {tf_e}")

            await asyncio.sleep(60)

    except Exception as e:
        print(f"❌ Erro na execução do bot: {e}\n{traceback.format_exc()}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(trading_loop())
