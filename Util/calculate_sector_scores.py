import pandas as pd
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
    # 1. 自动获取最近的三个交易日 (用于昨日排名参考)
    with engine_quant.connect() as conn:
        res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 3")).fetchall()
        if len(res) < 3:
            print("❌ 历史数据不足。")
            return
        today, yesterday = res[0][0], res[1][0]

    print(f"🚀 启动板块综合评分系统 v4 | 目标日期: {today}")

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
    # 步骤 B: 提取个股数据并对齐名称
    # ---------------------------------------------------------
    kline_sql = text("""
        SELECT t.symbol, t.close, t.high, t.amount, y.close AS prev_close, r.sector_name AS raw_db_sector_name
        FROM stk_daily_kline t
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

    # 计算涨跌幅及涨停状态
    df_k['chg_pct'] = (df_k['close'] / df_k['prev_close'] - 1) * 100
    df_k['is_limit'] = df_k.apply(lambda r: r['close'] >= round(r['prev_close']*(1+get_limit_threshold(r['symbol'])/100), 2), axis=1)
    df_k['hit_limit'] = df_k.apply(lambda r: r['high'] >= round(r['prev_close']*(1+get_limit_threshold(r['symbol'])/100), 2), axis=1)

    # ---------------------------------------------------------
    # 步骤 C: 准备辅助数据 (攻击/持续性)
    # ---------------------------------------------------------
    attack_symbols = pd.read_sql(f"SELECT symbol FROM stk_market_attack_log WHERE trade_date = '{today}'", engine_quant)['symbol'].unique()
    df_prev_scores = pd.read_sql(f"SELECT sector_name, rank_pos FROM stk_sector_scores WHERE trade_date = '{yesterday}'", engine_review).set_index('sector_name')

    # ---------------------------------------------------------
    # 步骤 D: 核心评分循环
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
        leaders = unique_group.sort_values('amount', ascending=False).head(3)
        l_avg_chg = leaders['chg_pct'].mean()
        if l_avg_chg > 2 and l_avg_chg > unique_group['chg_pct'].mean(): leader_s = 20
        elif l_avg_chg < 0: leader_s = 5
        else: leader_s = 12

        # 4. 攻击力度 (15)
        attack_s = min(unique_group['symbol'].isin(attack_symbols).sum() * 1.5, 15)

        # 5. 持续性基础分 (10)
        cont_s = 5
        if name in df_prev_scores.index:
            p_rank = df_prev_scores.loc[name, 'rank_pos']
            if p_rank == 1: cont_s = 10
            elif p_rank <= 3: cont_s = 8
            else: cont_s = 6

        sector_results.append({
            'trade_date': today, 'sector_name': name,
            'money_score': m_score, 'profit_score': profit_s,
            'leader_score': leader_s, 'attack_score': attack_s,
            'continuity_score': cont_s, 'total_score': m_score + profit_s + leader_s + attack_s + cont_s
        })

    # ---------------------------------------------------------
    # 🌟 步骤 E: 计算领头羊逻辑 (连强信号)
    # ---------------------------------------------------------
    df_res = pd.DataFrame(sector_results).sort_values('total_score', ascending=False)
    df_res['rank_pos'] = range(1, len(df_res) + 1)

    # 1. 获取过去 6 个交易日的历史入围次数 (Top 15)
    with engine_review.connect() as conn:
        hist_dates_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_sector_scores WHERE trade_date < :d ORDER BY trade_date DESC LIMIT 6"), {"d": today}).fetchall()
        hist_dates = [r[0] for r in hist_dates_res]
        
        if hist_dates:
            hist_sql = text("SELECT sector_name, COUNT(*) as cnt FROM stk_sector_scores WHERE trade_date IN :dates AND rank_pos <= 15 GROUP BY sector_name")
            hist_counts = pd.read_sql(hist_sql, conn, params={"dates": hist_dates}).set_index('sector_name')['cnt'].to_dict()
        else:
            hist_counts = {}

    # 2. 计算 persistence_7d 和 is_leader
    def calc_persistence(row):
        past_cnt = hist_counts.get(row['sector_name'], 0)
        # 如果今天排名在前 15，总次数 +1
        current_cnt = past_cnt + (1 if row['rank_pos'] <= 15 else 0)
        return int(current_cnt)

    df_res['persistence_7d'] = df_res.apply(calc_persistence, axis=1)
    df_res['is_leader'] = df_res['persistence_7d'].apply(lambda x: 1 if x >= 3 else 0)

    # ---------------------------------------------------------
    # 步骤 F: 存入数据库 (Upsert)
    # ---------------------------------------------------------
    with engine_review.begin() as conn:
        for _, row in df_res.iterrows():
            conn.execute(text("""
                INSERT INTO stk_sector_scores 
                (trade_date, sector_name, money_score, profit_score, leader_score, attack_score, continuity_score, total_score, rank_pos, persistence_7d, is_leader)
                VALUES (:trade_date, :sector_name, :money_score, :profit_score, :leader_score, :attack_score, :continuity_score, :total_score, :rank_pos, :persistence_7d, :is_leader)
                ON DUPLICATE KEY UPDATE 
                    total_score=VALUES(total_score), rank_pos=VALUES(rank_pos),
                    persistence_7d=VALUES(persistence_7d), is_leader=VALUES(is_leader),
                    money_score=VALUES(money_score), profit_score=VALUES(profit_score),
                    leader_score=VALUES(leader_score), attack_score=VALUES(attack_score),
                    continuity_score=VALUES(continuity_score)
            """), row.to_dict())

    leaders = df_res[df_res['is_leader'] == 1]['sector_name'].tolist()
    print(f"✅ 评分与领头羊同步完成！当前领头羊板块: {', '.join(leaders) if leaders else '暂无'}")

if __name__ == "__main__":
    calculate_sector_scores_v4()