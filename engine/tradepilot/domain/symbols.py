"""Tamaño de tick por raíz de contrato: para expresar el deslizamiento en ticks (lo que el trader compara a ojo)."""

TICK_SIZES: dict[str, float] = {
    "ES": 0.25, "MES": 0.25, "NQ": 0.25, "MNQ": 0.25, "YM": 1.0, "MYM": 1.0, "RTY": 0.1, "M2K": 0.1,
    "CL": 0.01, "MCL": 0.01, "QM": 0.025, "GC": 0.1, "MGC": 0.1, "SI": 0.005, "SIL": 0.005, "NG": 0.001, "QG": 0.005,
    "ZB": 1 / 32, "ZN": 1 / 64, "ZF": 1 / 128, "ZC": 0.25, "ZS": 0.25, "ZW": 0.25,
    "6E": 0.00005, "6J": 0.0000005, "6B": 0.0001, "M6E": 0.0001, "BTC": 5.0, "MBT": 5.0,
}


# Valor en dólares de un punto, para el P&L por operación calculado a partir de los fills (el del bróker manda en el día).
POINT_VALUES: dict[str, float] = {
    "ES": 50, "MES": 5, "NQ": 20, "MNQ": 2, "YM": 5, "MYM": 0.5, "RTY": 50, "M2K": 5, "CL": 1000, "MCL": 100, "QM": 500,
    "GC": 100, "MGC": 10, "SI": 5000, "SIL": 1000, "NG": 10000, "QG": 2500, "ZB": 1000, "ZN": 1000, "ZF": 1000,
    "ZC": 50, "ZS": 50, "ZW": 50, "6E": 125000, "6J": 12500000, "6B": 62500, "M6E": 12500, "BTC": 5, "MBT": 0.1,
}


def point_value(symbol: str) -> float:
    return POINT_VALUES.get(symbol_root(symbol), 1.0)


def symbol_root(symbol: str) -> str:
    return (symbol or "").split(" ")[0].upper()


def tick_size(symbol: str) -> float:
    return TICK_SIZES.get(symbol_root(symbol), 0.25)
