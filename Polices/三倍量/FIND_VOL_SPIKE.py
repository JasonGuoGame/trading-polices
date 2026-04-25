import pandas as pd
from sqlalchemy import create_engine, text
from xtquant import xtdata
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def screen_volume_spike_refined():
    print(f"[{datetime.datetime.now()}] 正在扫描【主板非ST】爆量 3 倍个股...")

    # 1. 获取最新两个交易日
    with engine.connect() as conn:
        date_query = text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")
        dates = [row[0] for row in conn.execute(date_query).fetchall()]
    
    if len(dates) < 2:
        print("错误：数据库数据不足。")
        return

    today = dates[0]
    yesterday = dates[1]
    print(f"分析日期：昨日 {yesterday} -> 今日 {today}")

    # 2. 关键修改：SQL 层面进行第一轮严格过滤
    # - 过滤代码前缀：只保留 60 (沪主) 和 00 (深主)
    # - 过滤名称：剔除包含 ST、*ST、退 的股票
    query = f"""
    SELECT k.symbol, s.name, k.trade_date, k.volume, k.close, k.amount
    FROM stk_daily_kline k
    JOIN stocks s ON k.symbol = s.symbol
    WHERE k.trade_date IN ('{today}', '{yesterday}')
      AND (k.symbol LIKE '60%%' OR k.symbol LIKE '00%%')
      AND s.name NOT LIKE '%%ST%%'
      AND s.name NOT LIKE '%%退%%'
    """
    df_raw = pd.read_sql(query, engine)

    if df_raw.empty:
        print("未发现匹配的原始行情数据。")
        return

    # 3. 数据透视
    df_pivot = df_raw.pivot(index=['symbol', 'name'], columns='trade_date', values=['volume', 'close', 'amount'])
    df_pivot = df_pivot.dropna()

    # 4. 计算指标
    df_pivot['vol_ratio'] = df_pivot['volume'][today] / df_pivot['volume'][yesterday]
    
    # 5. 设定筛选条件
    # - 爆量 3 倍以上
    # - 成交额大于 5000 万 (剔除流动性差的微盘股)
    # - 今日必须收红 (收盘价 > 昨日收盘价)
    condition = (df_pivot['vol_ratio'] >= 3.0) & \
                (df_pivot['amount'][today] > 50000000) & \
                (df_pivot['close'][today] > df_pivot['close'][yesterday])

    results = df_pivot[condition].copy()

    # 6. 精准过滤：利用 MiniQMT 实时名单二次校验 ST（防止名称更新延迟）
    try:
        st_stocks_realtime = xtdata.get_stock_list_in_sector('风险警示板')
        results = results[~results.index.get_level_values('symbol').isin(st_stocks_realtime)]
    except:
        pass # 如果 QMT 未连接，则跳过此步，以 SQL 过滤为准

    # 7. 整理输出
    if not results.empty:
        final_list = results.reset_index()
        report = pd.DataFrame({
            '代码': final_list['symbol'],
            '名称': final_list['name'],
            '放量倍数': final_list['vol_ratio'].round(2),
            '今日涨幅%': ((final_list['close'][today] - final_list['close'][yesterday]) / final_list['close'][yesterday] * 100).round(2),
            '成交额(亿)': (final_list['amount'][today] / 1e8).round(2)
        })

        report = report.sort_values('放量倍数', ascending=False)

        print("\n" + "🚀" * 20)
        print(f"🔥 主板【非ST】爆量 3 倍个股清单 (共 {len(report)} 只)")
        print("-" * 70)
        print(report.to_string(index=False))
        print("-" * 70)
        print("💡 研判：爆量通常是变盘信号，若出现在主线板块，则可能是新龙头的诞生地。")
        print("🚀" * 20 + "\n")
    else:
        print("\n今日主板市场未发现符合条件的爆量个股。")

if __name__ == "__main__":
    screen_volume_spike_refined()