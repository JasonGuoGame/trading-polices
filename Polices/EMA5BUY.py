import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def get_latest_trade_date():
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).scalar()
    return res

# ---------------------------------------------------------
# 1. 核心计算函数 (增加涨停过滤)
# ---------------------------------------------------------
def check_daily_buy_point(df_group):
    df_group = df_group.sort_values('trade_date')
    if len(df_group) < 60: return None
    
    # 获取最后两行数据
    last_row = df_group.iloc[-1]
    prev_row = df_group.iloc[-2]

    # --- 核心改进：过滤涨停 ---
    # 计算今日涨幅
    pct_chg = (last_row['close'] - prev_row['close']) / prev_row['close']
    
    # 1. 如果涨幅超过 9.5%，视为涨停或准涨停，直接剔除
    if pct_chg > 0.095:
        return None
    
    # 2. 如果收盘价等于最高价，且涨幅较高，也视为封板状态，剔除
    if last_row['close'] == last_row['high'] and pct_chg > 0.07:
        return None

    # --- 原有 EMA 策略逻辑 ---
    # 计算 EMA 指标
    df_group['ema5'] = ta.ema(df_group['close'], length=5)
    df_group['ema20'] = ta.ema(df_group['close'], length=20)
    df_group['ema60'] = ta.ema(df_group['close'], length=60)
    df_group['vol_ma5'] = df_group['volume'].rolling(5).mean()
    
    # 更新带有指标的行数据
    last_row = df_group.iloc[-1]
    prev_row = df_group.iloc[-2]
    
    # 判定条件
    cond_gold_cross = (prev_row['ema5'] <= prev_row['ema20']) and (last_row['ema5'] > last_row['ema20'])
    cond_ema60_up = last_row['ema60'] > prev_row['ema60']
    cond_above_60 = last_row['close'] > last_row['ema60']
    cond_red = last_row['close'] > last_row['open']
    cond_vol = last_row['volume'] > last_row['vol_ma5']
    
    if cond_gold_cross and cond_ema60_up and cond_above_60 and cond_red and cond_vol:
        return last_row
    
    return None

# ---------------------------------------------------------
# 2. 执行选股程序
# ---------------------------------------------------------
def run_daily_ema_selector():
    today = get_latest_trade_date()
    if not today: return
    print(f"🚀 开始扫描日线级别【第一买点】(已过滤涨停)，基准日期: {today}")

    start_date = (today - datetime.timedelta(days=200)).strftime('%Y-%m-%d')
    query = f"""
    SELECT symbol, trade_date, open, high, low, close, volume 
    FROM stk_daily_kline 
    WHERE trade_date >= '{start_date}'
    ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(text(query), engine)
    
    if df_all.empty: return

    # 分组计算
    print("   正在计算指标并过滤信号...")
    results = df_all.groupby('symbol').apply(check_daily_buy_point)
    
    # 清理并重置索引，解决 KeyError: 'symbol'
    df_results = results.dropna(how='all')
    
    if not df_results.empty:
        df_results = df_results.reset_index()

        # 获取股票名称
        symbols_str = ",".join([f"'{s}'" for s in df_results['symbol'].unique()])
        name_sql = f"SELECT symbol, name FROM stocks WHERE symbol IN ({symbols_str})"
        df_names = pd.read_sql(text(name_sql), engine)
        
        # 合并显示
        df_display = df_results.merge(df_names, on='symbol', how='left')
        
        print("\n" + "="*85)
        print(f"🎯 日线第一买点选股结果 (已过滤当日涨停，共 {len(df_display)} 只)")
        print("-" * 85)
        cols = ['trade_date', 'symbol', 'name', 'close', 'ema5', 'ema20']
        print(df_display[[c for c in cols if c in df_display.columns]].to_string(index=False))
        print("="*85)
    else:
        print("❌ 今日无符合条件的个股。")

if __name__ == "__main__":
    run_daily_ema_selector()