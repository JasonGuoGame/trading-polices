import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

import akshare as ak
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_ths_themes_with_money_flow():
    print(f"[{datetime.datetime.now()}] 正在计算同花顺题材【主力净流入】与【龙头/中军】...")
    
    try:
        # 1. 获取同花顺官方名单
        df_ths_list = ak.stock_board_concept_name_ths()
        name_col = next((c for c in df_ths_list.columns if c.lower() == 'name' or '名称' in c), None)
        ths_official_names = set(df_ths_list[name_col].tolist())

        # 2. 噪音黑名单
        SECTOR_BLACKLIST = [
            "融资融券", "沪股通", "深股通", "MSCI", "标准普尔", "富时罗素", "央国企改革", "中证", "上证", 
            "昨日", "小盘", "大盘", "权重", "两融", "证金", "汇金", "基金重仓", "预盈预增", "标普", 
            "深证", "创业板", "科创板", "活跃", "高振幅", "昨日涨停", "转债", "破净", "机构重仓", 
            "股权转让", "中盘股", "深成500", "最近多板", "东方财富", "年报预增", "电子", "HS300",
            "创业成份", "专精特新", "2025", "华为概念", "2026"
        ]
        noise_filter = " AND ".join([f"r.sector_name NOT LIKE '%%{k}%%'" for k in SECTOR_BLACKLIST])

        # 3. 日期获取
        with engine.connect() as conn:
            date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
            today = date_res[0]
            yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
            yesterday = yest_res[0]

        # 4. 核心 SQL：提取个股量价数据
        query = text(f"""
            SELECT 
                r.sector_name as '板块',
                s.name as '股票名称',
                k.symbol as '股票代码',
                k.close as 'close_t',
                k_y.close as 'close_y',
                k.amount as 'amt_raw'
            FROM stk_daily_kline k
            JOIN stocks s ON k.symbol = s.symbol
            JOIN stock_sector_relation r ON k.symbol = r.symbol
            JOIN stk_daily_kline k_y ON k.symbol = k_y.symbol AND k_y.trade_date = :yest
            WHERE k.trade_date = :today
              AND r.sector_name LIKE '概念-%%'
              AND ({noise_filter})
        """)
        
        with engine.connect() as conn:
            df_raw = pd.read_sql(query, conn, params={"today": today, "yest": yesterday})

        if df_raw.empty:
            print("❌ 数据为空，请检查数据库。")
            return

        # --- 5. 核心计算：个股模拟主力净流入 ---
        # 计算涨跌幅
        df_raw['pct_chg'] = (df_raw['close_t'] - df_raw['close_y']) / df_raw['close_y']
        # 模拟主力净流入 (金额 * 涨幅 * 0.5 系数)
        df_raw['net_flow_sim'] = df_raw['amt_raw'] * df_raw['pct_chg'] * 0.5
        # 转换为亿
        df_raw['amt_亿'] = df_raw['amt_raw'] / 1e8
        df_raw['net_flow_亿'] = df_raw['net_flow_sim'] / 1e8

        # 6. 按板块聚合分析
        theme_results = []
        for sector_name, group in df_raw.groupby('板块'):
            clean_name = sector_name.replace('概念-', '')
            if clean_name not in ths_official_names or len(group) < 6:
                continue

            # A. 资金流向计算 (你要求的两个指标)
            net_inflow_amount = group['net_flow_亿'].sum()
            total_sector_amount = group['amt_亿'].sum()
            net_inflow_rate = (net_inflow_amount / total_sector_amount * 100) if total_sector_amount > 0 else 0

            # B. 基础指标
            avg_ret = group['pct_chg'].mean() * 100
            profit_ratio = (group['pct_chg'] > 0).mean() * 100

            # C. 龙头与中军
            dragon = group.sort_values('pct_chg', ascending=False).iloc[0]
            core = group.sort_values('amt_亿', ascending=False).iloc[0]

            # D. 板块强度分 (权重：流入额+流入率+平均涨幅)
            score = (net_inflow_amount * 2) + (net_inflow_rate * 5) + (avg_ret * 10)

            theme_results.append({
                '题材名称': clean_name,
                '强度分': score,
                '主力净流入(亿)': round(net_inflow_amount, 2),
                '净流入率%': round(net_inflow_rate, 2),
                '平均涨幅%': round(avg_ret, 2),
                '赚钱效应%': round(profit_ratio, 1),
                '核心龙头': dragon['股票名称'],
                '核心中军': core['股票名称'],
                '总成交(亿)': round(total_sector_amount, 2)
            })

        # 7. 排序展示
        result_df = pd.DataFrame(theme_results).sort_values('强度分', ascending=False).head(15)

        print("\n" + "💰" * 12 + f" 同花顺题材资金流向与热力雷达 ({today}) " + "💰" * 12)
        print("-" * 125)
        # 整理显示列，对齐你的数据库字段需求
        display_cols = ['题材名称', '主力净流入(亿)', '净流入率%', '平均涨幅%', '核心龙头', '核心中军', '总成交(亿)']
        print(result_df[display_cols].to_string(index=False))
        print("-" * 125)
        
        best = result_df.iloc[0]
        print(f"🚩 结论：今日最吸金主线是【{best['题材名称']}】。")
        print(f"📊 该板块主力净买入约 {best['主力净流入(亿)']} 亿，资金密集度(占比)为 {best['净流入率%']}%。")
        print(f"💡 建议：观察中军【{best['核心中军']}】是否稳住均线，寻找龙头【{best['核心龙头']}】的二板接力机会。")
        print("💰" * 42 + "\n")

    except Exception as e:
        print(f"❌ 运行报错: {e}")

if __name__ == "__main__":
    get_ths_themes_with_money_flow()