import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import time

# --- 配置区 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

PERIOD = '1m'        # 1分钟线
KEEP_DAYS = 30       # 数据库保留最近30天数据
BATCH_SIZE = 50      # 每批处理50只股票
# --------------

def maintain_minute_db():
    xtdata.enable_hello = False
    now = datetime.datetime.now()
    
    # 1. 数据清理：删除超过 KEEP_DAYS 的老数据
    print(f"[{now}] 正在清理过往数据...")
    cutoff_time = (now - datetime.timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    
    with engine.begin() as conn:
        del_sql = text(f"DELETE FROM stk_min_kline WHERE trade_time < '{cutoff_time}'")
        res = conn.execute(del_sql)
        print(f"清理完成，已从数据库移除 {res.rowcount} 行老旧分时数据。")

    # 2. 获取股票列表 (只同步活跃的主板和双创)
    all_stocks = xtdata.get_stock_list_in_sector('沪深A股')
    all_stocks = [s for s in all_stocks if s.startswith(('60', '00', '30', '688'))]
    total_count = len(all_stocks)
    print(f"共需维护 {total_count} 只股票。")

    # 3. 获取数据库中每只股票的最新时间点（增量同步参考）
    print("正在扫描数据库现有进度...")
    with engine.connect() as conn:
        # 这一步通过 SQL 一次性查出每只票的最大时间，比循环查快得多
        query_progress = text("SELECT symbol, MAX(trade_time) as last_time FROM stk_min_kline GROUP BY symbol")
        progress_df = pd.read_sql(query_progress, conn)
        # 转为字典：{ '600519.SH': datetime_obj }
        last_sync_map = dict(zip(progress_df['symbol'], progress_df['last_time']))

    # 4. 循环同步
    for i in range(0, total_count, BATCH_SIZE):
        chunk = all_stocks[i : i + BATCH_SIZE]
        
        all_dfs = []
        for stock in chunk:
            # 确定每只股票的同步起始点
            if stock in last_sync_map:
                # 数据库里有，从最新时间加1分钟开始
                start_dt = last_sync_map[stock] + datetime.timedelta(minutes=1)
            else:
                # 数据库里没有，同步最近 30 天
                start_dt = now - datetime.timedelta(days=KEEP_DAYS)
            
            start_str = start_dt.strftime('%Y%m%d%H%M%S')
            
            try:
                # 下载指令 (增量下载)
                xtdata.download_history_data(stock, period=PERIOD, start_time=start_str)
                time.sleep(0.01) # 微小延迟防止 QMT 响应不过来
                
                # 读取本地数据
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
                    # 转换并去掉秒
                    df['trade_time'] = pd.to_datetime(df.index, unit='ms').strftime('%Y-%m-%d %H:%M:00')
                    df = df.reset_index(drop=True)
                    all_dfs.append(df[['symbol', 'trade_time', 'open', 'high', 'low', 'close', 'volume', 'amount']])
            except:
                continue

        # 批量写入 MySQL
        if all_dfs:
            final_df = pd.concat(all_dfs)
            try:
                # 使用 append。因为有主键 (symbol, trade_time)，重复的数据不会被存入
                final_df.to_sql('stk_min_kline', con=engine, if_exists='append', index=False, chunksize=2000)
                print(f"进度: {min(i + BATCH_SIZE, total_count)}/{total_count} | 写入 {len(final_df)} 行")
            except:
                # 遇到主键冲突等错误，跳过即可
                pass

    print(f"[{datetime.datetime.now()}] --- 数据库维护任务顺利完成 ---")

if __name__ == "__main__":
    maintain_minute_db()