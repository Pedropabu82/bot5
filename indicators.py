import pandas_ta as ta


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
