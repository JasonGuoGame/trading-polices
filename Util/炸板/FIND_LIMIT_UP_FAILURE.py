import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def screen_limit_up_failure():
    print(f"[{datetime.datetime.now()}] 正在扫描今日“触及涨停但未封板”的股票...")

    # 1. 获取最新交易日
    latest_date_query = "SELECT MAX(DATE(trade_time)) FROM stk_min_kline"
    latest_date = pd.read_sql(latest_date_query, engine).iloc[0,0]
    
    # 2. 获取前一个交易日的收盘价（用于计算今日涨停价）
    # 涨停价计算规则：昨日收盘价 * 1.1，四舍五入保留两位小数
    prev_close_query = f"""
    SELECT symbol, close as prev_close 
    FROM stk_daily_kline 
    WHERE trade_date = (
        SELECT MAX(trade_date) FROM stk_daily_kline WHERE trade_date < '{latest_date}'
    )
    AND (symbol LIKE '60%%' OR symbol LIKE '00%%') -- 只要主板
    """
    df_prev = pd.read_sql(prev_close_query, engine)
    
    # 计算涨停价字典
    # 注意：ST股是5%，这里我们按主板10%计算
    df_prev['limit_price'] = (df_prev['prev_close'] * 1.10).apply(lambda x: round(x + 0.0001, 2))
    limit_map = dict(zip(df_prev['symbol'], df_prev['limit_price']))

    # 3. 提取今日所有分时数据
    min_query = f"""
    SELECT symbol, high, close, trade_time 
    FROM stk_min_kline 
    WHERE DATE(trade_time) = '{latest_date}'
    """
    df_min = pd.read_sql(min_query, engine)
    
    results = []

    # 4. 按股票分组分析
    for symbol, df in df_min.groupby('symbol'):
        if symbol not in limit_map: continue
        
        limit_p = limit_map[symbol]
        
        # 形态判定逻辑：
        # A. 今日最高价 触及或超过了 涨停价
        reached_limit = (df['high'] >= limit_p).any()
        
        # B. 最终收盘价 低于 涨停价 (说明没封住)
        final_close = df['close'].iloc[-1]
        is_not_closed = final_close < limit_p
        
        if reached_limit and is_not_closed:
            # 计算回落幅度
            drop_pct = (limit_p - final_close) / limit_p * 100
            
            # 获取名字
            name_res = pd.read_sql(f"SELECT name FROM stocks WHERE symbol='{symbol}'", engine)
            name = name_res.iloc[0,0] if not name_res.empty else "未知"

            # 过滤ST（通过名称）
            if 'ST' in name or '退' in name: continue

            results.append({
                '代码': symbol,
                '名称': name,
                '涨停价': limit_p,
                '最终收盘': final_close,
                '回落幅度': f"{round(drop_pct, 2)}%",
                '收盘涨幅': f"{round((final_close - limit_map[symbol]/1.1)/(limit_map[symbol]/1.1)*100, 2)}%"
            })

    # 5. 输出结果
    if results:
        res_df = pd.DataFrame(results).sort_values('最终收盘', ascending=False)
        print("\n" + "⚠️" * 10 + f" {latest_date} 炸板（触及涨停未封死）名单 " + "⚠️" * 10)
        print("-" * 80)
        print(res_df.to_string(index=False))
        print("-" * 80)
        print("💡 研判建议：")
        print("1. 强力回落（回落>5%）：通常是“大阴棒”预警，主力派发迹象明显，避开。")
        print("2. 强势烂板（回落<1%）：可能是主力洗盘换手，关注次日是否出现‘弱转强’高开。")
    else:
        print(f"\n{latest_date} 全市场未发现炸板个股。")

if __name__ == "__main__":
    screen_limit_up_failure()