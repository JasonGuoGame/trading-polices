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
    '000001.SH',  # 上证指数
    '399001.SZ',  # 深证成指
    '399006.SZ',  # 创业板指
    '000300.SH',  # 沪深300
    '000852.SH',  # 中证1000
]

def sync_index_today():
    xtdata.enable_hello = False
    
    # 1. 获取今天日期字符串 (格式: 20240523)
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_date = datetime.date.today()
    
    print(f"[{datetime.datetime.now()}] 🚀 启动大盘指数【当日增量】同步...")

    # 2. 下载今日指令
    print(f"正在请求今日 ({today_str}) 指数行情...")
    for idx_code in INDEX_LIST:
        # 仅下载今天的数据，速度极快
        xtdata.download_history_data(idx_code, period='1d', start_time=today_str)
    
    # 缓冲 2 秒等待数据落盘
    time.sleep(2)

    # 3. 读取本地数据
    # count=-1 表示读取从今日 start_time 之后的所有记录
    res = xtdata.get_local_data(
        field_list=['open', 'high', 'low', 'close', 'volume', 'amount'],
        stock_list=INDEX_LIST,
        period='1d',
        start_time=today_str,
        count=-1
    )

    all_dfs = []
    for symbol in INDEX_LIST:
        if symbol in res and not res[symbol].empty:
            df = pd.DataFrame(res[symbol])
            df['symbol'] = symbol
            df['trade_date'] = pd.to_datetime(df.index, unit='ms').date
            df = df.reset_index(drop=True)
            
            # 只保留今天的数据（过滤掉可能的历史余量）
            df = df[df['trade_date'] == today_date]
            
            if df.empty: continue

            # 匹配 10 个字段结构
            df['turnover_rate'] = 0.0
            df['per_factor'] = 1.0
            
            df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate', 'per_factor']]
            all_dfs.append(df)

    # 4. 执行 UPSERT 覆盖同步
    if all_dfs:
        final_df = pd.concat(all_dfs)
        try:
            with engine.begin() as conn:
                # 写入临时表
                final_df.to_sql('temp_index_today', con=conn, if_exists='replace', index=False)
                
                # 显式 UPSERT：如果 (symbol, trade_date) 已存在，则更新价格和量
                upsert_sql = text("""
                    INSERT INTO stk_daily_kline (symbol, trade_date, open, high, low, close, volume, amount, turnover_rate, per_factor)
                    SELECT symbol, trade_date, open, high, low, close, volume, amount, turnover_rate, per_factor 
                    FROM temp_index_today
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
                conn.execute(text("DROP TABLE IF EXISTS temp_index_today;"))
                
            print(f"✅ 今日指数同步完成！已更新 {len(final_df)} 条记录。")
            # 打印简报
            print(final_df[['symbol', 'close', 'volume']].to_string(index=False))
            
        except Exception as e:
            print(f"❌ 写入数据库失败: {e}")
    else:
        print(f"💡 提示：今日 ({today_str}) 暂无指数行情产生。")

if __name__ == "__main__":
    sync_index_today()