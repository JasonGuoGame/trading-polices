import pandas as pd
import numpy as np
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import time
import warnings
import sys

# 屏蔽无关警告
warnings.filterwarnings('ignore')

# --- 1. 核心配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

PERIOD = '1m'
KEEP_DAYS = 8       
BATCH_SIZE = 300     # 盘中建议批次设为 300 左右，兼顾速度与稳定性
# ------------------

def sync_minute_pro():
    now = datetime.datetime.now()
    today_date = now.date()
    print(f"\n{'='*30}")
    print(f"🚀 实时分时同步系统启动 | 当前时间: {now.strftime('%H:%M:%S')}")
    print(f"{'='*30}")

    # --- 步骤 A: 检查 QMT 环境 ---
    try:
        xtdata.enable_hello = False
        all_a = xtdata.get_stock_list_in_sector('沪深A股')
        if not all_a:
            print("❌ 错误：无法连接 MiniQMT，请检查软件。")
            return
        print(f"✅ MiniQMT 联机成功。")
    except Exception as e:
        print(f"❌ 环境异常: {e}")
        return

    # --- 步骤 B: 确定清理 ---
    cutoff_dt = now - datetime.timedelta(days=KEEP_DAYS)
    if now.hour == 10 and now.minute < 10:
        cutoff_str = cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')
        print(f"🧹 执行例行清理...")
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM stk_min_kline WHERE trade_time < '{cutoff_str}'"))

    # --- 步骤 C: 扫描数据库进度 ---
    print("🔎 正在扫描数据库现有进度...")
    with engine.connect() as conn:
        # 重要提示：请确保已执行过下方的 SQL 增加索引：
        # ALTER TABLE stk_min_kline ADD INDEX idx_symbol_time (symbol, trade_time DESC);
        query_progress = text("SELECT symbol, MAX(trade_time) as last_time FROM stk_min_kline GROUP BY symbol")
        progress_df = pd.read_sql(query_progress, conn)
    
    last_sync_map = {}
    for _, row in progress_df.iterrows():
        l_time = pd.to_datetime(row['last_time'])
        # 核心：如果是今天的数据，回滚到早晨 09:30 确保断层补齐
        if l_time.date() >= today_date:
            last_sync_map[row['symbol']] = l_time.replace(hour=9, minute=30, second=0)
        else:
            last_sync_map[row['symbol']] = l_time

    # --- 步骤 D: 准备同步列表 ---
    all_stocks = [s for s in all_a if s.startswith(('60', '00', '30', '688'))]
    total_stocks = len(all_stocks)

    # --- 步骤 E: 分批循环同步 ---
    total_inserted = 0
    for i in range(0, total_stocks, BATCH_SIZE):
        chunk = all_stocks[i : i + BATCH_SIZE]
        chunk_start_dt = min([last_sync_map.get(s, cutoff_dt) for s in chunk])
        start_str = chunk_start_dt.strftime('%Y%m%d%H%M%S')
        
        try:
            # 1. 发送下载指令
            xtdata.download_history_data2(chunk, period=PERIOD, start_time=start_str)
            
            # 2. 【关键优化】给 QMT 处理网络数据流留出 2 秒缓冲
            time.sleep(2) 
            
            # 3. 【关键优化】使用 get_market_data_ex (读内存模式)
            res_ex = xtdata.get_market_data_ex(
                field_list=['open', 'high', 'low', 'close', 'volume', 'amount'],
                stock_list=chunk,
                period=PERIOD,
                start_time=start_str,
                count=-1
            )
        except Exception as e:
            print(f"⚠️ 批次 {i} 抓取异常: {e}")
            continue

        if not res_ex or 'close' not in res_ex:
            print(f"⏳ 进度: {min(i+BATCH_SIZE, total_stocks)}/{total_stocks} | 无新增")
            continue

        # 4. 【关键优化】重新解析 get_market_data_ex 的字典结构
        batch_dfs = []
        for stock in chunk:
            if stock in res_ex['close'] and not res_ex['close'][stock].empty:
                df = pd.DataFrame({
                    'open': res_ex['open'][stock],
                    # ... 其他列 ...
                })
                
                df.index = pd.to_datetime(df.index, unit='ms').floor('min')
                
                # --- 核心修改：删掉 if last_time: df = df[df.index > last_time] ---
                # 只保留库容清理过滤
                df = df[df.index >= cutoff_dt]
                
                if df.empty: continue
                # ... 组装数据 ...

        if batch_dfs:
            final_batch = pd.concat(batch_dfs, ignore_index=True)
            try:
                with engine.begin() as conn:
                    # 使用临时表覆盖更新 (UPSERT)
                    conn.execute(text("CREATE TEMPORARY TABLE temp_min_kline LIKE stk_min_kline"))
                    final_batch.to_sql('temp_min_kline', con=conn, if_exists='append', index=False, method='multi', chunksize=5000)
                    
                    upsert_sql = text("""
                        INSERT INTO stk_min_kline (symbol, trade_time, open, high, low, close, volume, amount)
                        SELECT symbol, trade_time, open, high, low, close, volume, amount FROM temp_min_kline
                        ON DUPLICATE KEY UPDATE 
                            open=VALUES(open), high=VALUES(high), low=VALUES(low), 
                            close=VALUES(close), volume=VALUES(volume), amount=VALUES(amount)
                    """)
                    conn.execute(upsert_sql)
                    conn.execute(text("DROP TEMPORARY TABLE IF EXISTS temp_min_kline"))
                
                total_inserted += len(final_batch)
                print(f"✅ 进度: {min(i+BATCH_SIZE, total_stocks)}/{total_stocks} | 同步: {len(final_batch)} 行")
            except Exception as e:
                print(f"❌ 数据库写入失败: {e}")
        else:
            print(f"⏳ 进度: {min(i+BATCH_SIZE, total_stocks)}/{total_stocks} | 已是最新")

    print(f"\n🏁 实时同步完成 | 本次入库: {total_inserted} 行")

if __name__ == "__main__":
    sync_minute_pro()