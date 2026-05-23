import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def calculate_market_emotion():
    print(f"[{datetime.datetime.now()}] 正在量化今日短线情绪...")

    with engine.connect() as conn:
        # 获取最近两个交易日
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(date_res) < 2:
            print("数据不足，无法计算。")
            return
        today, yesterday = date_res[0][0], date_res[1][0]

        # 2. 提取今日和昨日全市场行情
        # 只要主板 (60/00开头)，因为连板生态主要在主板
        query_sql = text("""
            SELECT symbol, trade_date, open, high, low, close,
                   (SELECT close FROM stk_daily_kline WHERE symbol = k.symbol AND trade_date < k.trade_date ORDER BY trade_date DESC LIMIT 1) as prev_close
            FROM stk_daily_kline k
            WHERE trade_date IN (:t, :y)
              AND (symbol LIKE '60%' OR symbol LIKE '00%')
        """)
        df_all = pd.read_sql(query_sql, conn, params={"t": today, "y": yesterday})

    # 分离今日和昨日数据
    df_today = df_all[df_all['trade_date'] == today].copy()
    df_yest = df_all[df_all['trade_date'] == yesterday].copy()

    # --- 逻辑 A: 判定涨跌停标准 ---
    # 主板涨停价格计算
    df_today['limit_up_p'] = (df_today['prev_close'] * 1.098).astype(float).round(2)
    df_today['limit_down_p'] = (df_today['prev_close'] * 0.902).astype(float).round(2)
    
    # 涨停家数 (今日收盘 = 涨停价)
    lu_stocks = df_today[df_today['close'] >= df_today['limit_up_p']]
    limit_up_count = len(lu_stocks)
    
    # 跌停家数 (今日收盘 = 跌停价)
    limit_down_count = len(df_today[df_today['close'] <= df_today['limit_down_p']])

    # --- 逻辑 B: 炸板率计算 ---
    # 炸板定义：最高触及涨停，但收盘没封住
    touch_lu_count = len(df_today[df_today['high'] >= df_today['limit_up_p']])
    broken_board_rate = 0
    if touch_lu_count > 0:
        broken_board_rate = (touch_lu_count - limit_up_count) / touch_lu_count * 100

    # --- 逻辑 C: 晋级率与昨日涨停表现 ---
    # 1. 找出昨日收盘涨停的股票名单
    df_yest['limit_up_p_y'] = (df_yest['prev_close'] * 1.098).astype(float).round(2)
    yest_lu_list = df_yest[df_yest['close'] >= df_yest['limit_up_p_y']]['symbol'].tolist()
    
    promotion_rate = 0
    yest_limit_avg_return = 0
    
    if yest_lu_list:
        # 2. 统计这些股票在今天的表现
        df_lu_performance = df_today[df_today['symbol'].isin(yest_lu_list)]
        
        # 今日收益 = (今日收盘 - 昨日收盘) / 昨日收盘
        yest_limit_avg_return = (df_lu_performance['close'] / df_lu_performance['prev_close'] - 1).mean() * 100
        
        # 晋级率 = 今日继续涨停的 / 昨日涨停总数
        today_still_lu = len(df_lu_performance[df_lu_performance['close'] >= df_lu_performance['limit_up_p']])
        promotion_rate = today_still_lu / len(yest_lu_list) * 100

    # --- 逻辑 D: 计算最高连板高度 ---
    # 获取过去10天数据进行递归高度扫描
    max_board_height = 1
    if limit_up_count > 0:
        # 简单逻辑：如果晋级率>0，代表有2连板及以上
        # 这里提取当前涨停股的历史连板天数
        with engine.connect() as conn:
            streak_sql = text("""
                SELECT symbol, COUNT(*) as height
                FROM (
                    SELECT k.symbol, k.trade_date,
                        (SELECT close FROM stk_daily_kline WHERE symbol=k.symbol AND trade_date < k.trade_date ORDER BY trade_date DESC LIMIT 1) as pc
                    FROM stk_daily_kline k
                    WHERE symbol IN :lu_list AND trade_date <= :today
                    ORDER BY trade_date DESC LIMIT 500
                ) t
                WHERE close >= ROUND(pc * 1.098, 2)
                GROUP BY symbol
            """)
            # 注意：实际连板高度需要连续判断，此处为简化示例。建议复用之前的连板高度脚本。
            max_board_height = 2 if promotion_rate > 0 else 1 # 这是一个简化的占位符，若需精确高度请调用之前的 Streak 逻辑

    # --- 3. 结果汇总与存入数据库 ---
    metrics = {
        'trade_date': today,
        'limit_up_count': int(limit_up_count),
        'limit_down_count': int(limit_down_count),
        'max_board_height': int(max_board_height),
        'promotion_rate': float(round(promotion_rate, 2)),
        'broken_board_rate': float(round(broken_board_rate, 2)),
        'yesterday_limit_avg_return': float(round(yest_limit_avg_return, 2))
    }

    # 执行写入
    df_save = pd.DataFrame([metrics])
    try:
        with engine.begin() as conn:
            df_save.to_sql('temp_emotion', con=conn, if_exists='replace', index=False)
            upsert_sql = text("""
                INSERT INTO market_emotion_metrics (trade_date, limit_up_count, limit_down_count, max_board_height, promotion_rate, broken_board_rate, yesterday_limit_avg_return)
                SELECT * FROM temp_emotion
                ON DUPLICATE KEY UPDATE 
                    limit_up_count = VALUES(limit_up_count),
                    limit_down_count = VALUES(limit_down_count),
                    max_board_height = VALUES(max_board_height),
                    promotion_rate = VALUES(promotion_rate),
                    broken_board_rate = VALUES(broken_board_rate),
                    yesterday_limit_avg_return = VALUES(yesterday_limit_avg_return);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE temp_emotion;"))
        
        # --- 终端输出 ---
        print("\n" + "📊" * 10 + f" 今日短线情绪看板 ({today}) " + "📊" * 10)
        print("-" * 75)
        print(f"🔥 涨停家数: {limit_up_count:<5} | ❄️ 跌停家数: {limit_down_count}")
        print(f"🚀 晋级率: {promotion_rate:.1f}%   | 💣 炸板率: {broken_board_rate:.1f}%")
        print(f"💰 昨日涨停今日平均收益: {yest_limit_avg_return:+.2f}%")
        print("-" * 75)
        
        # 核心回答：打板是否赚钱
        if yest_limit_avg_return > 2.0 and promotion_rate > 30:
            msg = "✅ 赚钱效应极强！接力者的盛宴，大胆参与核心龙头。"
        elif yest_limit_avg_return > 0:
            msg = "⚖️ 情绪平稳。局部轮动，建议汰弱留强。"
        else:
            msg = "❌ 亏钱效应明显！昨日涨停今天吃面，严禁接力，注意炸板风险。"
        print(f"💡 结论：{msg}\n")

    except Exception as e:
        print(f"❌ 存入数据库失败: {e}")

if __name__ == "__main__":
    calculate_market_emotion()