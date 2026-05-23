import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def find_consolidation_concentrated_stocks():
    print(f"[{datetime.datetime.now()}] 正在搜寻‘筹码高度集中+横盘蓄势’的黑马股...")

    # 1. 第一步：利用 CTE (公用表表达式) 和 ROW_NUMBER 锁定最近三期数据
    # 逻辑：
    # h1 为最新一期 (rn=1)
    # h2 为往回推一期 (rn=2)
    # h3 为往回推两期 (rn=3)
    holder_sql = """
    WITH RankedHolders AS (
        SELECT 
            symbol, name, holder_count, change_rate, end_date,
            ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY end_date DESC) as rn
        FROM stk_holders_history
    )
    SELECT 
        h1.symbol, 
        h1.name, 
        h1.holder_count as count1, 
        h2.holder_count as count2, 
        h3.holder_count as count3,
        h1.change_rate as last_change,
        h1.end_date as latest_date
    FROM RankedHolders h1
    JOIN RankedHolders h2 ON h1.symbol = h2.symbol AND h2.rn = 2
    JOIN RankedHolders h3 ON h1.symbol = h3.symbol AND h3.rn = 3
    WHERE h1.rn = 1
      AND h1.holder_count < h2.holder_count  -- 最新比上期少
      AND h2.holder_count < h3.holder_count  -- 上期比上上期少
      AND h1.end_date >= DATE_SUB(CURDATE(), INTERVAL 9 MONTH) -- 必须是近期的公告
    """
    
    try:
        with engine.connect() as conn:
            df_holders = pd.read_sql(text(holder_sql), conn)
    except Exception as e:
        print(f"❌ SQL执行失败: {e}")
        return

    if df_holders.empty:
        print("未发现筹码三连降的个股。")
        return

    print(f"初步发现 {len(df_holders)} 只筹码持续集中的个股，正在扫描形态...")

    results = []
    
    # 2. 第二步：在这些股票中筛选“横盘震荡”形态
    for _, row in df_holders.iterrows():
        symbol = row['symbol']
        
        # 增加 LIMIT 60 确保有足够数据，ORDER BY trade_date DESC 确保是最近的
        query_kline = f"""
            SELECT close, high, low FROM stk_daily_kline 
            WHERE symbol = '{symbol}' 
            ORDER BY trade_date DESC LIMIT 60
        """
        df_k = pd.read_sql(query_kline, engine)
        
        if len(df_k) < 60: 
            continue
        
        # 计算 60 日振幅 (Box Range)
        high_60 = df_k['high'].max()
        low_60 = df_k['low'].min()
        # 注意：这里的 amplitude 计算是以“这一段时间内最高/最低”对比，看是否横盘
        amplitude = (high_60 - low_60) / df_k['close'].iloc[0]
        
        # --- 筛选门槛 ---
        # 1. 振幅小于 20% (长期横盘，弹簧压缩)
        # 2. 当前价格在 20 日均线附近 (未脱离底部)
        ma20 = df_k['close'].head(20).mean()
        curr_price = df_k['close'].iloc[0]
        
        if amplitude < 0.20 and abs(curr_price - ma20)/ma20 < 0.06:
            # 计算总降幅 (从 h3 到 h1)
            total_drop = (row['count1'] - row['count3']) / row['count3'] * 100
            
            results.append({
                '代码': symbol,
                '名称': row['name'],
                '最新户数': row['count1'],
                '三期总降幅%': round(total_drop, 2),
                '60日振幅%': round(amplitude * 100, 2),
                '最新价格': curr_price,
                '公告日期': row['latest_date']
            })

    # 3. 输出结果
    if results:
        res_df = pd.DataFrame(results).sort_values('三期总降幅%', ascending=True)
        print("\n" + "💎" * 15)
        print(f"🚀 发现【横盘蓄势 + 筹码三连降】个股清单 (截止: {datetime.date.today()})")
        print("-" * 90)
        # 打印关键列
        print(res_df.to_string(index=False))
        print("-" * 90)
        print("💡 结论：这些票筹码已经从散户转移到大户手中，且股价未启动，耐心等待放量信号。")
    else:
        print("未发现符合形态的个股。")

if __name__ == "__main__":
    find_consolidation_concentrated_stocks()