from app.kline import market_prefix, secid


def test_shanghai_index_000300_not_mapped_to_sz():
    assert market_prefix("000300") == "sh"
    assert secid("000300") == "1.000300"


def test_shenzhen_index_and_etf_stay_sz():
    assert market_prefix("399300") == "sz"
    assert market_prefix("159845") == "sz"
    assert secid("399300") == "0.399300"


def test_000001_keeps_stock_default_sz():
    # Collides with 上证指数; stock chart path should remain SZ 平安银行.
    assert market_prefix("000001") == "sz"
