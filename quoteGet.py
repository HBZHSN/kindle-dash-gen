import yfinance as yf

symbols = [
    "600519.SS",   # 贵州茅台，上海
    "000001.SZ",   # 平安银行，深圳
    "AAPL",        # 苹果
    "MSFT",        # 微软
    "BTC-USD",     # 比特币
    "ETH-USD",     # 以太坊
    "^NDX",        # 纳斯达克100指数
    "GC=F",        # COMEX黄金期货
]

data = yf.download(
    tickers=symbols,
    period="1d",
    interval="1m",
    group_by="ticker",
    progress=False,
    threads=True,
)

for symbol in symbols:
    try:
        last = data[symbol]["Close"].dropna().iloc[-1]
        print(symbol, last)
    except Exception as e:
        print(symbol, "获取失败", e)