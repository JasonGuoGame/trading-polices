import sys
import os
# 彻底屏蔽代理干扰
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
os.environ['no_proxy'] = '*'

import akshare as ak
import pandas as pd
import numpy as np
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

def sync_ths_flow_all_sectors():
    print(f"[{datetime.datetime.now()}] 启动同花顺题材全量资金流向（流入+流出）监控...")

    try:
        # 1. 获取同花顺官方概念名单
        df_ths_list = ak.stock_board_concept_name_ths()
        name_col = next((c for c in df_ths_list.columns if c.lower() == 'name' or '名称' in c), None)
        ths_official_names = set(df_ths_list[name_col].tolist())

        noise_filter = " AND ".join([f"r.sector_name NOT LIKE '%%{k}%%'" for k in config.SECTOR_BLACKLIST])

        # 3. 日期获取
        with engine.connect() as conn:
            date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
            today = date_res[0]
            yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
            yesterday = yest_res[0]
        
        print(f"📅 分析日期: {today} | 对比日期: {yesterday}")

        # 4. 执行 SQL：提取个股行情
        query = text(f"""
            SELECT 
                r.sector_name as 'raw_sector',
                s.name as 'stock_name',
                k.symbol, k.close as 'close_t', k_y.close as 'close_y', k.amount as 'amount_raw'
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
            print("❌ 未提取到行情数据。")
            return

        # 5. 核心计算：主力净流入模拟 (保留正负号)
        df_raw['pct_chg'] = (df_raw['close_t'] - df_raw['close_y']) / df_raw['close_y']
        # 净流入 = 成交额 * 涨跌幅 * 0.5 (正数为流入，负数为流出)
        df_raw['net_flow_sim'] = df_raw['amount_raw'] * df_raw['pct_chg'] * 0.5

        # 6. 按板块聚合
        results_list = []
        for sector_name, group in df_raw.groupby('raw_sector'):
            clean_name = sector_name.replace('概念-', '')
            if clean_name not in ths_official_names or len(group) < 6:
                continue

            sum_inflow = group['net_flow_sim'].sum()
            sum_total_amount = group['amount_raw'].sum()
            
            # 计算指标
            net_inflow_amount = round(sum_inflow / 1e8, 2) # 亿
            net_inflow_rate = round((sum_inflow / sum_total_amount * 100), 2) if sum_total_amount > 0 else 0
            
            # 领涨/领跌龙头 (涨幅绝对值最大的)
            top_stock = group.sort_values(by='pct_chg', ascending=False).iloc[0]['stock_name']

            results_list.append({
                'sector_name': clean_name,
                'trade_date': today,
                'net_inflow_amount': net_inflow_amount,
                'net_inflow_rate': net_inflow_rate,
                'top_stock_name': top_stock
            })

        if not results_list:
            print("未能生成任何有效数据。")
            return

        df_final = pd.DataFrame(results_list)

        # 7. 写入数据库 (替换模式)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM stk_sector_fund_flow WHERE trade_date = :d"), {"d": today})
            df_final.to_sql('stk_sector_fund_flow', con=conn, if_exists='append', index=False)

            # B. 核心修改：库容维护逻辑，只保留最近 5 个交易日
            # 首先找出当前数据库中最新的 5 个日期
            date_list_sql = text("SELECT DISTINCT trade_date FROM stk_sector_fund_flow ORDER BY trade_date DESC LIMIT 5")
            top_5_dates = [r[0] for r in conn.execute(date_list_sql).fetchall()]
            
            if len(top_5_dates) >= 5:
                oldest_date = top_5_dates[-1] # 第 5 名的日期
                # 删除所有比第 5 名还旧的日期
                del_res = conn.execute(text("DELETE FROM stk_sector_fund_flow WHERE trade_date < :d"), {"d": oldest_date})
                if del_res.rowcount > 0:
                    print(f"🧹 库容维护：已清理早于 {oldest_date} 的老旧数据，移除 {del_res.rowcount} 条记录。")
                    
        # 8. 分类打印报告 (流入榜 vs 流出榜)
        inflow_top = df_final[df_final['net_inflow_amount'] > 0].sort_values('net_inflow_amount', ascending=False).head(10)
        outflow_top = df_final[df_final['net_inflow_amount'] < 0].sort_values('net_inflow_amount', ascending=True).head(10)

        print("\n" + "💰" * 8 + f" {today} 题材【净流入】前 10 名 " + "💰" * 8)
        print("-" * 85)
        print(inflow_top.to_string(index=False))

        print("\n" + "💸" * 8 + f" {today} 题材【净流出】前 10 名 " + "💸" * 8)
        print("-" * 85)
        print(outflow_top.to_string(index=False))

        print(f"\n✅ 同步完成！数据库已更新 {len(df_final)} 个题材的完整资金动向。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    sync_ths_flow_all_sectors()