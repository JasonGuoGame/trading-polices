import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import numpy as np

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def backfill_factors_pro():
    print("正在从数据库加载行情，准备计算全因子（含MACD）...")
    
    # 1. 加载全量数据
    query = "SELECT symbol, trade_date, open, high, low, close, volume FROM stk_daily_kline ORDER BY symbol, trade_date ASC"
    df_all = pd.read_sql(query, engine)
    
    all_results = []
    
    # 2. 分组计算
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 35: continue # MACD 至少需要34天数据才能稳定
        
        try:
            # --- 原始 5 因子计算 ---
            ma5 = ta.sma(df['close'], length=5)
            ma10 = ta.sma(df['close'], length=10)
            ma20 = ta.sma(df['close'], length=20)
            v_ma60 = ta.sma(df['volume'], length=60)
            rsi = ta.rsi(df['close'], length=14)
            high_120 = df['high'].rolling(120, min_periods=1).max()

            # --- 新增 MACD 因子计算 ---
            # 返回 DIF, DEA, HIST (默认参数 12, 26, 9)
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            
            # --- 整合数据 ---
            res = pd.DataFrame(index=df.index)
            res['symbol'] = symbol
            res['trade_date'] = df['trade_date']
            
            # 均线粘合度
            ma_stack = pd.concat([ma5, ma10, ma20], axis=1)
            res['f_ma_cohesion'] = (ma_stack.max(axis=1) - ma_stack.min(axis=1)) / ma20
            res['f_vol_ratio'] = df['volume'] / v_ma60
            res['f_rsi_14'] = rsi
            res['f_mom_20'] = (df['close'] - df['close'].shift(20)) / df['close'].shift(20)
            res['f_dist_high'] = (high_120 - df['close']) / high_120
            
            # MACD 字段映射
            res['f_macd_dif'] = macd['MACD_12_26_9']
            res['f_macd_dea'] = macd['MACDs_12_26_9']
            res['f_macd_hist'] = macd['MACDh_12_26_9']

            # 剔除空值并加入列表
            all_results.append(res.dropna())
            
        except Exception as e:
            continue

    # 3. 批量 Upsert 写入
    if all_results:
        final_df = pd.concat(all_results)
        print(f"计算完成，准备同步 {len(final_df)} 条数据到 stk_factors...")
        
        with engine.begin() as conn:
            # 创建临时表
            final_df.to_sql('temp_factors', con=conn, if_exists='replace', index=False)
            # 使用 UPSERT 语法更新
            upsert_sql = text("""
                INSERT INTO stk_factors (symbol, trade_date, f_ma_cohesion, f_vol_ratio, f_rsi_14, f_mom_20, f_dist_high, f_macd_dif, f_macd_dea, f_macd_hist)
                SELECT * FROM temp_factors
                ON DUPLICATE KEY UPDATE 
                f_ma_cohesion=VALUES(f_ma_cohesion), f_vol_ratio=VALUES(f_vol_ratio),
                f_rsi_14=VALUES(f_rsi_14), f_mom_20=VALUES(f_mom_20),
                f_dist_high=VALUES(f_dist_high), f_macd_dif=VALUES(f_macd_dif),
                f_macd_dea=VALUES(f_macd_dea), f_macd_hist=VALUES(f_macd_hist);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE temp_factors;"))
        print("✅ 因子库升级补全成功！")

if __name__ == "__main__":
    backfill_factors_pro()