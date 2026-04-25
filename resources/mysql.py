from xtquant import xtdata
import time

# 设定一个标的列表
code_list = ["601179.SH"]
# 设定获取数据的周期
period = "1d"

# 下载标的行情数据
print("开始下载历史、财务和板块数据...")
if 1:
    ## 为了方便用户进行数据管理，xtquant的大部分历史数据都是以压缩形式存储在本地的
    ## 比如行情数据，需要通过download_history_data下载，财务数据需要通过
    ## 所以在取历史数据之前，我们需要调用数据下载接口，将数据下载到本地
    for i in code_list:
        print("1开始下载历史、财务和板块数据...")
        xtdata.download_history_data(i,period=period,incrementally=True) # 增量下载行情数据（开高低收,等等）到本地
    print("2开始下载财务和板块数据...")
    xtdata.download_financial_data(code_list,start_time='20250101',end_time='20260101',incrementally=True) # 下载财务数据到本地
    print("3开始下载板块数据...")
    xtdata.download_sector_data() # 下载板块数据到本地
    # 更多数据的下载方式可以通过数据字典查询
print("=-" * 20)
# 读取本地历史行情数据
history_data = xtdata.get_market_data_ex([],code_list,period=period,count=-1)
print(history_data)
print("=" * 20)

# 如果需要盘中的实时行情，需要向服务器进行订阅后才能获取
# 订阅后，get_market_data函数于get_market_data_ex函数将会自动拼接本地历史行情与服务器实时行情

# 向服务器订阅数据
for i in code_list:
    xtdata.subscribe_quote(i,period=period,count=-1) # 设置count = -1来取到当天所有实时行情

print("4开始下载历史、财务和板块数据...")
# 等待订阅完成
time.sleep(1)

# 获取订阅后的行情
kline_data = xtdata.get_market_data_ex([],code_list,period=period)
print(kline_data)

# 获取订阅后的行情，并以固定间隔进行刷新,预期会循环打印10次
for i in range(10):
    # 这边做演示，就用for来循环了，实际使用中可以用while True
    kline_data = xtdata.get_market_data_ex([],code_list,period=period)
    print(kline_data)
    time.sleep(3) # 三秒后再次获取行情

# 如果不想用固定间隔触发，可以以用订阅后的回调来执行
# 这种模式下当订阅的callback回调函数将会异步的执行，每当订阅的标的tick发生变化更新，callback回调函数就会被调用一次
# 本地已有的数据不会触发callback
    
# 定义的回测函数
    ## 回调函数中，data是本次触发回调的数据，只有一条
def f(data):
    # print(data)
    
    code_list = list(data.keys())    # 获取到本次触发的标的代码

    kline_in_callabck = xtdata.get_market_data_ex([],code_list,period = period)    # 在回调中获取klines数据
    print(kline_in_callabck)

for i in code_list:
    xtdata.subscribe_quote(i,period=period,count=-1,callback=f) # 订阅时设定回调函数

# 使用回调时，必须要同时使用xtdata.run()来阻塞程序，否则程序运行到最后一行就直接结束退出了。
xtdata.run()