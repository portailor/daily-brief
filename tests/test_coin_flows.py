from brief.collect.coin_flows import event_kind


def _k(t):
    k = event_kind(t)
    return k[0] if k else None


def test_event_kinds():
    assert _k("Community BuyBack") == "바이백·소각"
    assert _k("Meridian mainnet upgrade") == "네트워크 업그레이드"
    assert _k("Bitazza Withdrawal Halt") == "입출금 중단"
    assert _k("Token unlock") == "물량 해제"
    assert _k("Binance listing") == "거래소 상장"
    assert _k("Delisting from Upbit") == "상장폐지"


def test_skips_talk_and_test_events():
    for t in ("Next Steps AMA", "Ecosystem Call Returns", "Testnet upgrade",
              "Settlement rollback demo", "Amaru Pre-Release", "Something Unknown"):
        assert _k(t) is None, t
