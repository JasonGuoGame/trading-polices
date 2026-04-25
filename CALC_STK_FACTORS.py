import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import numpy as np

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def calc_and_save_factors():
    print("开始从数据库读取历史行情...")
    
    # 1. 只需要读取最近 120 天的数据即可计算大部分因子
    query = """
    SELECT * FROM stk_daily_kline 
    WHERE trade_date >= (
        SELECT MIN(t.trade_date) FROM (
            SELECT DISTINCT trade_date FROM stk_daily_kline 
            ORDER BY trade_date DESC LIMIT 120
        ) AS t
    )
    ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    if df_all.empty:
        print("错误：行情数据库为空！")
        return

    # 2. 分组计算因子
    all_factors = []
    print(f"正在为 {df_all['symbol'].nunique()} 只股票计算因子...")
    
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 30: continue
        
        # --- 计算因子逻辑 ---
        # 1. 均线粘合度 (MA5, 10, 20)
        ma5 = ta.sma(df['close'], length=5)
        ma10 = ta.sma(df['close'], length=10)
        ma20 = ta.sma(df['close'], length=20)
        
        # 2. 60日成交量均线 (用于计算地量比)
        vol_ma60 = ta.sma(df['volume'], length=60)
        
        # 3. RSI 14
        rsi = ta.rsi(df['close'], length=14)
        
        # 4. 120日最高价 (用于计算压力位距离)
        high_120 = df['high'].rolling(120, min_periods=1).max()

        # 构造因子 DataFrame
        df_f = pd.DataFrame(index=df.index)
        df_f['symbol'] = symbol
        df_f['trade_date'] = df['trade_date']
        
        # 计算具体数值
        df_f['f_ma_cohesion'] = (ma5 - ma20).abs() / ma20
        df_f['f_vol_ratio'] = df['volume'] / vol_ma60
        df_f['f_rsi_14'] = rsi
        df_f['f_mom_20'] = (df['close'] - df['close'].shift(20)) / df['close'].shift(20)
        df_f['f_dist_high'] = (high_120 - df['close']) / high_120
        
        # 我们只需要最新一天的因子数据进行存储（增量更新）
        # 如果你想存全量，就把 .tail(1) 去掉
        latest_factor = df_f.tail(1).dropna(subset=['f_ma_cohesion'])
        
        if not latest_factor.empty:
            all_factors.append(latest_factor)

    # 3. 汇总并写入数据库
    if all_factors:
        df_final = pd.concat(all_factors)
        print(f"计算完成，准备写入 {len(df_final)} 条因子数据...")
        
        # 使用 SQLAlchemy 原生连接进行 UPSERT 写入
        with engine.begin() as conn:
            # 创建临时表写入
            df_final.to_sql('temp_factors', con=conn, if_exists='replace', index=False)
            
            # 使用 INSERT INTO ... SELECT ... ON DUPLICATE KEY UPDATE 确保不重复
            upsert_sql = text("""
                INSERT INTO stk_factors 
                SELECT * FROM temp_factors
                ON DUPLICATE KEY UPDATE 
                f_ma_cohesion = VALUES(f_ma_cohesion),
                f_vol_ratio = VALUES(f_vol_ratio),
                f_rsi_14 = VALUES(f_rsi_14),
                f_mom_20 = VALUES(f_mom_20),
                f_dist_high = VALUES(f_dist_high);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE temp_factors;"))
            
        print("✅ 因子表更新成功！")
    else:
        print("未生成有效因子数据。")

if __name__ == "__main__":
    calc_and_save_factors()