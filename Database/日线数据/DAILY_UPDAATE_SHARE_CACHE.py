# -*- coding: utf-8 -*-
"""
外部行情同步服务：
从 QMT 共享的 SQLite 缓存文件中高频读取实时行情，批量同步写入 MySQL 数据库。
"""
import os
import time
import sqlite3
import datetime
import pandas as pd
from sqlalchemy import create_engine, text

# --- 配置区 ---
# 1. QMT 共享 SQLite 缓存数据库路径
SHARE_DB_PATH = r"C:\ws\trading-polices\data_cache\realtime_cache.db"

# 2. 本地 MySQL 数据库连接配置
MYSQL_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(
    MYSQL_URL,
    pool_size=5,          # 连接池大小
    max_overflow=10,      # 最大溢出连接数
    pool_recycle=3600     # 自动回收空闲连接，防止 MySQL connection timeout
)

# 3. 轮询同步间隔（单位：秒）
SYNC_INTERVAL = 2.0  
# --------------

def init_mysql_table():
    """初始化 MySQL 中的实时行情表结构（如果不存在）"""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS stk_realtime_kline (
        symbol VARCHAR(20) NOT NULL,
        trade_time DATETIME NOT NULL,
        last_price DECIMAL(10, 4) DEFAULT 0.0000,
        volume BIGINT DEFAULT 0,
        amount DECIMAL(18, 4) DEFAULT 0.0000,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (symbol, trade_time),
        KEY idx_trade_time (trade_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='QMT全市场实时行情快照表';
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(create_table_sql))
        print("[Init] MySQL table 'stk_realtime_kline' checked/created successfully.")
    except Exception as e:
        print(f"[Init Error] Failed to initialize MySQL table: {e}")

def get_latest_data_from_sqlite():
    """从 SQLite 共享文件读取全量/增量实时数据"""
    if not os.path.exists(SHARE_DB_PATH):
        return pd.DataFrame()

    try:
        # timeout=10 防止与 QMT 写入冲突; uri=True 支持以只读模式打开
        conn = sqlite3.connect(f"file:{SHARE_DB_PATH}?mode=ro", uri=True, timeout=10)
        
        query = "SELECT symbol, time AS trade_time, lastPrice AS last_price, volume, amount FROM real_time_ticks"
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"[Read Cache Warning] SQLite read error (busy/locked): {e}")
        return pd.DataFrame()

def sync_data_to_mysql():
    """将数据通过临时表做高效率批量 UPSERT 入库"""
    init_mysql_table()
    
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Real-time MySQL Sync Engine...")
    last_processed_count = 0

    while True:
        loop_start_time = time.time()
        
        # 1. 从 SQLite 提取最新行情
        df = get_latest_data_from_sqlite()

        if not df.empty:
            try:
                # 数据清洗与格式转换
                df['trade_time'] = pd.to_datetime(df['trade_time'])
                
                # 2. 使用临时表批量插入 MySQL
                with engine.begin() as mysql_conn:
                    # A. 写入临时内存/磁盘表
                    df.to_sql('temp_stk_realtime', con=mysql_conn, if_exists='replace', index=False)
                    
                    # B. 高效 ON DUPLICATE KEY UPDATE 覆盖写入
                    upsert_sql = text("""
                        INSERT INTO stk_realtime_kline (symbol, trade_time, last_price, volume, amount)
                        SELECT symbol, trade_time, last_price, volume, amount 
                        FROM temp_stk_realtime
                        ON DUPLICATE KEY UPDATE 
                            last_price = VALUES(last_price),
                            volume = VALUES(volume),
                            amount = VALUES(amount);
                    """)
                    mysql_conn.execute(upsert_sql)
                    
                    # C. 清理临时表
                    mysql_conn.execute(text("DROP TABLE IF EXISTS temp_stk_realtime;"))

                last_processed_count = len(df)
                now_str = datetime.datetime.now().strftime('%H:%M:%S')
                print(f"[{now_str}] Sync Success: Pushed {last_processed_count} realtime ticks to MySQL.")

            except Exception as e:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [Sync Error] MySQL Bulk Write Failed: {e}")

        # 3. 动态休眠控制，保证固定频率轮询
        elapsed = time.time() - loop_start_time
        sleep_time = max(0.1, SYNC_INTERVAL - elapsed)
        time.sleep(sleep_time)

if __name__ == '__main__':
    sync_data_to_mysql()