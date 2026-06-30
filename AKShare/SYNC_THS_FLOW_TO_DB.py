import sys
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
# 这里使用简化的路径添加方式
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的全局配置文件

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def sync_sector_flow_from_stock_table():
    print(f"[{datetime.datetime.now()}] 🚀 启动基于‘个股真实资金流’的板块聚合系统...")

    try:
        # --- A. 获取同花顺官方标准名单（行业+概念） ---
        df_ths_ind = ak.stock_board_industry_name_ths() 
        df_ths_con = ak.stock_board_concept_name_ths()  
        # 1. 合并所有原始名称
        raw_official_names = set(df_ths_ind['name'].tolist()) | set(df_ths_con['name'].tolist())
        
        # 2. 🌟 核心过滤：应用 config.SECTOR_BLACKLIST 过滤无意义板块
        # 逻辑：只有当板块名不包含黑名单中任何一个关键词时，才保留
        official_names = {
            name for name in raw_official_names 
            if not any(noise in name for noise in config.SECTOR_BLACKLIST)
        }

        # 3. 构建过滤后的代码映射表
        # 先合并所有代码
        all_name_to_code = dict(zip(df_ths_ind['name'], df_ths_ind['code']))
        all_name_to_code.update(dict(zip(df_ths_con['name'], df_ths_con['code'])))
        
        # 只保留在 official_names 中的键值对
        name_to_code = {k: v for k, v in all_name_to_code.items() if k in official_names}

        print(f"✅ 名单脱水完成：原始 {len(raw_official_names)} 个，过滤后剩余 {len(official_names)} 个核心板块。")

        # --- B. 获取日期 ---
        with engine.connect() as conn:
            date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_stock_fund_flow")).fetchone()
            today = date_res[0]
        print(f"📅 分析日期: {today}")

        # --- C. SQL：关联‘个股资金流’、‘板块映射’、‘K线’ ---
        # 我们需要 K 线的 amount 来计算流入占比
        query = text("""
            SELECT 
                f.symbol, 
                f.stock_name,
                f.main_net_inflow,      -- 真实主力净流入(万元)
                f.active_buy_amount,    -- 真实主动买入(万元)
                f.capital_score,        -- 个股资金分
                f.attack_score,         -- 个股攻击分
                r.sector_name as db_sector_name,
                k.amount as stock_turnover_amount,
                (k.close/k.open - 1)*100 as p_chg  -- 算涨跌幅用于选出板块龙头
            FROM stk_stock_fund_flow f
            JOIN stock_sector_relation r ON f.symbol = r.symbol
            JOIN stk_daily_kline k ON f.symbol = k.symbol AND f.trade_date = k.trade_date
            WHERE f.trade_date = :today
        """)
        
        with engine.connect() as conn:
            df_raw = pd.read_sql(query, conn, params={"today": today})

        if df_raw.empty:
            print("❌ 未提取到个股资金流数据，请确认 stk_stock_fund_flow 已同步。")
            return

        # --- D. 名称对齐函数 ---
        def align_to_official(raw_name):
            name = raw_name.replace('行业-', '').replace('概念-', '').replace('Ⅱ', '').replace('Ⅲ', '').strip()
            if name in official_names: return name
            for off_n in official_names:
                if off_n in name: return off_n
            return None

        df_raw['official_name'] = df_raw['db_sector_name'].apply(align_to_official)
        df_raw = df_raw.dropna(subset=['official_name'])

        # --- E. 按板块聚合（含去重逻辑） ---
        results_list = []
        for official_name, group in df_raw.groupby('official_name'):
            # 🌟 关键：去重！解决一个股票属于 CSSW证券/SW2证券 等多个标签的问题
            unique_stocks = group.drop_duplicates(subset=['symbol'])
            
            if len(unique_stocks) < 4: continue 

            # 计算各项指标
            sum_main_inflow = unique_stocks['main_net_inflow'].sum() # 万元
            sum_turnover = unique_stocks['stock_turnover_amount'].sum() # 元
            avg_cap_score = unique_stocks['capital_score'].mean()
            avg_atk_score = unique_stocks['attack_score'].mean()
            
            # 计算流入占比
            # 注意：sum_main_inflow 是万元，sum_turnover 是元
            inflow_rate = (sum_main_inflow * 10000 / sum_turnover * 100) if sum_turnover > 0 else 0
            
            # 寻找板块领涨龙头
            top_stock = unique_stocks.sort_values(by='p_chg', ascending=False).iloc[0]['stock_name']

            results_list.append({
                'sector_name': official_name,
                'sector_code': name_to_code.get(official_name, ''),
                'trade_date': today,
                'net_inflow_amount': round(sum_main_inflow / 10000, 2), # 转为 亿元
                'net_inflow_rate': round(inflow_rate, 2),
                'avg_capital_score': round(avg_cap_score, 2),
                'avg_attack_score': round(avg_atk_score, 2),
                'top_stock_name': top_stock
            })

        df_final = pd.DataFrame(results_list)

        # --- F. 写入数据库 ---
        # 建议在 stk_sector_fund_flow 表中增加 avg_capital_score 等字段
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM stk_sector_fund_flow WHERE trade_date = :d"), {"d": today})
            df_final.to_sql('stk_sector_fund_flow', con=conn, if_exists='append', index=False)

        # --- G. 打印深度复盘报告 ---
        print("\n" + "💰" * 10 + f" {today} 板块资金流深度报告 " + "💰" * 10)
        report = df_final.sort_values('net_inflow_amount', ascending=False).head(15)
        # 只打印核心列
        print(report[['sector_name', 'net_inflow_amount', 'net_inflow_rate', 'avg_attack_score', 'top_stock_name']].to_string(index=False))

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    sync_sector_flow_from_stock_table()