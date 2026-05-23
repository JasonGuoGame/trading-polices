import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def add_suffix(code):
    code = str(code).zfill(6)
    return code + ".SH" if code.startswith('6') else code + ".SZ"

def fetch_stock_data(symbol):
    """
    单个股票抓取任务
    """
    pure_code = symbol.split('.')[0]
    try:
        df_raw = ak.stock_zh_a_gdhs_detail_em(symbol=pure_code)
        if df_raw is None or not isinstance(df_raw, pd.DataFrame) or df_raw.empty:
            return None
        
        # 数据清洗与对齐
        df_proc = df_raw.copy()
        df_proc['symbol'] = symbol
        mapping = {
            '名称': 'name', '股东户数统计截止日': 'end_date', '股东户数公告日期': 'ann_date',
            '股东户数-本次': 'holder_count', '股东户数-上次': 'prev_holder_count',
            '股东户数-增减': 'change_count', '股东户数-增减比例': 'change_rate',
            '户均持股市值': 'avg_hold_price'
        }
        df_proc = df_proc.rename(columns={k: v for k, v in mapping.items() if k in df_proc.columns})
        
        # 统一列名
        target_cols = ['symbol', 'name', 'end_date', 'ann_date', 'holder_count', 'prev_holder_count', 'change_count', 'change_rate', 'avg_hold_price']
        for col in target_cols:
            if col not in df_proc.columns: df_proc[col] = None
            
        df_final = df_proc[target_cols].copy()
        df_final['end_date'] = pd.to_datetime(df_final['end_date'], errors='coerce').dt.date
        df_final['ann_date'] = pd.to_datetime(df_final['ann_date'], errors='coerce').dt.date
        
        num_cols = ['holder_count', 'prev_holder_count', 'change_count', 'change_rate', 'avg_hold_price']
        for col in num_cols:
            df_final[col] = pd.to_numeric(df_final[col], errors='coerce').fillna(0)
            
        return df_final.dropna(subset=['symbol', 'end_date'])
    except Exception:
        return None

def fast_sync_all_holders():
    # 1. 获取股票清单
    with engine.connect() as conn:
        query = "SELECT symbol FROM stocks WHERE symbol REGEXP '^[0-9]{6}.(SH|SZ)$'"
        all_stocks = pd.read_sql(query, conn)['symbol'].tolist()
    
    total = len(all_stocks)
    print(f"🚀 启动快速同步：共 {total} 只股票，采用多线程模式...")

    all_dfs = []
    # 2. 多线程并发抓取
    # max_workers 建议设置在 5-10 之间。太大会被东财封 IP
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_stock = {executor.submit(fetch_stock_data, s): s for s in all_stocks}
        
        done_count = 0
        for future in as_completed(future_to_stock):
            res_df = future.result()
            if res_df is not None:
                all_dfs.append(res_df)
            
            done_count += 1
            if done_count % 50 == 0:
                print(f"进度: {done_count}/{total} | 内存已积累记录: {len(all_dfs)} 只股票数据")

    # 3. 批量写入数据库 (不再一只一只写，而是一次性大批量写)
    if all_dfs:
        print("📥 正在合并数据并批量存入数据库...")
        final_big_df = pd.concat(all_dfs, ignore_index=True)
        
        # 分块写入，防止 SQL 过大
        chunk_size = 50000
        with engine.begin() as conn:
            # 创建临时表
            conn.execute(text("CREATE TEMPORARY TABLE temp_fast_sync LIKE stk_holders_history"))
            
            # 写入临时表
            final_big_df.to_sql('temp_fast_sync', con=conn, if_exists='append', index=False, chunksize=5000)
            
            # 执行一次性的 UPSERT
            print("💾 执行数据库 UPSERT 覆盖更新...")
            upsert_sql = text("""
                INSERT INTO stk_holders_history 
                SELECT * FROM temp_fast_sync
                ON DUPLICATE KEY UPDATE 
                    ann_date = VALUES(ann_date),
                    holder_count = VALUES(holder_count),
                    change_rate = VALUES(change_rate),
                    avg_hold_price = VALUES(avg_hold_price);
            """)
            conn.execute(upsert_sql)
            print("✅ 数据库写入完成。")
    else:
        print("未抓取到任何数据。")

if __name__ == "__main__":
    start_time = time.time()
    fast_sync_all_holders()
    print(f"⏱️ 总耗时: {(time.time() - start_time)/60:.2f} 分钟")