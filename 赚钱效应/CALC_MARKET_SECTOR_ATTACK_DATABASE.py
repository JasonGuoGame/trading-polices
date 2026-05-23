import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def calculate_market_sector_metrics():
    print(f"[{datetime.datetime.now()}] 正在扫描主力资金攻击方向...")

    with engine.connect() as conn:
        # A. 获取最近 5 个交易日日期
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_sector_fund_flow ORDER BY trade_date DESC LIMIT 5")).fetchall()
        if len(date_res) < 2:
            print("数据不足。")
            return
        dates = [r[0] for r in date_res]
        today = dates[0]

        # B. 步骤 1：提取全量板块资金流向历史 (计算趋势分)
        flow_sql = text("SELECT * FROM stk_sector_fund_flow WHERE trade_date >= :sd")
        df_flow_all = pd.read_sql(flow_sql, conn, params={"sd": dates[-1]})

        # C. 步骤 2：提取今日个股行情 (寻找最强龙头)
        # 只看主板，过滤 ST
        stock_sql = text("""
            SELECT k.symbol, s.name, k.amount, (k.close - k.open)/k.open as pct_chg, r.sector_name
            FROM stk_daily_kline k
            JOIN stocks s ON k.symbol = s.symbol
            JOIN stock_sector_relation r ON k.symbol = r.symbol
            WHERE k.trade_date = :today AND s.name NOT LIKE '%ST%'
              AND (r.sector_name LIKE '行业-%' OR r.sector_name LIKE '概念-%')
        """)
        df_stocks = pd.read_sql(stock_sql, conn, params={"today": today})

    # D. 核心计算逻辑
    df_today_flow = df_flow_all[df_flow_all['trade_date'] == today].copy()
    
    # 按流入金额给板块排名
    df_today_flow['rank'] = df_today_flow['net_inflow_amount'].rank(ascending=False, method='min')
    
    results = []

    for _, row in df_today_flow.iterrows():
        sector_name = row['sector_name']
        
        # 1. 计算持续增强评分 (inflow_trend_score) - 100分制
        # 逻辑：
        # - 今日流入: +30分
        # - 今日流入 > 昨日流入: +30分
        # - 连续3日流入: +40分
        trend_score = 0
        sector_history = df_flow_all[df_flow_all['sector_name'] == sector_name].sort_values('trade_date', ascending=False)
        
        if len(sector_history) >= 1 and sector_history.iloc[0]['net_inflow_amount'] > 0:
            trend_score += 30
            if len(sector_history) >= 2 and sector_history.iloc[0]['net_inflow_amount'] > sector_history.iloc[1]['net_inflow_amount']:
                trend_score += 30
            if len(sector_history) >= 3 and (sector_history['net_inflow_amount'].head(3) > 0).all():
                trend_score += 40
        
        # 2. 寻找板块内的“最强标的” (strongest_stock)
        # 逻辑：在该板块下，涨幅第一且成交额排名前 3 的个股
        # 注意：需要把 sector_name 还原成带前缀的格式去匹配关系表
        full_name_hy = f"行业-{sector_name}"
        full_name_gn = f"概念-{sector_name}"
        
        sector_stocks = df_stocks[df_stocks['sector_name'].isin([full_name_hy, full_name_gn])]
        
        strongest_stock = "未发现"
        if not sector_stocks.empty:
            # 综合涨幅和金额寻找领头羊
            leader = sector_stocks.sort_values(by=['pct_chg', 'amount'], ascending=False).iloc[0]
            strongest_stock = f"{leader['name']}({leader['symbol']})"

        results.append({
            'trade_date': today,
            'sector_name': sector_name,
            'net_inflow_amount': float(row['net_inflow_amount']),
            'net_inflow_rate': float(row['net_inflow_rate']),
            'inflow_trend_score': int(trend_score),
            'sector_rank': int(row['rank']),
            'strongest_stock': strongest_stock
        })

    # E. 写入数据库
    if results:
        df_final = pd.DataFrame(results)
        try:
            with engine.begin() as conn:
                df_final.to_sql('temp_market_sector', con=conn, if_exists='replace', index=False)
                upsert_sql = text("""
                    INSERT INTO market_sector_metrics (trade_date, sector_name, net_inflow_amount, net_inflow_rate, inflow_trend_score, sector_rank, strongest_stock)
                    SELECT * FROM temp_market_sector
                    ON DUPLICATE KEY UPDATE 
                        net_inflow_amount = VALUES(net_inflow_amount),
                        net_inflow_rate = VALUES(net_inflow_rate),
                        inflow_trend_score = VALUES(inflow_trend_score),
                        sector_rank = VALUES(sector_rank),
                        strongest_stock = VALUES(strongest_stock);
                """)
                conn.execute(upsert_sql)
                conn.execute(text("DROP TABLE IF EXISTS temp_market_sector;"))
            
            # --- 结果展示 ---
            print("\n" + "🎯" * 10 + f" 今日资金攻击主线分析 ({today}) " + "🎯" * 10)
            print("-" * 100)
            top_display = df_final.sort_values('sector_rank').head(10)
            print(top_display[['sector_rank', 'sector_name', 'net_inflow_amount', 'inflow_trend_score', 'strongest_stock']].to_string(index=False))
            print("-" * 100)
            
            # 核心结论
            trending = df_final[df_final['inflow_trend_score'] >= 60].sort_values('net_inflow_amount', ascending=False)
            if not trending.empty:
                print(f"🔥 核心研判：资金正在【{trending.iloc[0]['sector_name']}】方向猛烈开火，且具备持续性趋势！")
            else:
                print("⚖️ 核心研判：今日资金攻击较为分散，暂无持续性增强板块。")
            print("🎯" * 32 + "\n")

        except Exception as e:
            print(f"❌ 数据库写入失败: {e}")

if __name__ == "__main__":
    calculate_market_sector_metrics()