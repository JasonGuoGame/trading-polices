import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def find_explosive_stocks():
    print(f"[{datetime.datetime.now()}] 正在全市场搜寻‘筹码高度集中+横盘起爆’的标的...")

    # 1. 步骤一：SQL 筛选筹码总降幅 > 15% 的个股
    holder_sql = """
    WITH RankedHolders AS (
        SELECT 
            symbol, name, holder_count, end_date,
            ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY end_date DESC) as rn
        FROM stk_holders_history
    )
    SELECT 
        h1.symbol, h1.name, 
        h1.holder_count as count1, h3.holder_count as count3,
        ((h1.holder_count - h3.holder_count) / h3.holder_count * 100) as total_drop_pct
    FROM RankedHolders h1
    JOIN RankedHolders h3 ON h1.symbol = h3.symbol AND h3.rn = 3
    WHERE h1.rn = 1
      AND ((h1.holder_count - h3.holder_count) / h3.holder_count) <= -0.15  -- 降幅超过15%
      AND h1.end_date >= DATE_SUB(CURDATE(), INTERVAL 9 MONTH)
    """
    
    try:
        with engine.connect() as conn:
            df_holders = pd.read_sql(text(holder_sql), conn)
    except Exception as e:
        print(f"❌ SQL执行失败: {e}")
        return

    if df_holders.empty:
        print("未发现筹码降幅达标的个股。")
        return

    results = []
    
    # 2. 步骤二：扫描行情（横盘、爆量、均线发散）
    print(f"已锁定 {len(df_holders)} 只筹码集中股，正在扫描技术面共振...")

    for _, row in df_holders.iterrows():
        symbol = row['symbol']
        
        # 读取最近 80 天行情用于计算均线和振幅
        query_kline = f"""
            SELECT trade_date, open, close, high, low, volume 
            FROM stk_daily_kline 
            WHERE symbol = '{symbol}' 
            ORDER BY trade_date ASC
        """
        df_k = pd.read_sql(query_kline, engine)
        
        if len(df_k) < 60: continue
        
        # --- A. 指标计算 ---
        df_k['MA5'] = ta.sma(df_k['close'], length=5)
        df_k['MA10'] = ta.sma(df_k['close'], length=10)
        df_k['MA20'] = ta.sma(df_k['close'], length=20)
        df_k['V_MA20'] = ta.sma(df_k['volume'], length=20)
        
        curr = df_k.iloc[-1]
        prev = df_k.iloc[-2]

        # --- B. 条件过滤 ---
        
        # 1. 极致横盘判断 (过去 60 日振幅在 8% - 16% 之间)
        high_60 = df_k['high'].tail(60).max()
        low_60 = df_k['low'].tail(60).min()
        amplitude = (high_60 - low_60) / curr['close']
        is_consolidation = 0.05 < amplitude < 0.16
        
        # 2. 成交量突变 (今日成交量 > 20日均量的 2 倍)
        is_vol_spike = curr['volume'] > (curr['V_MA20'] * 2.0)
        
        # 3. 均线多头发散 (MA5 > MA10 > MA20 且 MA5 向上拐头)
        is_ma_divergence = (curr['MA5'] > curr['MA10'] > curr['MA20']) and (curr['MA5'] > prev['MA5'])
        
        # 4. 价格表态 (今日收盘涨幅 > 3%)
        is_price_up = (curr['close'] - prev['close']) / prev['close'] > 0.03

        # --- C. 综合判定 ---
        if is_consolidation and is_vol_spike and is_ma_divergence and is_price_up:
            results.append({
                '代码': symbol,
                '名称': row['name'],
                '筹码总降幅%': round(row['total_drop_pct'], 2),
                '60日振幅%': round(amplitude * 100, 2),
                '成交量/均量': round(curr['volume'] / curr['V_MA20'], 2),
                '最新价': curr['close'],
                '今日涨幅%': round((curr['close']-prev['close'])/prev['close']*100, 2)
            })

    # 3. 输出报告
    if results:
        res_df = pd.DataFrame(results).sort_values('筹码总降幅%', ascending=True)
        print("\n" + "🚀" * 10 + " 发现【筹码锁死+放量起爆】标的 " + "🚀" * 10)
        print("-" * 90)
        print(res_df.to_string(index=False))
        print("-" * 90)
        print("💡 操作逻辑：")
        print("1. 这些股票筹码已从散户手中收缴，横盘多日后今日首次放量突破。")
        print("2. 止损位：今日阳线实体的底部。")
        print("3. 目标位：前期历史高点，或 MA5 走平离场。")
    else:
        print("\n今日暂未发现符合‘筹码大降+极致横盘突破’的个股。")

if __name__ == "__main__":
    find_explosive_stocks()