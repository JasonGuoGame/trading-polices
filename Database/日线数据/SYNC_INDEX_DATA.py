import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import time

# --- 配置区 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

# 定义需要监控的核心指数
INDEX_LIST = [
    '000001.SH',  # 上证指数 (App里的第一个)
    '399001.SZ',  # 深证成指 (App里的第二个) - 新增
    '399006.SZ',  # 创业板指 (App里的第三个)
    '000300.SH',  # 沪深300 (机构基准)
    '000852.SH',  # 中证1000 (小盘股风向标)
]

# 设定补全的历史起点
START_TIME = '20230101' 
# --------------

def sync_index_daily():
    xtdata.enable_hello = False
    print(f"[{datetime.datetime.now()}] 正在启动大盘指数数据补全任务...")

    # 1. 下载指令
    print("正在向服务器请求指数历史数据...")
    for idx_code in INDEX_LIST:
        xtdata.download_history_data(idx_code, period='1d', start_time=START_TIME)
        # 指数数据量小，下载很快，稍微停顿即可
        time.sleep(0.2)

    # 2. 读取本地下载好的数据
    print("正在从本地缓存读取并处理数据...")
    res = xtdata.get_local_data(
        stock_list=INDEX_LIST,
        period='1d',
        start_time=START_TIME,
        count=-1,
        field_list=['open', 'high', 'low', 'close', 'volume', 'amount']
    )

    all_dfs = []
    for symbol in INDEX_LIST:
        if symbol in res and not res[symbol].empty:
            df = pd.DataFrame(res[symbol])
            df['symbol'] = symbol
            df['trade_date'] = pd.to_datetime(df.index, unit='ms').date
            df = df.reset_index(drop=True)
            
            # --- 核心修改：匹配你的 10 个字段结构 ---
            df['turnover_rate'] = 0.0  # 指数换手率为0
            df['per_factor'] = 1.0     # 指数复权因子默认为1.0
            
            # 确保列名和顺序与你的数据库完全一致 (共10列)
            df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate', 'per_factor']]
            all_dfs.append(df)
            print(f"成功整理指数: {symbol} (共 {len(df)} 行)")

    # 3. 覆盖式同步到 MySQL
    if all_dfs:
        final_df = pd.concat(all_dfs)
        try:
            with engine.begin() as conn:
                # A. 确保清理旧的临时表
                conn.execute(text("DROP TABLE IF EXISTS temp_index_daily;"))
                
                # B. 将 Pandas 数据写入临时表 (会包含 per_factor 这一列)
                final_df.to_sql('temp_index_daily', con=conn, if_exists='replace', index=False)
                
                # C. 执行 UPSERT (手动列出所有 10 个字段)
                upsert_sql = text("""
                    INSERT INTO stk_daily_kline (symbol, trade_date, open, high, low, close, volume, amount, turnover_rate, per_factor)
                    SELECT symbol, trade_date, open, high, low, close, volume, amount, turnover_rate, per_factor 
                    FROM temp_index_daily
                    ON DUPLICATE KEY UPDATE 
                        open = VALUES(open),
                        high = VALUES(high),
                        low = VALUES(low),
                        close = VALUES(close),
                        volume = VALUES(volume),
                        amount = VALUES(amount),
                        turnover_rate = VALUES(turnover_rate),
                        per_factor = VALUES(per_factor);
                """)
                conn.execute(upsert_sql)
                conn.execute(text("DROP TABLE IF EXISTS temp_index_daily;"))
                
            print(f"✅ 大盘指数同步完成！共处理 {len(final_df)} 条记录。")
        except Exception as e:
            print(f"❌ 写入数据库失败: {e}")
    else:
        print("未获取到任何指数数据，请检查 MiniQMT 连接。")

if __name__ == "__main__":
    # 执行前确认数据库主键已建立
    # ALTER TABLE stk_daily_kline ADD PRIMARY KEY (symbol, trade_date);
    sync_index_daily()