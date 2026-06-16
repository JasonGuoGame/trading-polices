import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import sys
import warnings

warnings.filterwarnings('ignore')

# --- 1. 配置加载 ---
sys.path.append(r"C:\ws\trading-polices\config")
import config

engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def run_v7_strategy():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 启动 V7 龙头资金共振系统...")

    with engine_quant.connect() as conn:
        # A. 获取日期基准
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_factors ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(date_res) < 2: return
        today = date_res[0][0]
        yesterday = date_res[1][0]

        # B. 【条件1：大盘过滤】 沪深300 > MA20
        market_check = pd.read_sql(text("""
            SELECT k.close, f.f_bb_m 
            FROM stk_daily_kline k 
            JOIN stk_factors f ON k.symbol=f.symbol AND k.trade_date=f.trade_date 
            WHERE k.symbol='000300.SH' AND k.trade_date=:t
        """), conn, params={"t": today})
        
        if market_check.empty or market_check.iloc[0]['close'] < market_check.iloc[0]['f_bb_m']:
            print(f"❄️ 大盘环境未达标 (沪深300 < MA20)，V7 策略自动防御，不执行选股。")
            return 

        # C. 【条件2：板块资金】 连续3日净流入 > 5亿
        # 逻辑：找出最近 5 天内，连续 3 天以上流入且总额 > 5亿的板块
        sector_sql = text("""
            SELECT sector_name, SUM(net_inflow_amount) as total_inflow
            FROM stk_sector_fund_flow
            WHERE trade_date >= DATE_SUB(:t, INTERVAL 7 DAY) 
              AND net_inflow_amount > 0
            GROUP BY sector_name
            HAVING COUNT(trade_date) >= 3 AND total_inflow >= 5.0
        """)
        hot_sectors = pd.read_sql(sector_sql, conn, params={"t": today})['sector_name'].tolist()
        
        if not hot_sectors:
            print("💨 题材动能不足：未发现连续 3 日吸金超 5 亿的板块。")
            return
        
        # 处理板块名称匹配
        search_sectors = hot_sectors + [f"行业-{s}" for s in hot_sectors] + [f"概念-{s}" for s in hot_sectors]

        # D. 【核心 SQL：五维共振】
        # 解决 1055 报错：使用 MAX() 包装非 Group By 字段
        noise_filter = " AND ".join([f"r.sector_name NOT LIKE '%%{k}%%'" for k in config.SECTOR_BLACKLIST])
        
        main_sql = text(f"""
            WITH LimitUpGene AS (
                -- 找出近20日内出现过涨停的个股 (收盘价>=涨停价)
                SELECT symbol FROM (
                    SELECT symbol, close, 
                           LAG(close) OVER(PARTITION BY symbol ORDER BY trade_date) as pre_c
                    FROM stk_daily_kline 
                    WHERE trade_date >= DATE_SUB(:t, INTERVAL 30 DAY)
                ) t_gen
                WHERE close >= ROUND(pre_c * 1.098, 2)
                GROUP BY symbol
            ),
            TrendAnalysis AS (
                -- 计算 MA30 及其斜率
                SELECT symbol, trade_date, close,
                       AVG(close) OVER(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                       AVG(close) OVER(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 34 PRECEDING AND 5 PRECEDING) as ma30_prev
                FROM stk_daily_kline
                WHERE trade_date >= DATE_SUB(:t, INTERVAL 60 DAY)
            )
            SELECT 
                f.symbol, 
                MAX(s.name) as name, 
                MAX(r.sector_name) as sector_name, 
                MAX(f.f_macd_dif) as DIF, 
                MAX(f.f_quantity_ratio) as qr,
                MAX(c.chip_width70) as width70, 
                MAX(c.profit_ratio) as profit, 
                MAX(k.close) as close
            FROM stk_factors f
            JOIN stocks s ON f.symbol = s.symbol
            JOIN stk_daily_kline k ON f.symbol = k.symbol AND f.trade_date = k.trade_date
            JOIN stk_chip_factor c ON f.symbol = c.symbol AND f.trade_date = c.trade_date
            JOIN stock_sector_relation r ON f.symbol = r.symbol
            JOIN LimitUpGene g ON f.symbol = g.symbol
            JOIN TrendAnalysis tr ON f.symbol = tr.symbol AND f.trade_date = tr.trade_date
            WHERE f.trade_date = :t
              AND r.sector_name IN :s_list
              AND tr.close > tr.ma30                 -- 价格 > MA30
              AND tr.ma30 > tr.ma30_prev             -- MA30 趋势向上
              AND f.f_macd_dif > 0                   -- DIF > 0
              AND f.f_quantity_ratio > 1.5           -- 量比 > 1.5
              AND c.chip_width70 < 0.12              -- 筹码高度集中
              AND c.profit_ratio BETWEEN 40 AND 90   -- 获利盘适中
              AND s.name NOT LIKE '%%ST%%'
              AND ({noise_filter})
            GROUP BY f.symbol
        """)

        final_df = pd.read_sql(main_sql, conn, params={"t": today, "s_list": search_sectors})

    # E. 结果入库
    if not final_df.empty:
        # 计算 V7 综合分 (筹码越紧分越高)
        final_df['score'] = (100 - (final_df['width70'] * 500)).clip(0, 100).astype(int)
        
        save_records = []
        for _, row in final_df.iterrows():
            save_records.append({
                'symbol': row['symbol'], 'trade_date': today, 'stock_name': row['name'],
                'pool_type': 'long', # V7属于中线波段
                'sector_name': row['sector_name'].replace('行业-','').replace('概念-',''),
                'score': row['score'], 'status': 'V7龙头共振',
                'tags': json.dumps({"qr": float(row['qr']), "width": float(row['width70']), "profit": float(row['profit'])}),
                'notes': f"V7全维度达成：MA30向上，筹码锁死({row['width70']})，吸金主线。",
                'is_watch_focus': 1, 'watch_level': 3,
                'created_at': datetime.datetime.now(), 'updated_at': datetime.datetime.now()
            })
        
        with engine_review.begin() as conn:
            conn.execute(text("DELETE FROM stock_pools WHERE trade_date=:d AND status='V7龙头共振'"), {"d": today})
            pd.DataFrame(save_records).to_sql('stock_pools', con=conn, if_exists='append', index=False)
        
        print("\n" + "👑" * 10 + " V7 龙头资金共振名单 " + "👑" * 10)
        print("-" * 120)
        print(final_df[['symbol', 'name', 'score', 'width70', 'qr']].sort_values('score', ascending=False).to_string(index=False))
        print("-" * 120)
        print(f"✅ 同步成功！共选出 {len(final_df)} 只顶级共振标的。")
    else:
        print("💡 今日未发现完全符合 V7 严苛标准的个股。")

if __name__ == "__main__":
    run_v7_strategy()