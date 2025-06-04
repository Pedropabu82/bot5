# Bot de Trading Binance

Este projeto implementa um bot de trading para a Binance Futures. Ele utiliza a biblioteca `ccxt` de forma assíncrona e combina indicadores (como WaveTrend, RSI e MFI) para gerar sinais de entrada e saída. O objetivo principal é automatizar operações em pares definidos no arquivo de configuração.

## Configuração

O comportamento do bot é controlado pelo arquivo `config.json`. Nele são definidos os parâmetros de estratégia, os timeframes analisados e os pares que serão negociados. Um trecho de exemplo do arquivo pode ser visto abaixo:

```json
{
  "strategy": {
    "leverage": 10,
    "fixed_size_usd": 10,
    "sl_pct": 0.025,
    "tp_pct": 0.07
  },
  "timeframes": ["15m", "30m", "1h"],
  "symbols": ["BTC/USDT"]
}
```

Além desses parâmetros, é necessário fornecer as credenciais da API da Binance. Defina as variáveis de ambiente `BINANCE_API_KEY` e `BINANCE_API_SECRET` com a sua chave e segredo de API antes de executar o bot:

```bash
export BINANCE_API_KEY="sua_chave"
export BINANCE_API_SECRET="seu_segredo"
```

## Instalação

1. Tenha o Python 3 instalado.
2. Instale as dependências principais:

```bash
pip3 install pandas pandas_ta numpy ccxt
```

## Execução

Com as variáveis de ambiente configuradas e o `config.json` devidamente ajustado, execute o bot com:

```bash
python3 main.py
```

Os logs de operação serão gravados no arquivo `trades.log`.
