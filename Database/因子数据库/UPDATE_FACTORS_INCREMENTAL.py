import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime
import warnings
import numpy as np

# 屏蔽无关警告
warnings.filterwarnings('ignore')

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)
LOOKBACK_WINDOW = 180 

def get_elapsed_minutes():
    """计算当前时间对应的交易分钟数(1-240)"""
    now = datetime.datetime.now()
    curr_time = now.time()
    
    if curr_time < datetime.time(9, 30):
        return 1
    elif curr_time < datetime.time(11, 30):
        return (now.hour - 9) * 60 + now.minute - 30
    elif curr_time < datetime.time(13, 0):
        return 120
    elif curr_time < datetime.time(15, 0):
        return 120 + (now.hour - 13) * 60 + now.minute
    else:
        return 240

def update_factors_with_overwrite():
    print(f"[{datetime.datetime.now()}] 启动因子库覆盖更新系统(修复布林带并含量比)...")

    # 1. 获取进度
    with engine.connect() as conn:
        progress_df = pd.read_sql("SELECT symbol, MAX(trade_date) as last_date FROM stk_factors GROUP BY symbol", conn)
        last_date_map = dict(zip(progress_df['symbol'], pd.to_datetime(progress_df['last_date']).dt.date))

    # 2. 读取行情数据
    query_kline = f"""
        SELECT symbol, trade_date, open, high, low, close, volume, amount
        FROM stk_daily_kline 
        WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL {LOOKBACK_WINDOW} DAY)
        ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query_kline, engine)
    if df_all.empty: return
    df_all['trade_date'] = pd.to_datetime(df_all['trade_date']).dt.date
    
    # 获取实时计算所需的参数
    m = get_elapsed_minutes()
    today_date = datetime.date.today()

    all_results = []
    print(f"开始并行计算并修正 MACD/BOLL(修复上穿)/量比 映射...")

    # 3. 分组计算
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 35: continue 
        
        try:
            df = df.sort_values('trade_date').drop_duplicates('trade_date')
            
            # --- 指标计算 ---
            ma5 = ta.sma(df['close'], length=5)
            ma10 = ta.sma(df['close'], length=10)
            ma20 = ta.sma(df['close'], length=20)
            v_ma60 = ta.sma(df['volume'], length=60).fillna(df['volume'].expanding().mean())
            
            # 1. 量比计算
            v_ma5_yest = df['volume'].shift(1).rolling(5).mean()
            df['f_quantity_ratio'] = (df['volume'] / 240) / (v_ma5_yest / 240 + 0.001)
            if df['trade_date'].iloc[-1] >= today_date:
                realtime_qr = (df['volume'].iloc[-1] / m) / (v_ma5_yest.iloc[-1] / 240 + 0.01)
                df.iloc[-1, df.columns.get_loc('f_quantity_ratio')] = realtime_qr
            
            # 2. MACD & BOLL & RSI
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            bb = ta.bbands(df['close'], length=20, std=2)
            rsi = ta.rsi(df['close'], length=14)
            h120 = df['high'].rolling(120, min_periods=1).max()

            # --- 结果组装 ---
            res = pd.DataFrame({'symbol': symbol, 'trade_date': df['trade_date']})
            ma_stack = pd.concat([ma5, ma10, ma20], axis=1)
            res['f_ma_cohesion'] = (ma_stack.max(axis=1) - ma_stack.min(axis=1)) / (ma20 + 0.001)
            res['f_vol_ratio'] = df['volume'] / (v_ma60 + 0.01)
            res['f_rsi_14'] = rsi.fillna(50)
            res['f_mom_20'] = df['close'].pct_change(20).fillna(0)
            res['f_dist_high'] = (h120 - df['close']) / (h120 + 0.001)
            res['f_quantity_ratio'] = df['f_quantity_ratio'].fillna(1.0)
            
            # MACD 映射
            if macd is not None:
                res['f_macd_dif'] = macd.iloc[:, 0]
                res['f_macd_dea'] = macd.iloc[:, 2] # 真正的信号线
                res['f_macd_hist'] = macd.iloc[:, 1] * 2
            
            # 布林带映射 (修复位置)
            if bb is not None:
                # pandas_ta 顺序为: Lower, Mid, Upper (0, 1, 2)
                res['f_bb_l'] = bb.iloc[:, 0]
                res['f_bb_m'] = bb.iloc[:, 1]
                res['f_bb_u'] = bb.iloc[:, 2]

            # --- E. 增量切片 ---
            last_sync = last_date_map.get(symbol)
            if last_sync:
                res = res[res['trade_date'] >= last_sync]
            
            res = res.dropna(subset=['f_ma_cohesion'])
            if not res.empty:
                all_results.append(res)
                
        except Exception:
            continue

    # 4. 执行全量 UPSERT
    if all_results:
        final_df = pd.concat(all_results)
        print(f"🚀 准备更新 {len(final_df)} 条记录(含 BB 修复与量比)...")
        
        with engine.begin() as conn:
            final_df.to_sql('temp_factors_overwrite', con=conn, if_exists='replace', index=False)
            
            # 核心修正：SQL 的 SELECT 部分不再使用 *，而是显式列出字段名，确保与 INSERT 严格对齐
            upsert_sql = text("""
                INSERT INTO stk_factors (
                    symbol, trade_date, f_ma_cohesion, f_vol_ratio, f_rsi_14, 
                    f_mom_20, f_dist_high, f_macd_dif, f_macd_dea, f_macd_hist,
                    f_bb_u, f_bb_m, f_bb_l, f_quantity_ratio
                )
                SELECT 
                    symbol, trade_date, f_ma_cohesion, f_vol_ratio, f_rsi_14, 
                    f_mom_20, f_dist_high, f_macd_dif, f_macd_dea, f_macd_hist,
                    f_bb_u, f_bb_m, f_bb_l, f_quantity_ratio
                FROM temp_factors_overwrite
                ON DUPLICATE KEY UPDATE 
                    f_ma_cohesion = VALUES(f_ma_cohesion),
                    f_vol_ratio = VALUES(f_vol_ratio),
                    f_rsi_14 = VALUES(f_rsi_14),
                    f_mom_20 = VALUES(f_mom_20),
                    f_dist_high = VALUES(f_dist_high),
                    f_macd_dif = VALUES(f_macd_dif),
                    f_macd_dea = VALUES(f_macd_dea),
                    f_macd_hist = VALUES(f_macd_hist),
                    f_bb_u = VALUES(f_bb_u),
                    f_bb_m = VALUES(f_bb_m),
                    f_bb_l = VALUES(f_bb_l),
                    f_quantity_ratio = VALUES(f_quantity_ratio);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_factors_overwrite;"))
        print(f"✅ 因子同步完成！布林带轨道已校正。")
    else:
        print("💡 无需更新。")

if __name__ == "__main__":
    update_factors_with_overwrite()