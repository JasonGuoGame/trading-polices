import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys

warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
sys.path.append(r"C:\ws\trading-polices\config")
import config 

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def clean_and_pick_two_sectors(sector_str):
    """过滤黑名单并挑选前两个核心板块"""
    if not sector_str: return "其他"
    raw_list = sector_str.split(',')
    filtered = [s.replace('行业-', '').replace('概念-', '') for s in raw_list if not any(noise in s for noise in config.SECTOR_BLACKLIST)]
    if len(filtered) >= 2: return f"{filtered[0]} / {filtered[1]}"
    elif len(filtered) == 1: return filtered[0]
    return "综合题材"

def save_market_earning_effect(perf_results, winner_name, trade_date):
    """
    【核心改动】直接将胜率作为评分存入数据库
    """
    mapping = {
        "⚡ 连板接力": "limit_up_score",
        "🌊 趋势主升": "trend_score",
        "🔥 主线共振": "theme_score",
        "🧘 超跌低吸": "low_suck_score",
        "🚀 容量趋势": "capacity_score",
        "💀 退潮风险": "loss_score"
    }
    
    data_row = {
        'trade_date': trade_date,
        'market_style': winner_name.replace('⚡ ','').replace('🌊 ','').replace('🔥 ','').replace('🧘 ','').replace('🚀 ','').replace('💀 ',''),
        'created_at': datetime.datetime.now()
    }
    
    for res in perf_results:
        col = mapping.get(res['name'])
        if col:
            # 直接使用胜率作为分值 (0-100)
            data_row[col] = int(res['win_rate'])

    df_effect = pd.DataFrame([data_row])
    
    try:
        with engine_quant.begin() as conn:
            df_effect.to_sql('temp_market_effect', con=conn, if_exists='replace', index=False)
            upsert_sql = text("""
                INSERT INTO market_earning_effect (trade_date, limit_up_score, trend_score, theme_score, low_suck_score, capacity_score, loss_score, market_style, created_at)
                SELECT trade_date, limit_up_score, trend_score, theme_score, low_suck_score, capacity_score, loss_score, market_style, created_at FROM temp_market_effect
                ON DUPLICATE KEY UPDATE 
                    limit_up_score = VALUES(limit_up_score), trend_score = VALUES(trend_score),
                    theme_score = VALUES(theme_score), low_suck_score = VALUES(low_suck_score),
                    capacity_score = VALUES(capacity_score), loss_score = VALUES(loss_score),
                    market_style = VALUES(market_style);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_market_effect;"))
        print(f"📊 胜率分看板已更新。当前最高胜率模式: {data_row['market_style']}")
    except Exception as e:
        print(f"❌ 存入赚钱效应表失败: {e}")

def save_to_stock_pool(df, regime_name, trade_date):
    """同步个股至作战池数据库"""
    if df.empty: return
    now = datetime.datetime.now()
    pool_type = 'long' if '趋势' in regime_name or '容量' in regime_name else 'short'
    db_status = f"赢家模式:{regime_name.strip()}"
    
    records = []
    for _, row in df.iterrows():
        cleaned_sector = clean_and_pick_two_sectors(row['all_sectors'])
        tags_dict = {"regime": regime_name, "vol": float(row.get('量能倍数', 0)), "price": float(row.get('现价', 0))}
        records.append({
            'symbol': row['symbol'], 'trade_date': trade_date, 'stock_name': row['name'],
            'pool_type': pool_type, 'sector_name': cleaned_sector,
            'score': int(row.get('量能倍数', 0) * 10), 'status': db_status,
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"基于最高胜率模式【{regime_name}】选入。",
            'created_at': now, 'updated_at': now
        })

    df_save = pd.DataFrame(records).drop_duplicates(subset=['symbol'])
    with engine_review.begin() as conn:
        conn.execute(text("DELETE FROM stock_pools WHERE trade_date = :d AND status = :s"), {"d": trade_date, "s": db_status})
        df_save.to_sql('stock_pools', con=conn, if_exists='append', index=False)
    print(f"✅ 成功将 {len(df_save)} 只标的存入股票池。")

def get_market_winner_and_pick_stocks():
    print(f"[{datetime.datetime.now()}] 🔍 启动 V8.0 胜率优先·赢家跟随系统...")

    with engine_quant.connect() as conn:
        dates = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(dates) < 2: return
        t_today, t_yest = dates[0][0], dates[1][0]

        perf_df = pd.read_sql(text("SELECT symbol, (close-open)/open*100 as ret FROM stk_daily_kline WHERE trade_date=:t AND open>0"), conn, params={"t": t_today})
        perf_map = dict(zip(perf_df['symbol'], perf_df['ret'].dropna()))

        regimes = {
            "⚡ 连板接力": f"SELECT symbol FROM stk_daily_kline k WHERE trade_date='{t_yest}' AND close >= ROUND((SELECT close FROM stk_daily_kline WHERE symbol=k.symbol AND trade_date < '{t_yest}' ORDER BY trade_date DESC LIMIT 1)*1.098, 2)",
            "🌊 趋势主升": f"SELECT symbol FROM stk_factors WHERE trade_date='{t_yest}' AND f_macd_dif > 0 AND f_bb_m < (SELECT close FROM stk_daily_kline WHERE symbol=stk_factors.symbol AND trade_date='{t_yest}')",
            "🧘 超跌低吸": f"SELECT symbol FROM stk_factors WHERE trade_date='{t_yest}' AND f_rsi_14 < 35",
            "🔥 主线共振": f"SELECT symbol FROM stock_sector_relation r JOIN stk_sector_fund_flow f ON (r.sector_name=CONCAT('行业-', f.sector_name) OR r.sector_name=CONCAT('概念-', f.sector_name)) WHERE f.trade_date='{t_yest}' AND f.net_inflow_amount > 2.0",
            "🚀 容量趋势": f"SELECT f.symbol FROM stk_factors f JOIN stk_fundamental fund ON f.symbol = fund.symbol WHERE f.trade_date='{t_yest}' AND f.f_macd_dif > 0 AND fund.total_mv > 300",
            "💀 退潮风险": f"SELECT symbol FROM stk_daily_kline k WHERE trade_date='{t_yest}' AND (close - open)/open > 0.07"
        }

        perf_results = []
        for name, sql in regimes.items():
            sigs = pd.read_sql(text(sql), conn)['symbol'].tolist()
            rets = [perf_map[s] for s in sigs if s in perf_map and not np.isnan(perf_map[s])]
            if rets:
                perf_results.append({'name': name, 'avg_ret': np.mean(rets), 'win_rate': (np.array(rets)>0).mean() * 100, 'count': len(rets)})

        # --- 【关键改动】优先按 win_rate（胜率）排序 ---
        winner_df = pd.DataFrame(perf_results).sort_values(['win_rate', 'avg_ret'], ascending=[False, False])
        winner_name = winner_df.iloc[0]['name']

        save_market_earning_effect(perf_results, winner_name, t_today)

        print("\n" + "🏁" * 10 + f" 模式胜率看板 ({t_today}) " + "🏁" * 10)
        print(winner_df[['name', 'win_rate', 'avg_ret', 'count']].to_string(index=False))
    
        print(f"\n✅ 选股指令：跟随最高胜率模式 【{winner_name}】")

        # 1. 基础过滤：排除 ST，代码锁定主板
        base_filter = "AND s.name NOT LIKE '%ST%' AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%' OR k.symbol LIKE '30%')"
        
        # 2. 板块聚合子查询
        all_sectors_sql = "(SELECT GROUP_CONCAT(DISTINCT sector_name) FROM stock_sector_relation WHERE symbol = k.symbol)"

        if "容量" in winner_name:
            print("🎯 正在挖掘【容量趋势】中军标的...")
            pick_sql = f"""
                SELECT k.symbol, MAX(s.name) as name, MAX(f.f_vol_ratio) as '量能倍数', MAX(k.close) as '现价', {all_sectors_sql} as all_sectors 
                FROM stk_daily_kline k
                JOIN stocks s ON k.symbol = s.symbol
                JOIN stk_factors f ON k.symbol = f.symbol AND k.trade_date = f.trade_date
                JOIN stk_fundamental fund ON k.symbol = fund.symbol
                WHERE k.trade_date = '{t_today}' AND f.f_macd_dif > 0 AND fund.total_mv > 300 AND f.f_vol_ratio > 1.2 {base_filter}
                GROUP BY k.symbol ORDER BY MAX(k.amount) DESC LIMIT 15
            """
        elif "趋势" in winner_name:
            print("🎯 正在执行【趋势主升】三军会师精准筛选 (兼容严格模式)...")
            pick_sql = f"""
                SELECT 
                    f.symbol, 
                    MAX(s.name) as name, 
                    MAX(f.f_macd_dif) as f_macd_dif, 
                    MAX(f.f_vol_ratio) as '量能倍数', 
                    MAX(k.close) as '现价',
                    MAX(ab.surge_count) as '脉冲次数',
                    MAX(flow.net_inflow_amount) as '板块流入',
                    {all_sectors_sql} as all_sectors
                FROM stk_factors f
                JOIN stk_daily_kline k ON f.symbol = k.symbol AND f.trade_date = k.trade_date
                JOIN stocks s ON f.symbol = s.symbol
                JOIN stk_capital_abnormal ab ON f.symbol = ab.symbol AND f.trade_date = ab.trade_date
                JOIN stock_sector_relation r ON f.symbol = r.symbol
                JOIN stk_sector_fund_flow flow ON (r.sector_name = CONCAT('行业-', flow.sector_name) OR r.sector_name = CONCAT('概念-', flow.sector_name))
                WHERE f.trade_date = '{t_today}'
                  AND flow.trade_date = '{t_today}'
                  AND f.f_macd_dif > f.f_macd_dea        
                  AND f.f_macd_dif > 0                  
                  AND k.close > f.f_bb_m                
                  AND ab.surge_count >= 2               
                  AND flow.net_inflow_amount > 2.0      
                  AND f.f_ma_cohesion < 0.04            
                  AND f.f_vol_ratio BETWEEN 1.8 AND 4.5 
                  {base_filter}
                GROUP BY f.symbol
                ORDER BY MAX(ab.surge_count) DESC, MAX(f.f_vol_ratio) DESC
                LIMIT 20
            """
        elif "连板" in winner_name:
            print("🔥 正在筛选【连板接力】强势标的...")
            pick_sql = f"""
                SELECT k.symbol, MAX(s.name) as name, MAX(f.f_vol_ratio) as '量能倍数', MAX(k.close) as '现价', {all_sectors_sql} as all_sectors 
                FROM stk_daily_kline k
                JOIN stocks s ON k.symbol = s.symbol
                JOIN stk_factors f ON k.symbol = f.symbol AND k.trade_date = f.trade_date
                WHERE k.trade_date = '{t_today}' 
                  AND k.close >= ROUND((SELECT close FROM stk_daily_kline WHERE symbol=k.symbol AND trade_date < '{t_today}' ORDER BY trade_date DESC LIMIT 1)*1.098, 2)
                  {base_filter}
                GROUP BY k.symbol ORDER BY MAX(f.f_vol_ratio) DESC LIMIT 10
            """
        elif "主线" in winner_name:
            print("💰 正在筛选【主线共振】吸金标的...")
            pick_sql = f"""
                SELECT k.symbol, MAX(s.name) as name, MAX(f.f_vol_ratio) as '量能倍数', MAX(k.close) as '现价', {all_sectors_sql} as all_sectors 
                FROM stk_daily_kline k
                JOIN stocks s ON k.symbol = s.symbol
                JOIN stk_factors f ON k.symbol = f.symbol AND k.trade_date = f.trade_date
                JOIN stock_sector_relation r ON k.symbol = r.symbol
                JOIN stk_sector_fund_flow flow ON (r.sector_name=CONCAT('行业-', flow.sector_name) OR r.sector_name=CONCAT('概念-', flow.sector_name))
                WHERE k.trade_date = '{t_today}' AND flow.trade_date = '{t_today}' AND flow.net_inflow_amount > 2.0 {base_filter}
                GROUP BY k.symbol ORDER BY MAX(flow.net_inflow_amount) DESC LIMIT 15
            """
        else: # 低吸
            print("🧘 正在寻找【超跌低吸】反转标的...")
            pick_sql = f"""
                SELECT k.symbol, MAX(s.name) as name, MAX(f.f_rsi_14) as 'RSI', MAX(f.f_vol_ratio) as '量能倍数', MAX(k.close) as '现价', {all_sectors_sql} as all_sectors 
                FROM stk_daily_kline k
                JOIN stocks s ON k.symbol = s.symbol
                JOIN stk_factors f ON k.symbol = f.symbol AND f.trade_date = f.trade_date
                WHERE k.trade_date = '{t_today}' AND f.f_rsi_14 < 30 {base_filter}
                GROUP BY k.symbol ORDER BY MAX(f.f_rsi_14) ASC LIMIT 15
            """

        final_picks = pd.read_sql(text(pick_sql), conn)
        if not final_picks.empty:
            final_picks['显示板块'] = final_picks['all_sectors'].apply(clean_and_pick_two_sectors)
            print("-" * 110)
            print(final_picks[['symbol', 'name', '显示板块', '量能倍数', '现价']].head(15).to_string(index=False))
            save_to_stock_pool(final_picks, winner_name, t_today)

if __name__ == "__main__":
    get_market_winner_and_pick_stocks()