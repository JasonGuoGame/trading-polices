import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def batch_analyze_main_force():
    print(f"[{datetime.datetime.now()}] 启动全市场主力成本穿透分析 (单位修正版)...")

    # 1. 获取最新异动日期
    with engine.connect() as conn:
        latest_date = conn.execute(text("SELECT MAX(trade_date) FROM stk_capital_abnormal")).fetchone()[0]
        
        if latest_date is None:
            print("异动记录表为空，请先运行异动扫描脚本。")
            return

        print(f"分析日期：{latest_date}")
        
        # 提取异动个股的基本数据
        query_abnormal = text("""
            SELECT symbol, name, surge_count, surge_times, vol_ratio 
            FROM stk_capital_abnormal 
            WHERE trade_date = :d
        """)
        df_abnormal = pd.read_sql(query_abnormal, conn, params={"d": latest_date})

    if df_abnormal.empty:
        print("今日无异动记录。")
        return

    results = []

    # 2. 遍历个股
    for _, row in df_abnormal.iterrows():
        symbol = row['symbol']
        surge_times_str = row['surge_times']
        
        if not surge_times_str: continue

        try:
            # A. 提取异动时刻的具体分时成交
            times_list = [f"'{t}:00'" for t in surge_times_str.split(',')]
            
            query_min = f"""
                SELECT amount, volume 
                FROM stk_min_kline 
                WHERE symbol = '{symbol}' 
                  AND DATE(trade_time) = '{latest_date}'
                  AND TIME(trade_time) IN ({','.join(times_list)})
            """
            df_surges = pd.read_sql(query_min, engine)
            
            if df_surges.empty: continue
            
            # --- 修正点 1：成交量乘以 100 换算为“股” ---
            main_force_avg = df_surges['amount'].sum() / (df_surges['volume'].sum() * 100 + 0.01)
            
            # B. 提取日线数据
            query_daily = f"""
                SELECT close, amount, volume 
                FROM stk_daily_kline 
                WHERE symbol = '{symbol}' AND trade_date = '{latest_date}'
            """
            df_daily = pd.read_sql(query_daily, engine)
            if df_daily.empty: continue
            
            last_price = df_daily['close'].iloc[0]
            # --- 修正点 2：成交量乘以 100 换算为“股” ---
            market_vwap = df_daily['amount'].iloc[0] / (df_daily['volume'].iloc[0] * 100 + 0.01)

            # C. 计算偏离度
            cost_bias = (last_price - main_force_avg) / main_force_avg * 100
            
            results.append({
                '代码': symbol,
                '名称': row['name'],
                '异动次数': row['surge_count'],
                '收盘价': last_price,
                '全天均价': round(market_vwap, 2),
                '主力成本': round(main_force_avg, 2),
                '主力获利%': round(cost_bias, 2),
                '量能倍数': row['vol_ratio']
            })

        except Exception as e:
            continue

    # 3. 输出报告
    if results:
        df_res = pd.DataFrame(results)
        # 按照“主力成本保护度”进行排序（获利越小越好）
        df_res = df_res.sort_values('主力获利%', ascending=True)

        print("\n" + "💰" * 10 + " 主力入场成本穿透报告 (修正版) " + "💰" * 10)
        print("-" * 100)
        print(df_res.to_string(index=False))
        print("-" * 100)
        
        # 筛选出高潜力股
        potential = df_res[(df_res['主力获利%'] > -5) & (df_res['主力获利%'] < 4)]
        if not potential.empty:
            print(f"\n💡 重点狙击名单：共有 {len(potential)} 只个股股价紧贴/略低于主力成本线。")
            print(potential[['代码', '名称', '主力成本', '收盘价', '主力获利%']].head(5).to_string(index=False))
            print("\n💡 操盘建议：股价与主力成本极为贴近。若明日缩量回踩，为极佳买点。")
    else:
        print("未能计算出有效的成本分析数据。")

if __name__ == "__main__":
    batch_analyze_main_force()