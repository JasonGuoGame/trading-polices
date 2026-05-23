import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import time

# --- 配置区 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

PERIOD = '1m'
KEEP_DAYS = 30       # 数据库保留最近30天数据
BATCH_SIZE = 100     # 增量模式下，每批可以处理更多只股票
# --------------

def sync_minute_incremental():
    xtdata.enable_hello = False
    now = datetime.datetime.now()
    
    # 1. 自动清理：删除 30 天前的数据
    print(f"[{now}] 步骤 1: 正在清理 {KEEP_DAYS} 天前的老旧分时数据...")
    cutoff_dt = now - datetime.timedelta(days=KEEP_DAYS)
    cutoff_str = cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')
    
    with engine.begin() as conn:
        # 删除操作
        res = conn.execute(text(f"DELETE FROM stk_min_kline WHERE trade_time < '{cutoff_str}'"))
        print(f"清理完成，已移除 {res.rowcount} 行历史数据。")

    # 2. 获取股票进度：查出数据库里每只股票目前存到了什么时候
    print("步骤 2: 正在扫描数据库现有进度...")
    with engine.connect() as conn:
        # 一次性获取所有股票的最晚时间点
        query = text("SELECT symbol, MAX(trade_time) as last_time FROM stk_min_kline GROUP BY symbol")
        progress_df = pd.read_sql(query, conn)
        # 转为字典映射 { '代码': 最后时间 }
        last_sync_map = dict(zip(progress_df['symbol'], progress_df['last_time']))

    # 3. 获取股票列表并分批同步
    all_stocks = xtdata.get_stock_list_in_sector('沪深A股')
    # all_stocks = [s for s in all_stocks if s.startswith(('60', '00'))] # 只要主板
    total_count = len(all_stocks)
    print(f"步骤 3: 准备同步 {total_count} 只股票的增量数据...")

    for i in range(0, total_count, BATCH_SIZE):
        chunk = all_stocks[i : i + BATCH_SIZE]
        all_dfs = []
        
        for stock in chunk:
            # 确定每只股票的同步起点
            if stock in last_sync_map and last_sync_map[stock] is not None:
                # 数据库有数据，从【最后时间 + 1分钟】开始取
                start_dt = last_sync_map[stock] + datetime.timedelta(minutes=1)
                # 如果最后时间已经因为清理变旧了，则强制使用清理边界
                if start_dt < cutoff_dt:
                    start_dt = cutoff_dt
            else:
                # 数据库没数据，取最近 30 天
                start_dt = cutoff_dt
            
            start_str = start_dt.strftime('%Y%m%d%H%M%S')
            
            try:
                # 下载最新增量（只下载缺的那几小时/几天）
                xtdata.download_history_data(stock, period=PERIOD, start_time=start_str)
                
                # 获取本地这一段增量数据
                res = xtdata.get_local_data(
                    stock_list=[stock],
                    period=PERIOD,
                    start_time=start_str,
                    count=-1,
                    field_list=['open', 'high', 'low', 'close', 'volume', 'amount']
                )
                
                if stock in res and not res[stock].empty:
                    df = pd.DataFrame(res[stock])
                    df['symbol'] = stock
                    # 格式化时间并规范化
                    df['trade_time'] = pd.to_datetime(df.index, unit='ms').map(lambda x: x.replace(second=0))
                    df = df.reset_index(drop=True)
                    all_dfs.append(df[['symbol', 'trade_time', 'open', 'high', 'low', 'close', 'volume', 'amount']])
            except:
                continue

        # 4. 增量写入：使用 append 模式（速度极快）
        if all_dfs:
            final_batch = pd.concat(all_dfs)
            try:
                # 注意：只要主键 (symbol, trade_time) 正确，这里直接 append 即可。
                # 如果担心网络重试导致极个别重复，可以使用 if_exists='append'
                # MySQL 会因为主键冲突拦截掉重叠的那一两分钟，保护数据不乱。
                final_batch.to_sql('stk_min_kline', con=engine, if_exists='append', index=False, chunksize=5000)
                print(f"进度: {min(i + BATCH_SIZE, total_count)}/{total_count} | 写入增量: {len(final_batch)} 行")
            except Exception as e:
                # 即使有极个别主键冲突，也说明数据已存在，跳过即可
                pass

    print(f"[{datetime.datetime.now()}] --- 30日分时库增量维护完成 ---")

if __name__ == "__main__":
    # 为了保证删除和写入的高效，请确保 MySQL 的 trade_time 字段有索引
    # ALTER TABLE stk_min_kline ADD INDEX idx_time (trade_time);
    sync_minute_incremental()