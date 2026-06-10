import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import sys
import os
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 (支持双库) ---
# 原始行情库
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
# 股票池/复盘库
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def save_to_stock_pool(df_results, trade_date):
    """
    将分析结果存入 trading_review.stock_pools 表
    """
    if df_results.empty:
        return

    now = datetime.datetime.now()
    pool_records = []

    for _, row in df_results.iterrows():
        # A. 计算分值：主力获利越接近0或负数，分值越高
        # 获利 0% 得到 100分，获利 5% 得到 50分
        raw_score = 100 - abs(row['主力获利%']) * 10
        final_score = int(np.clip(raw_score, 0, 100))

        # B. 判定观察等级
        # 主力获利在 -5% 到 3% 之间为重点关注
        is_focus = 1 if -5 <= row['主力获利%'] <= 3 else 0
        watch_lvl = 3 if is_focus == 1 else 1

        # C. 构造 JSON 标签
        tags_dict = {
            "mf_cost": row['主力成本'],
            "market_vwap": row['全天均价'],
            "bias": row['主力获利%'],
            "surge_cnt": row['异动次数']
        }

        pool_records.append({
            'symbol': row['代码'],
            'trade_date': trade_date,
            'stock_name': row['名称'],
            'pool_type': 'short',
            'sector_name': row['板块'],
            'score': final_score,
            'status': '主力入场',
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"主力成本:{row['主力成本']}, 现价偏离主力:{row['主力获利%']}%, 脉冲{row['异动次数']}次",
            'is_watch_focus': 0,
            'watch_level': watch_lvl,
            'created_at': now,
            'updated_at': now
        })

    df_save = pd.DataFrame(pool_records)

    try:
        with engine_review.begin() as conn:
            # 1. 写入临时表
            df_save.to_sql('temp_mf_sync', con=conn, if_exists='replace', index=False)
            
            # 2. 执行 UPSERT (存在则更新分数和备注)
            upsert_sql = text("""
                INSERT INTO stock_pools (
                    symbol, trade_date, stock_name, pool_type, sector_name, 
                    score, status, tags, notes, is_watch_focus, watch_level, created_at, updated_at
                )
                SELECT * FROM temp_mf_sync
                ON DUPLICATE KEY UPDATE 
                    score = VALUES(score),
                    tags = VALUES(tags),
                    notes = VALUES(notes),
                    is_watch_focus = VALUES(is_watch_focus),
                    watch_level = VALUES(watch_level),
                    updated_at = VALUES(updated_at);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_mf_sync;"))
        print(f"✅ 成功同步 {len(df_save)} 条主力成本分析至股票池。")
    except Exception as e:
        print(f"❌ 数据库写入失败: {e}")

def run_main_force_sync():
    print(f"[{datetime.datetime.now()}] 启动【主力成本+板块资金】共振同步系统...")

    with engine_quant.connect() as conn:
        # 1. 获取日期
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_sector_fund_flow ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(date_res) < 2: return
        today, yesterday = date_res[0][0], date_res[1][0]

        # 2. 锁定双日净流入板块
        flow_sql = text("""
            SELECT t.sector_name FROM stk_sector_fund_flow t
            JOIN stk_sector_fund_flow y ON t.sector_name = y.sector_name AND y.trade_date = :yest
            WHERE t.trade_date = :today AND t.net_inflow_amount > 0 AND y.net_inflow_amount > 0
        """)
        hot_sectors = pd.read_sql(flow_sql, conn, params={"today": today, "yest": yesterday})['sector_name'].tolist()
        
        if not hot_sectors:
            print("今日无符合条件的加速流入板块。")
            return

        # 3. 提取异动个股
        sector_list_hy = [f"行业-{s}" for s in hot_sectors]
        sector_list_gn = [f"概念-{s}" for s in hot_sectors]
        
        main_query = text("""
            SELECT DISTINCT a.symbol, a.name, a.surge_count, a.surge_times, a.vol_ratio, r.sector_name
            FROM stk_capital_abnormal a
            JOIN stock_sector_relation r ON a.symbol = r.symbol
            WHERE a.trade_date = :today
              AND (r.sector_name IN :sector_list_plain OR r.sector_name IN :sector_list_hy OR r.sector_name IN :sector_list_gn)
        """)
        
        df_abnormal = pd.read_sql(main_query, conn, params={
            "today": today, "sector_list_plain": hot_sectors,
            "sector_list_hy": sector_list_hy, "sector_list_gn": sector_list_gn
        })

    if df_abnormal.empty:
        print("未发现匹配个股。")
        return

    results = []
    # 4. 计算主力成本
    for _, row in df_abnormal.iterrows():
        symbol = row['symbol']
        try:
            times_list = [f"'{t}:00'" for t in row['surge_times'].split(',')]
            query_min = f"SELECT amount, volume FROM stk_min_kline WHERE symbol='{symbol}' AND DATE(trade_time)='{today}' AND TIME(trade_time) IN ({','.join(times_list)})"
            df_surges = pd.read_sql(query_min, engine_quant)
            
            if df_surges.empty: continue
            
            mf_cost = df_surges['amount'].sum() / (df_surges['volume'].sum() * 100 + 0.01)
            
            query_daily = f"SELECT close, amount, volume FROM stk_daily_kline WHERE symbol='{symbol}' AND trade_date='{today}'"
            df_daily = pd.read_sql(query_daily, engine_quant)
            
            last_price = df_daily['close'].iloc[0]
            market_vwap = df_daily['amount'].iloc[0] / (df_daily['volume'].iloc[0] * 100 + 0.01)
            cost_bias = (last_price - mf_cost) / mf_cost * 100
            
            results.append({
                '代码': symbol, '名称': row['name'], 
                '板块': row['sector_name'].replace('行业-','').replace('概念-',''),
                '异动次数': row['surge_count'], '收盘价': last_price,
                '全天均价': round(market_vwap, 2), '主力成本': round(mf_cost, 2),
                '主力获利%': round(cost_bias, 2)
            })
        except: continue

    # 5. 存储与打印
    if results:
        df_final = pd.DataFrame(results)
        save_to_stock_pool(df_final, today)
        
        # 仅打印重点推荐（获利在合理区间内的）
        best_ones = df_final[(df_final['主力获利%'] > -5) & (df_final['主力获利%'] < 3)]
        if not best_ones.empty:
            print("\n" + "💎" * 5 + " 重点狙击名单 " + "💎" * 5)
            print(best_ones[['代码', '名称', '板块', '主力成本', '收盘价', '主力获利%']].to_string(index=False))
    else:
        print("今日无符合条件个股入库。")

if __name__ == "__main__":
    run_main_force_sync()