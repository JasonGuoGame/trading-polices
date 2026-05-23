import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def run_swing_strategy_with_chips():
    print(f"[{datetime.datetime.now()}] 启动【筹码精选池】波段交易筛选...")

    # 1. 第一阶段：通过 SQL 锁定“筹码高度集中”的股票池
    # 逻辑：股东人数三连降 + 人均持金 > 20万
    concentrated_sql = text("""
        WITH RankedHolders AS (
            SELECT 
                symbol, name, end_date, holder_count, avg_hold_price,
                ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY end_date DESC) as rn
            FROM stk_holders_history
        )
        SELECT h1.symbol, h1.name
        FROM RankedHolders h1
        JOIN RankedHolders h2 ON h1.symbol = h2.symbol AND h2.rn = 2
        JOIN RankedHolders h3 ON h1.symbol = h3.symbol AND h3.rn = 3
        WHERE h1.rn = 1 
          AND h1.holder_count < h2.holder_count  -- 第一次降
          AND h2.holder_count < h3.holder_count  -- 第二次降
          AND h1.avg_hold_price > 200000         -- 门槛：人均持金 > 20万
          AND h1.end_date >= DATE_SUB(CURDATE(), INTERVAL 9 MONTH)
    """)

    try:
        with engine.connect() as conn:
            concentrated_pool = pd.read_sql(concentrated_sql, conn)
    except Exception as e:
        print(f"❌ 筹码 SQL 执行失败: {e}")
        return

    if concentrated_pool.empty:
        print("未发现筹码高度集中的标的。")
        return

    symbols_list = concentrated_pool['symbol'].tolist()
    name_map = dict(zip(concentrated_pool['symbol'], concentrated_pool['name']))
    print(f"✅ 筹码面已筛选出 {len(symbols_list)} 只个股，正在扫描波段技术形态...")

    # 2. 第二阶段：仅加载这些个股的 120 天日线数据 (大幅提升速度)
    query_kline = text("""
        SELECT * FROM stk_daily_kline 
        WHERE symbol IN :symbols
        AND trade_date >= (
            SELECT MIN(t.trade_date) FROM (
                SELECT DISTINCT trade_date FROM stk_daily_kline 
                ORDER BY trade_date DESC LIMIT 120
            ) AS t
        )
        ORDER BY symbol, trade_date ASC
    """)
    
    with engine.connect() as conn:
        df_all = pd.read_sql(query_kline, conn, params={"symbols": symbols_list})

    if df_all.empty: return

    # 3. 加载板块信息
    query_relation = "SELECT symbol, sector_name FROM stock_sector_relation"
    df_relation = pd.read_sql(query_relation, engine)

    results = []
    
    # 4. 按股票分组计算技术指标
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
        is_cohesion = cohesion < 0.03  # 粘合在3%以内
        
        # B. 启动信号
        is_breakout = curr['close'] > curr['rolling_high_20']
        is_vol_up = curr['volume'] > 2.0 * curr['V_MA5']
        
        # C. 多头排列雏形
        is_bullish = curr['MA5'] > curr['MA10'] > curr['MA20']
        
        # D. 筹码状态（60日换手充分）
        total_turnover_60 = df['turnover_rate'].tail(60).sum()
        is_chips_clean = total_turnover_60 > 100

        # --- 综合逻辑判断 ---
        if is_breakout and is_vol_up and is_cohesion and is_bullish:
            # 查找所属板块
            sectors = df_relation[df_relation['symbol'] == symbol]['sector_name'].tolist()
            # 过滤干扰，只取行业和概念
            filtered_sectors = [s for s in sectors if '概念' in s or 'THY' in s or 'SW' in s]
            sector_str = ",".join(filtered_sectors[:3])

            results.append({
                '代码': symbol,
                '名称': name_map.get(symbol, "未知"),
                '收盘价': curr['close'],
                '成交量倍数': round(curr['volume'] / curr['V_MA5'], 2),
                '均线粘合': f"{round(cohesion*100, 2)}%",
                '60日换手': round(total_turnover_60, 2),
                'RSI': round(curr['RSI'], 2),
                '所属板块': sector_str
            })

    # 5. 输出结果
    if results:
        res_df = pd.DataFrame(results).sort_values('均线粘合', ascending=True)
        cols = ['代码', '名称', '收盘价', '成交量倍数', '均线粘合', '60日换手', 'RSI', '所属板块']
        res_df = res_df[cols]
        
        print("\n" + "💎" * 10 + " 发现【筹码精选池】波段起爆潜力股 " + "💎" * 10)
        print("-" * 120)
        print(res_df.to_string(index=False))
        print("-" * 120)
        print("💡 操盘建议：")
        print("1. 名单中个股已通过‘股东人数三连降’和‘人均持金20万’硬核筛选。")
        print("2. 这种‘主力锁仓+均线粘合突破’的形态，一旦启动，往往是板块级的主升浪。")
    else:
        print("\n今日筹码精选池中，未发现符合波段起爆条件的个股。")

if __name__ == "__main__":
    run_swing_strategy_with_chips()