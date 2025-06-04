import asyncio
import logging
import pandas as pd
import ccxt.async_support as ccxt


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


def round_to_tick(price, tick_size):
    return round(price / tick_size) * tick_size


class BinanceClient:
    def __init__(self, api_key, api_secret, sandbox_mode=True):
        self.exchange = ccxt.binanceusdm({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
        })
        self.exchange.set_sandbox_mode(sandbox_mode)
        self.markets = None
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
            symbol_formatted = symbol.replace('/', '')
            klines = await retry(self.live_exchange.fapiPublicGetKlines, params={
                'symbol': symbol_formatted,
                'interval': timeframe,
                'limit': limit
            })
            data = [{
                'timestamp': pd.to_datetime(int(kline[0]), unit='ms'),
                'open': float(kline[1]),
                'high': float(kline[2]),
                'low': float(kline[3]),
                'close': float(kline[4]),
                'volume': float(kline[5])
            } for kline in klines]
            df = pd.DataFrame(data)
            latest_price = df['close'].iloc[-1]
            if latest_price < 50000:
                logging.warning(f"Unrealistic price detected: {latest_price} for {symbol} on {timeframe}")
            return df
        except Exception as e:
            logging.error(f"Failed to fetch OHLCV for {symbol} on {timeframe}: {str(e)}")
            return pd.DataFrame()

    async def get_position_amt(self, symbol):
        try:
            symbol_formatted = symbol.replace('/', '')
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
