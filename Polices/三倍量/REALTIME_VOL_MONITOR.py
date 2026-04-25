import time
import pandas as pd
from xtquant import xtdata
from functools import partial  # 必须导入，用于绑定股票代码

# --- 配置区 ---
VOL_RATIO_LIMIT = 3.0
MIN_AMOUNT = 50000000 
MAX_SUBSCRIBE_COUNT = 1000 
# --------------

class VolMonitor:
    def __init__(self):
        self.yesterday_vols = {}
        self.stock_names = {}
        self.triggered_list = set()

    def prepare_data(self):
        print("正在进行初始化，获取昨日成交量...")
        all_stocks = xtdata.get_stock_list_in_sector('沪深A股')
        target_stocks = [s for s in all_stocks if s.startswith(('60', '00')) and (s.endswith('.SH') or s.endswith('.SZ'))]
        
        # 获取昨日行情
        last_k = xtdata.get_market_data_ex(['volume', 'amount'], target_stocks, period='1d', count=1)
        
        temp_list = []
        for stock in target_stocks:
            if stock in last_k and not last_k[stock].empty:
                yest_vol = last_k[stock]['volume'].iloc[-1]
                yest_amt = last_k[stock]['amount'].iloc[-1]
                if yest_vol > 0 and yest_amt > 10000000:
                    temp_list.append({'symbol': stock, 'yest_vol': yest_vol, 'yest_amt': yest_amt})
        
        # 按成交额排序，取前 500
        df_sort = pd.DataFrame(temp_list).sort_values('yest_amt', ascending=False).head(MAX_SUBSCRIBE_COUNT)
        
        for _, row in df_sort.iterrows():
            s = row['symbol']
            self.yesterday_vols[s] = row['yest_vol']
            detail = xtdata.get_instrument_detail(s)
            self.stock_names[s] = detail.get('InstrumentName', '未知')

        print(f"初始化完成，盯盘目标: {len(self.yesterday_vols)} 只股票。")
        return list(self.yesterday_vols.keys())

    def on_data(self, stock_code, data):
        """
        修正后的回调函数
        stock_code: 通过 partial 传入的股票代码
        data: MiniQMT 传入的原始数据 (列表格式)
        """
        # 1. 确认 data 是列表且不为空
        if not isinstance(data, list) or len(data) == 0:
            return
        
        # 2. 如果已经报警过，直接跳过
        if stock_code in self.triggered_list:
            return
        
        # 3. 获取最新的一笔 tick (字典)
        quote = data[-1]
        
        curr_vol = quote.get('volume', 0)
        curr_amt = quote.get('amount', 0)
        yest_vol = self.yesterday_vols.get(stock_code, 0)
        
        if yest_vol > 0:
            ratio = curr_vol / yest_vol
            if ratio >= VOL_RATIO_LIMIT and curr_amt >= MIN_AMOUNT:
                name = self.stock_names.get(stock_code, '未知')
                print(f"\n🔥【爆量预警】{stock_code} | {name}")
                print(f"   今日累计量: {curr_vol} | 昨日总量: {yest_vol} | 倍数: {ratio:.2f}")
                print(f"   当前总成交额: {curr_amt/1e8:.2f} 亿")
                print("-" * 30)
                self.triggered_list.add(stock_code)

def start_monitoring():
    monitor = VolMonitor()
    target_list = monitor.prepare_data()

    print("正在逐个订阅行情...")
    success_sub = 0
    for stock in target_list:
        try:
            # --- 关键修改：使用 partial 绑定 stock 变量到回调函数中 ---
            callback_with_id = partial(monitor.on_data, stock)
            xtdata.subscribe_quote(stock, period='tick', count=0, callback=callback_with_id)
            # -----------------------------------------------------
            success_sub += 1
        except:
            continue

    print(f"🚀 监控启动成功！当前有效订阅: {success_sub} 只。正在实时盯盘...")
    xtdata.run()

if __name__ == "__main__":
    start_monitoring()