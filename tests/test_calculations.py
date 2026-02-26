from dark_alpha_phase_one.calculations import (
    Candle,
    aggregate_klines_to_window,
    atr_series,
    calculate_position_usdt,
    calculate_return,
)


def test_calculate_return_5m() -> None:
    closes = [100, 101, 102, 103, 104, 106]
    result = calculate_return(closes, lookback_minutes=5)
    assert result == 0.06


def test_atr_series_on_aggregated_15m() -> None:
    candles_1m = []
    for i in range(30):
        base = 100 + i
        candles_1m.append(Candle(open=base, high=base + 2, low=base - 1, close=base + 1))

    candles_15m = aggregate_klines_to_window(candles_1m, window=15)
    result = atr_series(candles_15m, period=2)

    assert len(candles_15m) == 2
    assert len(result) == 1
    assert result[0] > 0


def test_calculate_position_usdt() -> None:
    entry = 100.0
    stop = 98.8
    max_risk = 10.0
    result = calculate_position_usdt(entry=entry, stop=stop, max_risk_usdt=max_risk)
    assert round(result, 6) == round(10 / (1.2 / 100), 6)


def test_atr_series_has_values_with_sufficient_warmup() -> None:
    candles = []
    for i in range(45):
        base = 100 + (i * 0.5)
        candles.append(Candle(open=base, high=base + 1.2, low=base - 0.8, close=base + 0.3))

    atr_values = atr_series(candles, period=14)

    assert atr_values
    assert atr_values[-1] > 0
