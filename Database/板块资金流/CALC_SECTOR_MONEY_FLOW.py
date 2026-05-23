import os
# 1. 彻底屏蔽系统代理，防止断线
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
os.environ['no_proxy'] = '*'

import akshare as ak
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import time

# --- 2. 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS stk_sector_fund_flow (
        sector_name VARCHAR(100),
        trade_date DATE,
        net_inflow_amount DECIMAL(18, 2) COMMENT '主力净流入额(亿)',
        net_inflow_rate DECIMAL(10, 2) COMMENT '主力净流入占比%',
        top_stock_name VARCHAR(100) COMMENT '领涨龙头',
        PRIMARY KEY (sector_name, trade_date)
    ) ENGINE=InnoDB;
    """
    with engine.begin() as conn:
        conn.execute(text(sql))

def calculate_local_fallback(today, yesterday):
    """
    【本地备选方案】当 AKShare 失效时，利用本地数据模拟计算
    解决重复累加的关键：仅使用‘行业-’前缀，并引入涨跌权重
    """
    print("⚠️ 正在启动本地数据降级计算（模拟主力流向）...")
    
    # 提取行情并去重
    query = text("""
        SELECT k.symbol, s.name, k.close, k.amount,
               (SELECT close FROM stk_daily_kline WHERE symbol = k.symbol AND trade_date = :yest) as prev_close
        FROM stk_daily_kline k
        JOIN stocks s ON k.symbol = s.symbol
        WHERE k.trade_date = :today AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%' OR k.symbol LIKE '30%')
    """)
    with engine.connect() as conn:
        df_stocks = pd.read_sql(query, conn, params={"today": today, "yest": yesterday})
    
    # 计算涨跌幅系数 (模拟主力净流入 = 成交额 * 涨幅 * 0.4)
    # 0.4 是一个经验拟合系数，用来对齐东财量级
    df_stocks['pct_chg'] = (df_stocks['close'] - df_stocks['prev_close']) / (df_stocks['prev_close'] + 0.01)
    df_stocks['net_flow_sim'] = df_stocks['amount'] * df_stocks['pct_chg'] * 0.4

    # 提取行业映射
    rel_query = text("SELECT symbol, sector_name FROM stock_sector_relation WHERE sector_name LIKE '行业-%%'")
    with engine.connect() as conn:
        df_rel = pd.read_sql(rel_query, conn)
    
    df_merged = pd.merge(df_stocks, df_rel, on='symbol')
    
    res = []
    for name, group in df_merged.groupby('sector_name'):
        res.append({
            'sector_name': name.replace('行业-', ''),
            'net_inflow_amount': round(group['net_flow_sim'].sum() / 1e8, 2),
            'net_inflow_rate': round((group['net_flow_sim'].sum() / group['amount'].sum() * 100), 2),
            'top_stock_name': group.sort_values('pct_chg', ascending=False).iloc[0]['name'],
            'trade_date': today
        })
    return pd.DataFrame(res)

def save_to_db(df):
    """通用 UPSERT 写入函数"""
    if df.empty: return
    with engine.begin() as conn:
        df.to_sql('temp_flow', con=conn, if_exists='replace', index=False)
        conn.execute(text("""
            INSERT INTO stk_sector_fund_flow (sector_name, trade_date, net_inflow_amount, net_inflow_rate, top_stock_name)
            SELECT sector_name, trade_date, net_inflow_amount, net_inflow_rate, top_stock_name FROM temp_flow
            ON DUPLICATE KEY UPDATE 
                net_inflow_amount = VALUES(net_inflow_amount),
                net_inflow_rate = VALUES(net_inflow_rate),
                top_stock_name = VALUES(top_stock_name);
        """))
        conn.execute(text("DROP TABLE IF EXISTS temp_flow;"))

def run_sync_flow():
    init_db()
    
    # 获取日期
    with engine.connect() as conn:
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        today, yesterday = date_res[0][0], date_res[1][0]

    # --- 尝试 AKShare ---
    df_final = pd.DataFrame()
    retries = 3
    for i in range(retries):
        try:
            print(f"正在通过 AKShare 获取资金数据 (尝试 {i+1}/{retries})...")
            df_raw = ak.stock_sector_fund_flow_rank(indicator="今日")
            if not df_raw.empty:
                df_final['sector_name'] = df_raw['名称']
                df_final['net_inflow_amount'] = (pd.to_numeric(df_raw['今日主力净流入-净额'], errors='coerce') / 1e8).round(2)
                df_final['net_inflow_rate'] = pd.to_numeric(df_raw['今日主力净流入-净占比'], errors='coerce').round(2)
                df_final['top_stock_name'] = df_raw['今日主力净流入最大股']
                df_final['trade_date'] = today
                print("✅ AKShare 获取成功。")
                break
        except Exception as e:
            print(f"⚠️ AKShare 失败: {e}")
            time.sleep(5)

    # --- 如果 AKShare 彻底失败，执行本地计算 ---
    if df_final.empty:
        df_final = calculate_local_fallback(today, yesterday)

    # 保存并打印
    if not df_final.empty:
        save_to_db(df_final)
        top10 = df_final.sort_values('net_inflow_amount', ascending=False).head(10)
        print("\n" + "💰" * 5 + " 今日资金净流入 TOP 10 " + "💰" * 5)
        print(top10[['sector_name', 'net_inflow_amount', 'net_inflow_rate']].to_string(index=False))
    else:
        print("❌ 无法获取或计算资金流向数据。")

if __name__ == "__main__":
    run_sync_flow()