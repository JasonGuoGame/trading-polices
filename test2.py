import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# 1. 数据库配置
DB_URI = "mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db?charset=utf8mb4"
engine = create_engine(DB_URI)

def get_stocks_in_vwap_band(
    sector_input: str, 
    trade_date: str, 
    band_pct: float = 1.0,      # 均线 ±1.0% 区间
    threshold: float = 0.90,     # 90% 时间在区间内
    max_pct_chg: float = 3.0,    # 全天最大允许涨幅 (%)
    red_only: bool = True,       # 收盘必须红盘 (today_close > base_price)
    exact_match: bool = False
) -> pd.DataFrame:

    start_time = f"{trade_date} 00:00:00"
    end_time = f"{trade_date} 23:59:59"

    if exact_match:
        sector_condition = "r.sector_name = %s"
        sector_param = sector_input
    else:
        sector_condition = "r.sector_name LIKE %s"
        sector_param = f"%{sector_input}%"

    # 🛠【修正点】：使用 d.open（今开盘价）作为基准参考价，避免使用不存在的 d.pre_close
    sql = f"""
    SELECT 
        k.symbol,
        k.trade_time,
        k.close,
        k.volume,
        k.amount,
        d.open AS daily_open,
        SUM(k.amount) OVER(PARTITION BY k.symbol ORDER BY k.trade_time) as cum_amount,
        SUM(k.volume) OVER(PARTITION BY k.symbol ORDER BY k.trade_time) as cum_volume
    FROM stk_min_kline k
    INNER JOIN (
        SELECT DISTINCT symbol 
        FROM stock_sector_relation r
        WHERE {sector_condition}
    ) target_stocks ON k.symbol = target_stocks.symbol
    LEFT JOIN stk_daily_kline d 
        ON k.symbol = d.symbol AND d.trade_date = %s
    WHERE k.trade_time >= %s AND k.trade_time <= %s;
    """

    print(f"[*] 正在拉取板块 ['{sector_input}'] 日期 {trade_date} 的数据...")

    try:
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params=(sector_param, trade_date, start_time, end_time))

        if df.empty:
            print(f"[!] 未找到匹配板块 ['{sector_input}'] 的数据。")
            return pd.DataFrame()

        # 1. 强制转为数值类型
        for col in ['close', 'amount', 'volume', 'daily_open', 'cum_amount', 'cum_volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 2. 计算真实分时 VWAP (volume 单位为手，乘以 100)
        df['vwap'] = np.where(
            df['cum_volume'] > 0, 
            df['cum_amount'] / (df['cum_volume'] * 100.0), 
            0
        )

        # 3. 判定收盘价是否处于均线 ±band_pct% 范围内
        lower_bound = 1.0 - (band_pct / 100.0)   # 0.99
        upper_bound = 1.0 + (band_pct / 100.0)   # 1.01

        df['is_inside_band'] = (
            (df['close'] >= df['vwap'] * lower_bound) & 
            (df['close'] <= df['vwap'] * upper_bound)
        ).astype(int)

        # 4. 按股票维度汇总统计
        stats = df.groupby('symbol').agg(
            total_bars=('trade_time', 'count'),
            inside_bars=('is_inside_band', 'sum'),
            today_close=('close', 'last'),         # 今日最后一条K线的收盘价
            first_min_open=('close', 'first'),     # 今日第一条分钟线的价格（作为昨收备用参考）
            daily_open=('daily_open', 'first')     # 来自日线表的开盘价
        ).reset_index()

        # 基准价优先取日线 daily_open；若日线缺漏则退而取第一根分钟线价格
        stats['base_price'] = np.where(stats['daily_open'] > 0, stats['daily_open'], stats['first_min_open'])

        stats['inside_ratio'] = (stats['inside_bars'] / stats['total_bars']).round(4)
        stats['inside_ratio_pct'] = (stats['inside_ratio'] * 100).round(2).astype(str) + '%'
        
        # 计算今日涨跌幅 (%)
        stats['pct_chg'] = (((stats['today_close'] - stats['base_price']) / stats['base_price']) * 100).round(2)

        # 5. 过滤条件组拼：
        #    a) 在均线 ±1% 内的时间占比 >= 90% (threshold)
        #    b) 全天涨幅 <= 3% (max_pct_chg)
        #    c) 收盘红盘 (today_close > base_price)
        #    d) K线总数 >= 100 根
        cond_ratio = stats['inside_ratio'] >= threshold
        cond_max_pct = stats['pct_chg'] <= max_pct_chg
        cond_red = (stats['today_close'] > stats['base_price']) if red_only else True
        cond_bars = stats['total_bars'] >= 100

        result = stats[cond_ratio & cond_max_pct & cond_red & cond_bars].copy()
        result = result.sort_values(by=['inside_ratio', 'pct_chg'], ascending=[False, True])

        print(f"[✔] 计算完成！板块共 {len(stats)} 只股票，匹配到符合条件股票: {len(result)} 只。")

        # 打印真实占比 TOP 5 股票 preview
        print("\n--- 真实占比 TOP 5 股票验证 ---")
        top_5 = stats.sort_values(by='inside_ratio', ascending=False).head(5)
        print(top_5[['symbol', 'inside_ratio_pct', 'pct_chg', 'today_close', 'base_price']].to_string(index=False))
        print("--------------------------------\n")

        return result[['symbol', 'pct_chg', 'inside_ratio_pct', 'today_close', 'base_price']]

    except Exception as e:
        print(f"[X] 运行报错: {e}")
        return pd.DataFrame()


if __name__ == '__main__':
    df_res = get_stocks_in_vwap_band(
        sector_input='贵金属', 
        trade_date='2026-09-09', 
        band_pct=0.8,        # 均线 ±1.0%
        threshold=0.90,      # 90% 时间在区间内
        max_pct_chg=3.0,     # 全天涨幅 <= 3%
        red_only=True,       # 要求收盘红盘
        exact_match=False
    )
    
    if not df_res.empty:
        print("📊 最终符合条件的股票 (收盘红盘):")
        print(df_res.to_string(index=False))