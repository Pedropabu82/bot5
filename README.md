# Bot5

Bot5 is a simple trading bot that uses the Binance futures API via the
`ccxt` library.

## Setup

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

Copy `config.json` and adjust the strategy settings if necessary. The bot
expects Binance credentials to be provided through the environment:

```bash
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret
```

## Running

Execute the bot directly with Python:

```bash
python main.py
```

The API key and secret must be defined for the bot to connect to Binance.
