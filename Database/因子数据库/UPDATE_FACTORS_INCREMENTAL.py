import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime
import time

# --- 配置区 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)
LOOKBACK_WINDOW = 150 # 每次取150天数据进行计算，足以覆盖MACD和所有均线周期
# --------------

def update_factors_incremental():
    print(f"[{datetime.datetime.now()}] 启动因子库增量更新...")

    # 1. 获取数据库中因子表的现有进度
    print("正在检查现有因子库进度...")
    with engine.connect() as conn:
        progress_query = text("SELECT symbol, MAX(trade_date) as last_date FROM stk_factors GROUP BY symbol")
        progress_df = pd.read_sql(progress_query, conn)
        last_date_map = dict(zip(progress_df['symbol'], progress_df['last_date']))

    # 2. 获取行情数据
    # 我们只读取最近 200 天的数据，不再读取全表数百万行
    print(f"正在读取最近 {LOOKBACK_WINDOW} 天的行情...")
    query_kline = f"""
        SELECT symbol, trade_date, open, high, low, close, volume 
        FROM stk_daily_kline 
        WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL {LOOKBACK_WINDOW} DAY)
        ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query_kline, engine)
    
    if df_all.empty:
        print("行情库无数据。")
        return

    all_results = []
    
    # 3. 分组计算 (只处理最近的一段数据)
    print("正在计算增量因子...")
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 35: continue
        
        try:
            # --- 计算指标 ---
            ma5 = ta.sma(df['close'], length=5)
            ma10 = ta.sma(df['close'], length=10)
            ma20 = ta.sma(df['close'], length=20)
            v_ma60 = ta.sma(df['volume'], length=60)
            rsi = ta.rsi(df['close'], length=14)
            high_120 = df['high'].rolling(120, min_periods=1).max()
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)

            # --- 整合结果 ---
            res = pd.DataFrame(index=df.index)
            res['symbol'] = symbol
            res['trade_date'] = df['trade_date']
            
            ma_stack = pd.concat([ma5, ma10, ma20], axis=1)
            res['f_ma_cohesion'] = (ma_stack.max(axis=1) - ma_stack.min(axis=1)) / ma20
            res['f_vol_ratio'] = df['volume'] / v_ma60
            res['f_rsi_14'] = rsi
            res['f_mom_20'] = (df['close'] - df['close'].shift(20)) / df['close'].shift(20)
            res['f_dist_high'] = (high_120 - df['close']) / high_120
            res['f_macd_dif'] = macd['MACD_12_26_9']
            res['f_macd_dea'] = macd['MACDs_12_26_9']
            res['f_macd_hist'] = macd['MACDh_12_26_9']

            # --- 核心过滤逻辑：增量筛选 ---
            last_sync_date = last_date_map.get(symbol)
            if last_sync_date:
                # 只保留数据库里没有的日期，或者是最后一天（为了覆盖盘中的不完整数据）
                res = res[res['trade_date'] >= last_sync_date]
            
            all_results.append(res.dropna())
            
        except Exception:
            continue

    # 4. UPSERT 写入
    if all_results:
        final_df = pd.concat(all_results)
        print(f"准备更新 {len(final_df)} 条记录到因子库...")
        
        with engine.begin() as conn:
            final_df.to_sql('temp_factors_inc', con=conn, if_exists='replace', index=False)
            upsert_sql = text("""
                INSERT INTO stk_factors (symbol, trade_date, f_ma_cohesion, f_vol_ratio, f_rsi_14, f_mom_20, f_dist_high, f_macd_dif, f_macd_dea, f_macd_hist)
                SELECT * FROM temp_factors_inc
                ON DUPLICATE KEY UPDATE 
                f_ma_cohesion=VALUES(f_ma_cohesion), f_vol_ratio=VALUES(f_vol_ratio),
                f_rsi_14=VALUES(f_rsi_14), f_mom_20=VALUES(f_mom_20),
                f_dist_high=VALUES(f_dist_high), f_macd_dif=VALUES(f_macd_dif),
                f_macd_dea=VALUES(f_macd_dea), f_macd_hist=VALUES(f_macd_hist);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE temp_factors_inc;"))
        print("✅ 增量因子更新完成！")
    else:
        print("没有新数据需要更新。")

if __name__ == "__main__":
    update_factors_incremental()