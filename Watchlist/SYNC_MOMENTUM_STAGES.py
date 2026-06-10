import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys
import os

# 屏蔽无关警告
warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
# 这里使用简化的路径添加方式
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的全局配置文件

# --- 1. 数据库配置 ---
# 行情库
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
# 作战池库
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def clean_and_pick_two_sectors(sector_str):
    """过滤黑名单并挑选前两个核心板块"""
    if not sector_str: return "其他"
    raw_list = sector_str.split(',')
    filtered = []
    for s in raw_list:
        if not any(noise in s for noise in config.SECTOR_BLACKLIST):
            clean_s = s.replace('行业-', '').replace('概念-', '')
            filtered.append(clean_s)
    if len(filtered) >= 2:
        return f"{filtered[0]} / {filtered[1]}"
    elif len(filtered) == 1:
        return filtered[0]
    return "综合题材"

def calculate_stage_score(row, stage):
    """
    分阶段打分逻辑：
    - 启动阶段：量比较大（3-8）是核心，权重占 70%
    - 主升阶段：换手率高（10-25%）是核心，权重占 70%
    """
    if stage == 'Startup':
        s_qr = np.clip((row['量比'] - 3) / 5 * 70, 0, 70)
        s_to = np.clip((row['换手%'] - 5) / 5 * 30, 0, 30)
        return int(s_qr + s_to)
    else:
        s_to = np.clip((row['换手%'] - 10) / 15 * 70, 0, 70)
        s_qr = np.clip((row['量比'] - 2) / 3 * 30, 0, 30)
        return int(s_to + s_qr)

def run_momentum_pipeline():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 启动动能分层扫描 (去伪存真版)...")

    # 1. 获取日期基准
    with engine_quant.connect() as conn:
        today = conn.execute(text("SELECT MAX(trade_date) FROM stk_factors")).fetchone()[0]
        yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
        yesterday = yest_res[0]

    # 2. 执行全市场初筛 SQL (联表获取所有必要因子)
    query_sql = text("""
        SELECT 
            f.symbol, s.name, k.turnover_rate as '换手%', f.f_quantity_ratio as '量比',
            f.f_macd_dif as 'DIF', k.close, k.open, ky.close as 'prev_close',
            (SELECT GROUP_CONCAT(DISTINCT sector_name) FROM stock_sector_relation WHERE symbol = f.symbol) as all_sectors
        FROM stk_factors f
        INNER JOIN stk_daily_kline k ON f.symbol = k.symbol AND k.trade_date = :t
        INNER JOIN stk_daily_kline ky ON f.symbol = ky.symbol AND ky.trade_date = :y
        INNER JOIN stocks s ON f.symbol = s.symbol
        WHERE f.trade_date = :t
          AND f.f_macd_dif > 0                         -- 必须多头趋势
          AND k.close > ky.close AND k.close > k.open  -- 必须双重红盘
          AND s.name NOT LIKE '%%ST%%'
          AND (f.symbol LIKE '60%%' OR f.symbol LIKE '00%%' OR f.symbol LIKE '30%%')
    """)

    try:
        with engine_quant.connect() as conn:
            df_all = pd.read_sql(query_sql, conn, params={"t": today, "y": yesterday})
    except Exception as e:
        print(f"❌ 读取行情数据失败: {e}")
        return

    if df_all.empty:
        print("💡 今日全市场活跃度较低，未发现符合形态的个股。")
        return

    # --- 关键修复 1：在 Pandas 循环前强制去重 ---
    df_all = df_all.drop_duplicates(subset=['symbol'])

    # 3. 逻辑分流与数据封装
    final_pool = []
    for _, row in df_all.iterrows():
        to, qr = row['换手%'], row['量比']
        stage, status_text = None, ""
        
        # 判定分层
        if 0.05 <= to < 0.10 and 3 <= qr <= 8:
            stage, status_text = 'Startup', '启动突破'
        elif 0.10 <= to <= 0.25 and 2 <= qr <= 5:
            stage, status_text = 'MainRise', '主升接力'
            
        if stage:
            cleaned_sector = clean_and_pick_two_sectors(row['all_sectors'])
            score = calculate_stage_score(row, stage)
            
            # 构造 JSON 标签
            tags = {
                "stage": stage, 
                "qr": round(float(qr), 2), 
                "to": round(float(to), 2), 
                "dif": round(float(row['DIF']), 3)
            }
            
            final_pool.append({
                'symbol': row['symbol'],
                'trade_date': today,
                'stock_name': row['name'],
                'pool_type': 'short', # 统一存入短线作战池
                'sector_name': cleaned_sector,
                'score': score,
                'status': status_text,
                'tags': json.dumps(tags, ensure_ascii=False),
                'notes': f"{status_text}: 换手率{to:.1f}%, 量比{qr:.1f}。形态健康。",
                'created_at': datetime.datetime.now(),
                'updated_at': datetime.datetime.now(),
                'pct_chg': round((row['close']-row['prev_close'])/row['prev_close']*100, 2) # 仅用于打印
            })

    # 4. 关键：全量覆盖写入数据库 (Delete then Insert)
    if final_pool:
        df_save = pd.DataFrame(final_pool)
        
         # --- 关键修复 2：在写入前再次确认 symbol 唯一性 ---
        df_save = df_save.sort_values('score', ascending=False).drop_duplicates(subset=['symbol'])

        try:
            with engine_review.begin() as conn:
                # 步骤 A：物理删除今日此策略的所有旧记录 (去伪存真)
                print(f"正在抹除数据库中 {today} 的旧短线信号...") #AND (status = '启动突破' OR status = '主升接力') AND pool_type = 'short'
                conn.execute(text("DELETE FROM stock_pools WHERE trade_date = :d AND status != '竞价异动'"), {"d": today})
                
                # 步骤 B：写入当前最新结果
                # 去掉不属于数据库字段的临时列
                save_cols = ['symbol', 'trade_date', 'stock_name', 'pool_type', 'sector_name', 'score', 'status', 'tags', 'notes', 'created_at', 'updated_at']
                df_save[save_cols].to_sql('stock_pools', con=conn, if_exists='append', index=False, chunksize=1000)
            
            # --- 5. 终端输出报告 ---
            pd.set_option('display.max_colwidth', 50)
            print("\n" + "⚡" * 8 + f" {today} 动能分层黑马池报告 " + "⚡" * 8)
            print("-" * 125)
            # 按照状态分组，再按分数排序打印
            report = df_save.sort_values(['status', 'score'], ascending=[False, False])
            print(report[['symbol', 'stock_name', 'sector_name', 'status', 'score', 'pct_chg']].to_string(index=False))
            print("-" * 125)
            print(f"✅ 净化完成！今日共存入 {len(df_save)} 只标的，已剔除盘中走弱的‘骗子票’。")
            
        except Exception as e:
            print(f"❌ 数据库写入失败: {e}")
    else:
        print("💡 当前未扫描到符合‘启动’或‘主升’逻辑的强势个股。")

if __name__ == "__main__":
    run_momentum_pipeline()