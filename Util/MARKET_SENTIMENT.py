import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import sys
import numpy as np

# --- 1. 引入全局配置 ---
INDEX_LIST = [
    '000001.SH', '399001.SZ', '399006.SZ', '000300.SH', '000852.SH',
]

# --- 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_limit_threshold(symbol):
    if symbol.startswith(('30', '68')): return 19.8
    if symbol.startswith(('8', '4')): return 29.8
    return 9.8

def get_top_sectors(target_date):
    try:
        with engine_review.connect() as conn:
            query = text("""
                SELECT sector_name FROM stk_sector_breadths 
                WHERE trade_date = :d AND sector_type = 'industry' 
                ORDER BY rank_pos ASC LIMIT 5
            """)
            res = conn.execute(query, {"d": target_date}).fetchall()
            return [r[0] for r in res]
    except Exception:
        return []

def analyze_market_sentiment():
    # 1. 获取日期序列
    with engine_quant.connect() as conn:
        res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 10"))
        dates = [row[0] for row in res.fetchall()]
    
    if len(dates) < 3: return
    today, yesterday = dates[0], dates[1]
    print(f"[{datetime.datetime.now()}] 🚀 启动全维度市场情绪分析 (纯个股模式): {today}")

    # 2. 读取最近 6 天数据
    lookback_dates = dates[:6] 
    sql_k = text("""
        SELECT symbol, trade_date, open, high, low, close, amount, volume 
        FROM stk_daily_kline 
        WHERE trade_date IN :d 
        AND symbol NOT IN :idx
    """)
    df_raw = pd.read_sql(sql_k, engine_quant, params={"d": lookback_dates, "idx": INDEX_LIST})
    
    # 3. 连板轨迹识别
    df_raw = df_raw.sort_values(['symbol', 'trade_date'])
    df_raw['prev_close'] = df_raw.groupby('symbol')['close'].shift(1)
    df_raw = df_raw.dropna(subset=['prev_close'])
    
    df_raw['is_limit'] = df_raw.apply(lambda r: r['close'] >= round(r['prev_close'] * (1 + get_limit_threshold(r['symbol'])/100), 2), axis=1)
    df_raw['is_hit'] = df_raw.apply(lambda r: r['high'] >= round(r['prev_close'] * (1 + get_limit_threshold(r['symbol'])/100), 2), axis=1)
    df_raw['chg_pct'] = (df_raw['close'] / df_raw['prev_close'] - 1) * 100

    df_raw['board_height'] = 0
    for sym, group in df_raw.groupby('symbol'):
        height = 0
        for idx, row in group.iterrows():
            if row['is_limit']: height += 1
            else: height = 0
            df_raw.at[idx, 'board_height'] = height

    # 4. 提取今日与昨日切片
    df_today = df_raw[df_raw['trade_date'] == today].set_index('symbol')
    df_yest = df_raw[df_raw['trade_date'] == yesterday].set_index('symbol')

    # --- 核心指标计算 ---
    adv_count = int((df_today['chg_pct'] > 0).sum())
    dec_count = int((df_today['chg_pct'] < 0).sum())
    flat_count = int((df_today['chg_pct'] == 0).sum()) # 🌟 新增：计算平盘数量
    
    limit_up_today = df_today['is_limit'].sum()
    hit_limit_today = df_today['is_hit'].sum()
    broken_limit = hit_limit_today - limit_up_today
    broken_rate = (broken_limit / hit_limit_today * 100) if hit_limit_today > 0 else 0

    yest_limit_symbols = df_yest[df_yest['is_limit']].index
    df_boards = df_today[df_today.index.isin(yest_limit_symbols)]
    board2_count = (df_boards['board_height'] == 2).sum()
    board3_count = (df_boards['board_height'] == 3).sum()
    board4_count = (df_boards['board_height'] == 4).sum()
    board5_plus = (df_boards['board_height'] >= 5).sum()
    highest_board = df_today['board_height'].max() if limit_up_today > 0 else 0

    yest_b1 = df_yest[df_yest['board_height'] == 1].index
    yest_b2 = df_yest[df_yest['board_height'] == 2].index
    yest_b3 = df_yest[df_yest['board_height'] == 3].index
    f_premium = df_today.loc[df_today.index.isin(yest_b1), 'chg_pct'].mean() if len(yest_b1) > 0 else 0
    s_premium = df_today.loc[df_today.index.isin(yest_b2), 'chg_pct'].mean() if len(yest_b2) > 0 else 0
    t_premium = df_today.loc[df_today.index.isin(yest_b3), 'chg_pct'].mean() if len(yest_b3) > 0 else 0
    all_limit_p = df_today.loc[df_today.index.isin(yest_limit_symbols), 'chg_pct'].mean() if len(yest_limit_symbols) > 0 else 0

    total_turnover = df_today['amount'].sum() / 1e8
    prev_turnover = df_yest['amount'].sum() / 1e8
    turnover_change = (total_turnover / prev_turnover - 1) * 100

    # --- 5. 情绪评分系统 ---
    score = 0
    up_ratio = adv_count / len(df_today) * 100
    if up_ratio < 20: score -= 10 
    else: score += min(up_ratio / 10, 10)
    
    score += min(limit_up_today / 8, 10)
    limit_down_count = (df_today['chg_pct'] <= -9.8).sum()
    if limit_down_count > 30: score -= 20 
    elif limit_down_count > 10: score += 0
    else: score += 10
    
    score += max(15 * (1 - broken_rate/100), 0)
    score += min(max(all_limit_p * 5, 0), 20)
    score += min(highest_board * 1.5, 10)
    score += min(total_turnover / 1000, 10)
    
    today_top5 = get_top_sectors(today)
    yest_top5 = get_top_sectors(yesterday)
    overlap = len(set(today_top5) & set(yest_top5)) if today_top5 and yest_top5 else 0
    score += (overlap * 5)

    market_score = int(max(0, min(score, 100)))
    
    if market_score <= 30: stage, level, advice = "冰点", 0, "跌停潮，空仓休息"
    elif market_score <= 45: stage, level, advice = "退潮", 1, "风险高，轻仓试错"
    elif market_score <= 60: stage, level, advice = "修复", 1, "局部回归，关注龙头"
    elif market_score <= 80: stage, level, advice = "发酵", 2, "主线清晰，持股待涨"
    else: stage, level, advice = "高潮", 3, "情绪火热，防分歧"

    # --- 6. 数据库存入 ---
    # 定义安全转换函数防止 numpy 类型报错
    def s_f(v): return float(v) if not pd.isna(v) else 0.0

    with engine_review.begin() as conn:
        # SQL 语句中新增 flat 字段
        sql = text("""
            INSERT INTO market_breadths (
                trade_date, total_stocks, advancers, decliners, flat, up_ratio,
                limit_up, limit_down, broken_limit, broken_rate,
                yesterday_limit_up, limit_up_premium, 
                first_board_premium, second_board_premium, third_board_premium,
                highest_board, board2_count, board3_count, board4_count, board5_count,
                total_turnover, turnover_change, market_score, emotion_stage, 
                trading_level, trading_advice, created_at
            ) VALUES (
                :d, :ts, :adv, :dec, :flat, :ur, :lu, :ld, :bl, :br, :ylu, :lup, :fbp, :sbp, :tbp, :hb, :b2, :b3, :b4, :b5, :tt, :tc, :ms, :es, :tl, :ta, :now
            ) ON DUPLICATE KEY UPDATE 
                total_stocks=VALUES(total_stocks), advancers=VALUES(advancers), decliners=VALUES(decliners),
                flat=VALUES(flat), up_ratio=VALUES(up_ratio), limit_up=VALUES(limit_up), limit_down=VALUES(limit_down),
                broken_limit=VALUES(broken_limit), broken_rate=VALUES(broken_rate),
                yesterday_limit_up=VALUES(yesterday_limit_up), limit_up_premium=VALUES(limit_up_premium),
                first_board_premium=VALUES(first_board_premium), second_board_premium=VALUES(second_board_premium),
                third_board_premium=VALUES(third_board_premium), highest_board=VALUES(highest_board),
                board2_count=VALUES(board2_count), board3_count=VALUES(board3_count),
                board4_count=VALUES(board4_count), board5_count=VALUES(board5_count),
                total_turnover=VALUES(total_turnover), turnover_change=VALUES(turnover_change),
                market_score=VALUES(market_score), emotion_stage=VALUES(emotion_stage),
                trading_level=VALUES(trading_level), trading_advice=VALUES(trading_advice), created_at=VALUES(created_at)
        """)
        
        conn.execute(sql, {
            "d": today, "ts": int(len(df_today)), "adv": adv_count, "dec": dec_count, 
            "flat": flat_count,  # 🌟 这里传入计算好的 flat 数量
            "ur": s_f(up_ratio), "lu": int(limit_up_today), "ld": int(limit_down_count),
            "bl": int(broken_limit), "br": s_f(broken_rate), "ylu": int(len(yest_limit_symbols)),
            "lup": s_f(all_limit_p), "fbp": s_f(f_premium), "sbp": s_f(s_premium), "tbp": s_f(t_premium),
            "hb": int(highest_board), "b2": int(board2_count), "b3": int(board3_count),
            "b4": int(board4_count), "b5": int(board5_plus), "tt": s_f(total_turnover),
            "tc": s_f(turnover_change), "ms": int(market_score), "es": stage, "tl": int(level), "ta": advice,
            "now": datetime.datetime.now()
        })
    print(f"✅ 处理完成: {today} | 涨跌平: {adv_count}/{dec_count}/{flat_count} | 评分: {market_score}")

if __name__ == "__main__":
    analyze_market_sentiment()