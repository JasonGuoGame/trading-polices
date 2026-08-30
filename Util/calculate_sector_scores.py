import json
import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime
import numpy as np

# --- 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_limit_threshold(symbol):
    if symbol.startswith(('30', '68')): return 19.8
    return 9.8

def calculate_sector_scores_v4():
    # 1. 自动获取最近的三个交易日
    with engine_quant.connect() as conn:
        res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 3")).fetchall()
        if len(res) < 3:
            print("❌ 历史数据不足。")
            return
        today, yesterday = res[0][0], res[1][0]

    print(f"🚀 启动板块综合评分系统 v4 (含新高明细存入) | 目标日期: {today}")

    # ---------------------------------------------------------
    # 步骤 A: 获取【标准板块名单】及资金数据
    # ---------------------------------------------------------
    flow_sql = f"SELECT sector_name, net_inflow_amount, net_inflow_rate FROM stk_sector_fund_flow WHERE trade_date = '{today}'"
    df_flow = pd.read_sql(flow_sql, engine_quant).set_index('sector_name')
    if df_flow.empty:
        print(f"❌ 逻辑中断：尚未生成 {today} 的资金流数据。")
        return
    official_sector_list = df_flow.index.tolist()

    # ---------------------------------------------------------
    # 步骤 B: 提取个股数据 (🌟 此处修改 SQL，加入 stocks 表获取股票名称)
    # ---------------------------------------------------------
    kline_sql = text("""
        SELECT t.symbol, s.name AS stock_name, t.close, t.high, t.amount, y.close AS prev_close, r.sector_name AS raw_db_sector_name
        FROM stk_daily_kline t
        JOIN stocks s ON t.symbol = s.symbol
        JOIN stk_daily_kline y ON t.symbol = y.symbol AND y.trade_date = :y_date
        JOIN stock_sector_relation r ON t.symbol = r.symbol
        WHERE t.trade_date = :t_date
    """)
    with engine_quant.connect() as conn:
        df_k_raw = pd.read_sql(kline_sql, conn, params={"t_date": today, "y_date": yesterday})

    def map_to_official(db_name):
        clean_name = db_name.replace('行业-', '').replace('概念-', '').replace('Ⅱ', '').replace('Ⅲ', '').strip()
        if clean_name in official_sector_list: return clean_name
        for off_name in official_sector_list:
            if off_name in clean_name: return off_name
        return None

    df_k_raw['official_name'] = df_k_raw['raw_db_sector_name'].apply(map_to_official)
    df_k = df_k_raw.dropna(subset=['official_name']).copy()
    if df_k.empty: return

    # ---------------------------------------------------------
    # 步骤 B.2: 计算个股新高状态 (20/60/250日)
    # ---------------------------------------------------------
    print("📊 正在计算全市场个股新高状态...")
    high_start_date = (pd.to_datetime(today) - pd.Timedelta(days=400)).strftime('%Y-%m-%d')
    sql_hist = text("""
        SELECT symbol, trade_date, high, close 
        FROM stk_daily_kline 
        WHERE trade_date >= :sd AND trade_date <= :td
    """)
    df_hist = pd.read_sql(sql_hist, engine_quant, params={"sd": high_start_date, "td": today})
    
    df_hist = df_hist.sort_values(['symbol', 'trade_date'])
    df_hist['max_20'] = df_hist.groupby('symbol')['high'].transform(lambda x: x.shift(1).rolling(20).max())
    df_hist['max_60'] = df_hist.groupby('symbol')['high'].transform(lambda x: x.shift(1).rolling(60).max())
    df_hist['max_250'] = df_hist.groupby('symbol')['high'].transform(lambda x: x.shift(1).rolling(250).max())

    df_high_today = df_hist[df_hist['trade_date'] == today].copy()
    df_high_today['is_h20'] = (df_high_today['close'] > df_high_today['max_20']).astype(int)
    df_high_today['is_h60'] = (df_high_today['close'] > df_high_today['max_60']).astype(int)
    df_high_today['is_h250'] = (df_high_today['close'] > df_high_today['max_250']).astype(int)

    df_k = df_k.merge(df_high_today[['symbol', 'is_h20', 'is_h60', 'is_h250']], on='symbol', how='left').fillna(0)

    # 计算涨跌幅及涨停状态
    df_k['chg_pct'] = (df_k['close'] / df_k['prev_close'] - 1) * 100
    df_k['is_limit'] = df_k.apply(lambda r: r['close'] >= round(r['prev_close']*(1+get_limit_threshold(r['symbol'])/100), 2), axis=1)
    df_k['hit_limit'] = df_k.apply(lambda r: r['high'] >= round(r['prev_close']*(1+get_limit_threshold(r['symbol'])/100), 2), axis=1)

    # ---------------------------------------------------------
    # 步骤 C: 准备辅助数据
    # ---------------------------------------------------------
    attack_symbols = pd.read_sql(f"SELECT symbol FROM stk_market_attack_log WHERE trade_date = '{today}'", engine_quant)['symbol'].unique()
    df_prev_scores = pd.read_sql(f"SELECT sector_name, rank_pos FROM stk_sector_scores WHERE trade_date = '{yesterday}'", engine_review).set_index('sector_name')

    # ---------------------------------------------------------
    # 步骤 D: 核心评分循环 (整合新高统计)
    # ---------------------------------------------------------
    sector_results = []
    for name in official_sector_list:
        group = df_k[df_k['official_name'] == name]
        if group.empty: continue
        
        # 1. 资金分 (30)
        f = df_flow.loc[name]
        m_score = min(max(float(f['net_inflow_amount'])/1e8 * 1.5, 0), 15) + min(max(float(f['net_inflow_rate']) * 2, 0), 10) + (5 if float(f['net_inflow_amount']) > 0 else 0)

        # 2. 赚钱效应分 (25)
        up_rate = (group['chg_pct'] > 0).sum() / len(group)
        limit_count = group['is_limit'].sum()
        broken_rate = (group['hit_limit'].sum() - limit_count) / group['hit_limit'].sum() if group['hit_limit'].sum() > 0 else 0
        profit_s = (up_rate * 10) + min(limit_count * 2, 10) + max(5 * (1 - broken_rate), 0)

        # 3. 龙头强度 (20)
        unique_group = group.drop_duplicates(subset=['symbol'])
        leaders = unique_group.sort_values('amount', ascending=False).head(5)
        leader_pct = leaders['chg_pct'].mean()
        if leader_pct >= 5: core_score = 5
        elif leader_pct >= 3: core_score = 5
        elif leader_pct >= 0: core_score = 3
        else: core_score = 0
        l_count = unique_group['is_limit'].sum()
        if l_count >= 20: limit_score = 10
        elif l_count >= 10: limit_score = 6
        else: limit_score = 0
        top_amount = leaders['amount'].sum()
        sector_amount = unique_group['amount'].sum()
        amount_ratio = top_amount / sector_amount
        if amount_ratio >= 0.3: amount_score = 5
        elif amount_ratio >= 0.15: amount_score = 3
        else: amount_score = 1
        leader_s = (core_score + limit_score + amount_score)
     
        # 4. 攻击力度 (15)
        attack_s = min(unique_group['symbol'].isin(attack_symbols).sum() * 1.5, 15)

        # 5. 持续性基础分 (10)
        cont_s = 5
        if name in df_prev_scores.index:
            p_rank = df_prev_scores.loc[name, 'rank_pos']
            if p_rank == 1: cont_s = 10
            elif p_rank <= 3: cont_s = 8
            else: cont_s = 6

        # 获取新高个股家数统计
        h20_c = int(unique_group['is_h20'].sum())
        h60_c = int(unique_group['is_h60'].sum())
        h250_c = int(unique_group['is_h250'].sum())

        sector_results.append({
            'trade_date': today, 'sector_name': name,
            'money_score': m_score, 'profit_score': profit_s,
            'leader_score': leader_s, 'attack_score': attack_s,
            'continuity_score': cont_s, 
            'total_score': m_score + profit_s + leader_s + attack_s + cont_s,
            'high_20d_count': h20_c, 'high_60d_count': h60_c, 'high_250d_count': h250_c
        })

    # ---------------------------------------------------------
    # 🌟 [新增逻辑] 步骤 G: 提取并存入个股新高明细
    # ---------------------------------------------------------
    print("💾 正在同步个股新高明细到 stk_new_high_detail...")
    # 筛选至少满足一种新高的股票
    df_high_detail = df_k[(df_k['is_h20']==1) | (df_k['is_h60']==1) | (df_k['is_h250']==1)].copy()
    
    if not df_high_detail.empty:
        # 准备入库字段
        df_high_detail['trade_date'] = today
        df_save_detail = df_high_detail[[
            'trade_date', 'symbol', 'stock_name', 'official_name', 
            'is_h20', 'is_h60', 'is_h250', 'close'
        ]].rename(columns={
            'official_name': 'sector_name',
            'is_h20': 'high_20d',
            'is_h60': 'high_60d',
            'is_h250': 'high_250d'
        })
        
        with engine_review.begin() as conn:
            # 存入临时表并 Upsert
            df_save_detail.to_sql('tmp_high_detail', conn, if_exists='replace', index=False)
            conn.execute(text("""
                INSERT INTO stk_new_high_detail (trade_date, symbol, stock_name, sector_name, high_20d, high_60d, high_250d, close, created_at)
                SELECT trade_date, symbol, stock_name, sector_name, high_20d, high_60d, high_250d, close, NOW()
                FROM tmp_high_detail
                ON DUPLICATE KEY UPDATE 
                    high_20d=VALUES(high_20d), high_60d=VALUES(high_60d), high_250d=VALUES(high_250d), close=VALUES(close)
            """))
            conn.execute(text("DROP TABLE IF EXISTS tmp_high_detail"))

    # ---------------------------------------------------------
    # 步骤 E: 计算领头羊逻辑
    # ---------------------------------------------------------
    df_res = pd.DataFrame(sector_results).sort_values('total_score', ascending=False)
    df_res['rank_pos'] = range(1, len(df_res) + 1)

    with engine_review.connect() as conn:
        hist_dates_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_sector_scores WHERE trade_date < :d ORDER BY trade_date DESC LIMIT 6"), {"d": today}).fetchall()
        hist_dates = [r[0] for r in hist_dates_res]
        if hist_dates:
            hist_sql = text("SELECT sector_name, COUNT(*) as cnt FROM stk_sector_scores WHERE trade_date IN :dates AND rank_pos <= 15 GROUP BY sector_name")
            hist_counts = pd.read_sql(hist_sql, conn, params={"dates": hist_dates}).set_index('sector_name')['cnt'].to_dict()
        else:
            hist_counts = {}

    def calc_persistence(row):
        past_cnt = hist_counts.get(row['sector_name'], 0)
        current_cnt = past_cnt + (1 if row['rank_pos'] <= 15 else 0)
        return int(current_cnt)

    df_res['persistence_7d'] = df_res.apply(calc_persistence, axis=1)
    df_res['is_leader'] = df_res['persistence_7d'].apply(lambda x: 1 if x >= 3 else 0)

    # ---------------------------------------------------------
    # 步骤 F: 存入数据库 (Upsert 板块总表)
    # ---------------------------------------------------------
    with engine_review.begin() as conn:
        for _, row in df_res.iterrows():
            conn.execute(text("""
                INSERT INTO stk_sector_scores 
                (trade_date, sector_name, money_score, profit_score, leader_score, attack_score, continuity_score, 
                 total_score, rank_pos, persistence_7d, is_leader, high_20d_count, high_60d_count, high_250d_count)
                VALUES 
                (:trade_date, :sector_name, :money_score, :profit_score, :leader_score, :attack_score, :continuity_score, 
                 :total_score, :rank_pos, :persistence_7d, :is_leader, :high_20d_count, :high_60d_count, :high_250d_count)
                ON DUPLICATE KEY UPDATE 
                    total_score=VALUES(total_score), rank_pos=VALUES(rank_pos),
                    persistence_7d=VALUES(persistence_7d), is_leader=VALUES(is_leader),
                    money_score=VALUES(money_score), profit_score=VALUES(profit_score),
                    leader_score=VALUES(leader_score), attack_score=VALUES(attack_score),
                    continuity_score=VALUES(continuity_score),
                    high_20d_count=VALUES(high_20d_count), high_60d_count=VALUES(high_60d_count), high_250d_count=VALUES(high_250d_count)
            """), row.to_dict())

    leaders = df_res[df_res['is_leader'] == 1]['sector_name'].tolist()
    print(f"✅ 评分、明细及领头羊同步完成！当前领头羊板块: {', '.join(leaders) if leaders else '暂无'}")

if __name__ == "__main__":
    calculate_sector_scores_v4()