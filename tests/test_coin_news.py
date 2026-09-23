from brief.collect.coin_news import mentions


def _a(t):
    return {"title": t}


def test_bitcoin_does_not_match_bitcoin_cash():
    assert not mentions(_a("CME adds Bitcoin Cash futures"), "bitcoin", "Bitcoin", "BTC")
    assert mentions(_a("CME adds Bitcoin Cash futures"), "bitcoin-cash", "Bitcoin Cash", "BCH")
    assert mentions(_a("Bitcoin ETFs take $1B"), "bitcoin", "Bitcoin", "BTC")


def test_symbol_needs_uppercase_and_three_letters():
    assert mentions(_a("MINA jumps after upgrade"), "mina-protocol", "Mina Protocol", "MINA")
    assert not mentions(_a("one more thing"), "harmony", "Harmony", "ONE")
    assert not mentions(_a("OP stack news"), "optimism", "Optimism", "OP")
