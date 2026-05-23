import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def batch_analyze_main_force_with_flow():
    print(f"[{datetime.datetime.now()}] 启动【双日吸金板块 + 主力成本穿透】分析...")

    with engine.connect() as conn:
        # 1. 获取最近两个交易日 (用于判断板块资金连续流入)
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_sector_fund_flow ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(date_res) < 2:
            print("资金流向数据不足。")
            return
        today, yesterday = date_res[0][0], date_res[1][0]
        print(f"板块分析周期：{yesterday} -> {today}")

        # 2. 锁定【连续两天净流入】的强势板块
        flow_sql = text("""
            SELECT t.sector_name 
            FROM stk_sector_fund_flow t
            JOIN stk_sector_fund_flow y ON t.sector_name = y.sector_name AND y.trade_date = :yest
            WHERE t.trade_date = :today 
              AND t.net_inflow_amount > 0 
              AND y.net_inflow_amount > 0
        """)
        hot_sectors = pd.read_sql(flow_sql, conn, params={"today": today, "yest": yesterday})['sector_name'].tolist()
        
        if not hot_sectors:
            print("今日未发现连续两天资金流入的强势板块。")
            return
        
        print(f"识别到连续吸金板块 {len(hot_sectors)} 个，开始筛选个股...")

        # 3. 提取【属于这些板块】且【今日有异动】的个股
        # 关联 stocks, stock_sector_relation 和 stk_capital_abnormal
        # 注意：此处自动处理 '行业-' 或 '概念-' 前缀
        main_query = text("""
            SELECT DISTINCT a.symbol, a.name, a.surge_count, a.surge_times, a.vol_ratio, r.sector_name
            FROM stk_capital_abnormal a
            JOIN stock_sector_relation r ON a.symbol = r.symbol
            WHERE a.trade_date = :today
              AND (
                  r.sector_name IN :sector_list_plain OR
                  r.sector_name IN :sector_list_hy OR
                  r.sector_name IN :sector_list_gn
              )
        """)
        
        # 构造匹配列表
        sector_list_hy = [f"行业-{s}" for s in hot_sectors]
        sector_list_gn = [f"概念-{s}" for s in hot_sectors]
        
        df_abnormal = pd.read_sql(main_query, conn, params={
            "today": today,
            "sector_list_plain": hot_sectors,
            "sector_list_hy": sector_list_hy,
            "sector_list_gn": sector_list_gn
        })

    if df_abnormal.empty:
        print("在强势吸金板块中未发现异动个股。")
        return

    results = []
    # 4. 执行主力成本测算逻辑
    print(f"正在对 {len(df_abnormal)} 只处于‘热钱中心’的异动标的进行成本穿透...")
    for _, row in df_abnormal.iterrows():
        symbol = row['symbol']
        surge_times_str = row['surge_times']
        if not surge_times_str: continue

        try:
            # 提取异动时刻分时
            times_list = [f"'{t}:00'" for t in surge_times_str.split(',')]
            query_min = f"""
                SELECT amount, volume FROM stk_min_kline 
                WHERE symbol = '{symbol}' AND DATE(trade_time) = '{today}'
                AND TIME(trade_time) IN ({','.join(times_list)})
            """
            df_surges = pd.read_sql(query_min, engine)
            if df_surges.empty: continue
            
            # 修正单位（*100）
            mf_cost = df_surges['amount'].sum() / (df_surges['volume'].sum() * 100 + 0.01)
            
            # 提取日线数据算市场均价
            query_daily = f"SELECT close, amount, volume FROM stk_daily_kline WHERE symbol='{symbol}' AND trade_date='{today}'"
            df_daily = pd.read_sql(query_daily, engine)
            if df_daily.empty: continue
            
            last_price = df_daily['close'].iloc[0]
            market_vwap = df_daily['amount'].iloc[0] / (df_daily['volume'].iloc[0] * 100 + 0.01)

            # 偏离度
            cost_bias = (last_price - mf_cost) / mf_cost * 100
            
            results.append({
                '代码': symbol,
                '名称': row['name'],
                '板块': row['sector_name'].replace('行业-','').replace('概念-',''),
                '异动次数': row['surge_count'],
                '收盘价': last_price,
                '全天均价': round(market_vwap, 2),
                '主力成本': round(mf_cost, 2),
                '主力获利%': round(cost_bias, 2)
            })
        except:
            continue

    # 5. 排序并输出
    if results:
        df_res = pd.DataFrame(results).sort_values(['主力获利%', '异动次数'], ascending=[True, False])
        
        print("\n" + "💰" * 10 + " 【双日吸金板块】主力成本分析报告 " + "💰" * 10)
        print("-" * 110)
        print(df_res.to_string(index=False))
        print("-" * 110)
        
        # 重点推荐
        potential = df_res[(df_res['主力获利%'] > -5) & (df_res['主力获利%'] < 3)]
        if not potential.empty:
            print(f"\n🚀 发现 {len(potential)} 只处于‘加速板块’且‘主力尚未脱离成本’的共振标的！")
            print(potential[['代码', '名称', '板块', '主力成本', '主力获利%']].head(5).to_string(index=False))
            print("\n💡 操盘研判：板块资金连续两天加速涌入，且个股主力今天刚扫货被套或微盈，极具爆发力。")
    else:
        print("未发现符合条件的标的。")

if __name__ == "__main__":
    batch_analyze_main_force_with_flow()