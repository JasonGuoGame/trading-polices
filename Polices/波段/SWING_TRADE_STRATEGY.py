import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def run_swing_strategy():
    print(f"[{datetime.datetime.now()}] 正在全市场执行波段交易筛选...")
    
    # 1. 加载代码与名称映射 (从 stocks 表)
    query_names = "SELECT symbol, name FROM stocks"
    df_names = pd.read_sql(query_names, engine)
    # 转为字典提高查找速度: { '000001.SZ': '平安银行' }
    name_map = dict(zip(df_names['symbol'], df_names['name']))

    # 2. 加载最近 120 天日线数据
    query_kline = """
    SELECT * FROM stk_daily_kline 
    WHERE trade_date >= (
        SELECT MIN(t.trade_date) FROM (
            SELECT DISTINCT trade_date FROM stk_daily_kline 
            ORDER BY trade_date DESC LIMIT 120
        ) AS t
    )
    ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query_kline, engine)
    
    if df_all.empty: return

    # 3. 加载板块信息
    query_relation = "SELECT symbol, sector_name FROM stock_sector_relation"
    df_relation = pd.read_sql(query_relation, engine)

    results = []
    
    # 按股票分组计算
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 30: continue
        
        # 只做沪深主板
        if not symbol.startswith(('60', '00')): continue

        # --- 计算技术指标 ---
        df['MA5'] = ta.sma(df['close'], length=5)
        df['MA10'] = ta.sma(df['close'], length=10)
        df['MA20'] = ta.sma(df['close'], length=20)
        df['V_MA5'] = ta.sma(df['volume'], length=5)
        df['rolling_high_20'] = df['high'].shift(1).rolling(20).max()
        df['RSI'] = ta.rsi(df['close'], length=14)
        
        curr = df.iloc[-1]
        
        # --- 策略条件判断 ---
        # A. 均线粘合度
        ma_list = [curr['MA5'], curr['MA10'], curr['MA20']]
        cohesion = (max(ma_list) - min(ma_list)) / curr['MA20']
        is_cohesion = cohesion < 0.03
        
        # B. 启动信号
        is_breakout = curr['close'] > curr['rolling_high_20']
        is_vol_up = curr['volume'] > 2.0 * curr['V_MA5']
        
        # C. 多头排列雏形
        is_bullish = curr['MA5'] > curr['MA10'] > curr['MA20']
        
        # D. 筹码状态
        total_turnover_60 = df['turnover_rate'].tail(60).sum()
        is_chips_clean = total_turnover_60 > 100

        # --- 综合逻辑 ---
        if is_breakout and is_vol_up and is_cohesion and is_bullish:
            # 查找所属板块
            sectors = df_relation[df_relation['symbol'] == symbol]['sector_name'].tolist()
            sector_str = ",".join(sectors[:3])

            # 获取股票名称
            stock_name = name_map.get(symbol, "未知")

            results.append({
                '代码': symbol,
                '名称': stock_name,  # <--- 新增字段
                '收盘价': curr['close'],
                '成交量倍数': round(curr['volume'] / curr['V_MA5'], 2),
                '均线粘合': f"{round(cohesion*100, 2)}%",
                '60日换手': round(total_turnover_60, 2),
                'RSI': round(curr['RSI'], 2),
                '所属板块': sector_str
            })

    # 输出结果
    if results:
        res_df = pd.DataFrame(results).sort_values('均线粘合', ascending=True)
        # 调整列顺序，让代码和名称在一起
        cols = ['代码', '名称', '收盘价', '成交量倍数', '均线粘合', '60日换手', 'RSI', '所属板块']
        res_df = res_df[cols]
        
        print("\n" + "🚀" * 10 + " 发现波段起爆潜力股 " + "🚀" * 10)
        print("-" * 110)
        print(res_df.to_string(index=False))
        print("-" * 110)
        print("💡 操作建议：观察今日是否有主力大单持续买入，若回调不破均线则为介入点。")
    else:
        print("\n今日未发现符合波段起爆条件的个股。")

if __name__ == "__main__":
    run_swing_strategy()