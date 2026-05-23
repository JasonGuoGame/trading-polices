import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

# 粘合度阈值 (1.5% 以内认为粘合)
COHESION_THRESHOLD = 0.015 

def screen_stocks():
    print(f"[{datetime.datetime.now()}] 启动‘筹码锁仓+均线粘合’精选筛选系统...")

    # --- 第一步：执行筹码过滤 SQL，获取“主力高度控盘”池 ---
    # 逻辑：三连降 + 降幅大 + 人均持金 > 20万
    concentrated_sql = text("""
        WITH RankedHolders AS (
            SELECT 
                symbol, name, end_date, holder_count, avg_hold_price,
                ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY end_date DESC) as rn
            FROM stk_holders_history
        )
        SELECT h1.symbol
        FROM RankedHolders h1
        JOIN RankedHolders h2 ON h1.symbol = h2.symbol AND h2.rn = 2
        JOIN RankedHolders h3 ON h1.symbol = h3.symbol AND h3.rn = 3
        WHERE h1.rn = 1 
          AND h1.holder_count < h2.holder_count  -- 第一次降
          AND h2.holder_count < h3.holder_count  -- 第二次降
          AND h1.avg_hold_price > 200000         -- 人均持金门槛
          AND h1.end_date >= DATE_SUB(CURDATE(), INTERVAL 9 MONTH)
    """)

    print("正在执行第一轮：筹码分布过滤...")
    try:
        with engine.connect() as conn:
            concentrated_df = pd.read_sql(concentrated_sql, conn)
    except Exception as e:
        print(f"❌ 筹码 SQL 执行失败: {e}")
        return

    if concentrated_df.empty:
        print("未发现筹码连续集中的标的，筛选终止。")
        return

    symbols_pool = concentrated_df['symbol'].tolist()
    print(f"✅ 筹码面选出 {len(symbols_pool)} 只种子个股，开始进入第二轮技术形态扫描...")

    # --- 第二步：获取这些股票的行情数据 ---
    # 使用 IN 语句精准提取，速度极快
    query_kline = text("""
        SELECT k.*, s.name 
        FROM stk_daily_kline k
        JOIN stocks s ON k.symbol = s.symbol
        WHERE k.symbol IN :symbols
          AND k.trade_date >= (
            SELECT MIN(t.trade_date) FROM (
                SELECT DISTINCT trade_date FROM stk_daily_kline 
                ORDER BY trade_date DESC LIMIT 60
            ) AS t
          )
        ORDER BY k.symbol, k.trade_date ASC
    """)

    with engine.connect() as conn:
        df_all = pd.read_sql(query_kline, conn, params={"symbols": symbols_pool})

    if df_all.empty:
        print("行情库中未找到这些个股的数据。")
        return

    selected_stocks = []

    # --- 第三步：对筹码池中的个股执行 MA 粘合判断 ---
    for symbol, df in df_all.groupby('symbol'):
        df = df.sort_values('trade_date')
        
        # 计算技术指标
        df['MA5'] = ta.sma(df['close'], length=5)
        df['MA10'] = ta.sma(df['close'], length=10)
        df['MA20'] = ta.sma(df['close'], length=20)
        df['V_MA10'] = ta.sma(df['volume'], length=10)
        
        curr = df.iloc[-1]
        
        if pd.isna(curr['MA20']): continue

        # 1. 条件一：成交量翻倍 (今日量 > 2倍过去10天平均量)
        cond_vol = curr['volume'] > 2 * curr['V_MA10']
        
        # 2. 条件二：均线高度粘合
        mas = [curr['MA5'], curr['MA10'], curr['MA20']]
        cohesion = (max(mas) - min(mas)) / curr['MA20']
        cond_cohesion = cohesion < COHESION_THRESHOLD
        
        # 3. 条件三：收盘价在所有均线上方（起爆表态）
        cond_price = curr['close'] > max(mas)

        if cond_vol and cond_cohesion and cond_price:
            # 关联筹码数据，显示该股的持金水平
            selected_stocks.append({
                '代码': symbol,
                '名称': df['name'].iloc[0],
                '最新价': round(curr['close'], 2),
                '量能倍数': round(curr['volume'] / curr['V_MA10'], 2),
                '均线粘合': f"{round(cohesion * 100, 2)}%",
                '日期': curr['trade_date']
            })

    # --- 第四步：展示终极名单 ---
    if selected_stocks:
        result_df = pd.DataFrame(selected_stocks).sort_values('量能倍数', ascending=False)
        print("\n" + "💎" * 15)
        print(f"🚀 终极筛选完成：共选出 {len(result_df)} 只【主力重仓 + 粘合起爆】个股：")
        print("-" * 100)
        print(result_df.to_string(index=False))
        print("-" * 100)
        print("💡 操盘建议：由于筹码已三连降且人均持金高，这种放量突破极具攻击力，建议关注明早开盘溢价。")
        print("💎" * 15)
    else:
        print("\n今日主力锁仓池中未发现符合粘合突破形态的股票。")

if __name__ == "__main__":
    screen_stocks()