import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine
import time
import traceback

# --- 配置区 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

PERIOD = '1d'
START_TIME = '20230101'
BATCH_SIZE = 100
# --------------

def sync_all_stocks_to_mysql():
    xtdata.enable_hello = False
    
    print("正在获取全沪深 A 股列表...")
    all_stocks = xtdata.get_stock_list_in_sector('沪深A股')
    total_count = len(all_stocks)
    # all_stocks = all_stocks[:10]  # 测试用，正式运行请注释
    print(f"共发现 {total_count} 只股票。")

    # 1. 批量下载
    print("正在下发下载指令给 MiniQMT，请稍候...")
    for i in range(0, total_count, BATCH_SIZE):
        chunk = all_stocks[i : i + BATCH_SIZE]
        for stock in chunk:
            xtdata.download_history_data(stock, period=PERIOD, start_time=START_TIME, incrementally=True)
        print(f"已下发: {min(i + BATCH_SIZE, total_count)} / {total_count}")
    
    print("等待 5 秒让 QMT 完成磁盘写入...")
    time.sleep(5)

    # 2. 分批读取并入库
    print("开始同步数据到 MySQL...\n")
    debug_printed = False  # 控制只打印一次调试信息
    
    for i in range(0, total_count, BATCH_SIZE):
        chunk = all_stocks[i : i + BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        
        res = xtdata.get_local_data(
            stock_list=chunk,
            period=PERIOD,
            start_time=START_TIME,
            count=-1,
            field_list=['open', 'high', 'low', 'close', 'volume', 'amount', 'turnoverRate']
        )
        
        all_dfs = []
        for stock in chunk:
            if stock not in res or res[stock].empty:
                continue
                
            df = res[stock].copy()
            
            # 🔑 1. 安全转换日期
            df['trade_date'] = pd.to_datetime(df.index.astype(str), errors='coerce')
            df = df.dropna(subset=['trade_date'])
            if df.empty: continue
            df['trade_date'] = df['trade_date'].dt.strftime('%Y-%m-%d')
            
            # 🔑 2. 动态查找并标准化换手率（兼容多种命名）
            turnover_candidates = ['turnoverRate', 'turnover', 'turnover_rate', 'freeTurnoverRate', 'turnoverRate_f']
            src_col = None
            for cand in turnover_candidates:
                if cand in df.columns:
                    src_col = cand
                    break
                    
            if src_col:
                df['turnover_rate'] = pd.to_numeric(df[src_col], errors='coerce')
                df.drop(columns=[src_col], inplace=True)
            else:
                # 数据源未返回该字段时，显式创建空列，避免 to_sql 忽略该列
                df['turnover_rate'] = pd.NA
                
            # 🔍 调试打印（仅第一次执行）
            if not debug_printed:
                print(f"🔍 数据源实际列名: {list(df.columns)}")
                print(f"🔍 换手率前3条样例: {df['turnover_rate'].head(3).tolist()}")
                debug_printed = True

            df['symbol'] = stock
            
            # 🔑 3. 强制对齐目标列结构（缺失的列自动填 NaN）
            target_cols = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate']
            df = df.reindex(columns=target_cols)
            all_dfs.append(df)
            
        if not all_dfs:
            print(f"批次 {batch_no:3d}: 无有效数据，跳过")
            continue
            
        final_batch_df = pd.concat(all_dfs, ignore_index=True)
        print(f"批次 {batch_no:3d}: 准备写入 {len(final_batch_df):>6} 条记录", end=" | ")
        
        try:
            final_batch_df.to_sql(
                'stk_daily_kline',
                con=engine,
                if_exists='append',
                index=False,
                chunksize=1000
            )
            print("✅ 写入成功")
        except Exception as e:
            print(f"❌ 写入失败")
            print(f"   错误类型: {type(e).__name__}")
            print(f"   详细信息: {e}")
            continue

    print("\n--- 🎉 所有数据同步任务完成 ---")

if __name__ == "__main__":
    sync_all_stocks_to_mysql()