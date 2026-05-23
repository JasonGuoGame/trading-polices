import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys

warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
# 这里使用简化的路径添加方式
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的全局配置文件

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
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

def save_to_stock_pool(df, regime_name, trade_date):
    """同步至作战池数据库"""
    if df.empty: return
    now = datetime.datetime.now()
    pool_type = 'long' if '趋势' in regime_name else 'short'
    db_status = f"赢家模式:{regime_name.strip()}"
    
    records = []
    for _, row in df.iterrows():
        # 清洗并提取双板块
        cleaned_sector = clean_and_pick_two_sectors(row['all_sectors'])
        
        tags_dict = {
            "regime": regime_name,
            "vol_ratio": float(row.get('量能倍数', 0)),
            "price": float(row.get('现价', 0))
        }
        if '收盘位置' in row: tags_dict['pos'] = float(row['收盘位置'])

        records.append({
            'symbol': row['symbol'],
            'trade_date': trade_date,
            'stock_name': row['name'],
            'pool_type': pool_type,
            'sector_name': cleaned_sector, # 双板块存入
            'score': int(row.get('量能倍数', 0) * 10),
            'status': db_status,
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"今日赢家模式【{regime_name}】触发。量比:{row.get('量能倍数',0)}",
            'created_at': now,
            'updated_at': now
        })

    df_save = pd.DataFrame(records)
    with engine_review.begin() as conn:
        conn.execute(text("DELETE FROM stock_pools WHERE trade_date = :d AND status = :s"), {"d": trade_date, "s": db_status})
        df_save.to_sql('stock_pools', con=conn, if_exists='append', index=False)
    print(f"✅ 成功同步 {len(df_save)} 只标的到股票池。")

def get_market_winner_and_pick_stocks():
    print(f"[{datetime.datetime.now()}] 🔍 启动 V6.0 赢家跟随+板块脱水系统...")

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
            # --- 新增 1：🚀 容量趋势 ---
            "🚀 容量趋势": f"SELECT f.symbol FROM stk_factors f JOIN stk_fundamental fund ON f.symbol = fund.symbol WHERE f.trade_date='{t_yest}' AND f.f_macd_dif > 0 AND fund.total_mv > 300",
            # --- 新增 2：💀 退潮风险 (昨日大涨股的今日反馈) ---
            "💀 退潮风险": f"SELECT symbol FROM stk_daily_kline k WHERE trade_date='{t_yest}' AND (close - open)/open > 0.07"
        }

        perf_results = []
        for name, sql in regimes.items():
            sigs = pd.read_sql(text(sql), conn)['symbol'].tolist()
            rets = [perf_map[s] for s in sigs if s in perf_map and not np.isnan(perf_map[s])]
            if rets:
                perf_results.append({'name': name, 'avg_ret': np.mean(rets), 'win_rate': (np.array(rets)>0).mean() * 100, 'count': len(rets)})

        winner_df = pd.DataFrame(perf_results).sort_values('win_rate', ascending=False)
        winner_name = winner_df.iloc[0]['name']

        print("\n" + "🏁" * 10 + f" 模式复盘看板 ({t_today}) " + "🏁" * 10)
        print(winner_df[['name', 'avg_ret', 'win_rate', 'count']].to_string(index=False))
    
        print(f"\n✅ 确认当前赢家模式：【{winner_name}】")

        print(f"🏆 今日最强模式：【{winner_name}】")

        # 核心筛选 SQL：增加 GROUP_CONCAT 以支持双板块处理
        base_filter = "AND s.name NOT LIKE '%ST%' AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%' OR k.symbol LIKE '30%')"
        all_sectors_sql = "(SELECT GROUP_CONCAT(DISTINCT sector_name) FROM stock_sector_relation WHERE symbol = k.symbol)"

        if "趋势" in winner_name:
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
            pick_sql = f"SELECT k.symbol, s.name, f.f_vol_ratio as '量能倍数', k.close as '现价', {all_sectors_sql} as all_sectors FROM stk_daily_kline k JOIN stocks s ON k.symbol = s.symbol JOIN stk_factors f ON k.symbol = f.symbol AND f.trade_date = k.trade_date WHERE k.trade_date = '{t_today}' AND k.close >= ROUND((SELECT close FROM stk_daily_kline WHERE symbol=k.symbol AND trade_date < '{t_today}' ORDER BY trade_date DESC LIMIT 1)*1.098, 2) {base_filter}"
        elif "主线" in winner_name:
            pick_sql = f"SELECT k.symbol, s.name, f.f_vol_ratio as '量能倍数', k.close as '现价', {all_sectors_sql} as all_sectors FROM stk_daily_kline k JOIN stocks s ON k.symbol = s.symbol JOIN stk_factors f ON k.symbol = f.symbol AND f.trade_date = k.trade_date JOIN stock_sector_relation r ON k.symbol = r.symbol JOIN stk_sector_fund_flow flow ON (r.sector_name=CONCAT('行业-', flow.sector_name) OR r.sector_name=CONCAT('概念-', flow.sector_name)) WHERE k.trade_date = '{t_today}' AND flow.trade_date = '{t_today}' AND flow.net_inflow_amount > 2.0 {base_filter}"
        else: # 低吸
            pick_sql = f"SELECT k.symbol, s.name, f.f_rsi_14, f.f_vol_ratio as '量能倍数', k.close as '现价', {all_sectors_sql} as all_sectors FROM stk_daily_kline k JOIN stocks s ON k.symbol = s.symbol JOIN stk_factors f ON k.symbol = f.symbol AND f.trade_date = k.trade_date WHERE k.trade_date = '{t_today}' AND f.f_rsi_14 < 30 {base_filter}"

        final_picks = pd.read_sql(text(pick_sql), conn)

    if not final_picks.empty:
        final_picks = final_picks.drop_duplicates('symbol')
        # 在打印前先应用双板块清洗
        final_picks['显示板块'] = final_picks['all_sectors'].apply(clean_and_pick_two_sectors)
        
        print("-" * 110)
        print(final_picks[['symbol', 'name', '显示板块', '量能倍数', '现价']].head(15).to_string(index=False))
        print("-" * 110)
        
        save_to_stock_pool(final_picks, winner_name, t_today)
    else:
        print("💡 当前未发现符合赢家模式过滤标准的个股。")

if __name__ == "__main__":
    get_market_winner_and_pick_stocks()