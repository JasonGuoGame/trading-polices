import os
import sys
import time
import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

# 彻底屏蔽代理
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

warnings.filterwarnings('ignore')

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def add_suffix(code):
    code = str(code).zfill(6)
    return code + ".SH" if code.startswith('6') else code + ".SZ"

def sync_stock_fund_flow():
    print(f"[{datetime.datetime.now()}] 启动东方财富个股资金流向同步...")

    try:
        # 1. 获取全市场今日资金流向排名
        # indicator="今日"; 可选 "3日", "5日", "10日"
        df_raw = ak.stock_individual_fund_flow_rank(indicator="今日")
        
        if df_raw.empty:
            print("未能获取到数据。")
            return

        # 2. 映射字段 (根据你的表结构对齐)
        # 东财列名：代码, 名称, 最新价, 涨跌幅, 今日主力净流入-净额, 今日主力净流入-净占比, 
        # 今日超大单净流入-净额, 今日超大单净流入-净占比, 今日大单净流入-净额, 今日大单净流入-净占比...
        
        df_final = pd.DataFrame()
        df_final['symbol'] = df_raw['代码'].apply(add_suffix)
        df_final['stock_name'] = df_raw['名称']
        df_final['trade_date'] = datetime.date.today()
        
        # 转换金额为“万元” (东财原始数据通常是元)
        df_final['main_net_inflow'] = pd.to_numeric(df_raw['今日主力净流入-净额'], errors='coerce') / 10000
        df_final['super_large_net_inflow'] = pd.to_numeric(df_raw['今日超大单净流入-净额'], errors='coerce') / 10000
        df_final['large_net_inflow'] = pd.to_numeric(df_raw['今日大单净流入-净额'], errors='coerce') / 10000
        df_final['medium_net_inflow'] = pd.to_numeric(df_raw['今日中单净流入-净额'], errors='coerce') / 10000
        df_final['small_net_inflow'] = pd.to_numeric(df_raw['今日小单净流入-净额'], errors='coerce') / 10000
        
        # 占比数据
        df_final['main_net_ratio'] = pd.to_numeric(df_raw['今日主力净流入-净占比'], errors='coerce')
        df_final['super_large_ratio'] = pd.to_numeric(df_raw['今日超大单净流入-净占比'], errors='coerce')
        df_final['large_ratio'] = pd.to_numeric(df_raw['今日大单净流入-净占比'], errors='coerce')

        # 如果接口里有 3d, 5d 数据也可以直接取
        # 字段名可能为：'3日主力净流入-净额' 等
        if '3日主力净流入-净额' in df_raw.columns:
            df_final['inflow_3d'] = pd.to_numeric(df_raw['3日主力净流入-净额'], errors='coerce') / 10000
        if '5日主力净流入-净额' in df_raw.columns:
            df_final['inflow_5d'] = pd.to_numeric(df_raw['5日主力净流入-净额'], errors='coerce') / 10000

        # 计算资金评分 (简单逻辑：流入额和占比的加权排名)
        df_final['capital_score'] = (
            df_final['main_net_inflow'].rank(pct=True) * 50 + 
            df_final['main_net_ratio'].rank(pct=True) * 50
        ).fillna(0).astype(int)
        
        df_final['rank_market'] = df_final['main_net_inflow'].rank(ascending=False, method='min').fillna(0).astype(int)

        # 3. 写入数据库 (UPSERT)
        print(f"整理完成，同步 {len(df_final)} 条记录...")
        with engine.begin() as conn:
            df_final.to_sql('temp_stock_flow', con=conn, if_exists='replace', index=False)
            
            upsert_sql = text("""
                INSERT INTO stk_stock_fund_flow (
                    trade_date, symbol, stock_name, main_net_inflow, 
                    super_large_net_inflow, large_net_inflow, medium_net_inflow, 
                    small_net_inflow, main_net_ratio, super_large_ratio, large_ratio,
                    inflow_3d, inflow_5d, rank_market, capital_score
                )
                SELECT 
                    trade_date, symbol, stock_name, main_net_inflow, 
                    super_large_net_inflow, large_net_inflow, medium_net_inflow, 
                    small_net_inflow, main_net_ratio, super_large_ratio, large_ratio,
                    inflow_3d, inflow_5d, rank_market, capital_score 
                FROM temp_stock_flow
                ON DUPLICATE KEY UPDATE 
                    main_net_inflow = VALUES(main_net_inflow),
                    main_net_ratio = VALUES(main_net_ratio),
                    super_large_net_inflow = VALUES(super_large_net_inflow),
                    capital_score = VALUES(capital_score),
                    rank_market = VALUES(rank_market);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_stock_flow;"))
            
        print("✅ 成功同步今日个股资金流数据。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    sync_stock_fund_flow()