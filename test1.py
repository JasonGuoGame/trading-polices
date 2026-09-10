import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# 1. 数据库配置
DB_URI = "mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db?charset=utf8mb4"
engine = create_engine(DB_URI)

def get_stocks_above_vwap(
    sector_input: str, 
    trade_date: str, 
    threshold: float = 0.90,
    volume_in_lots: bool = True  # volume 是否为“手”（1手=100股）。如果是手，设为 True
) -> pd.DataFrame:
    """
    筛选指定板块中全天 90% 以上时间处于分时均价线之上的股票
    """
    start_time = f"{trade_date} 00:00:00"
    end_time = f"{trade_date} 23:59:59"
    sector_param = f"%{sector_input}%"

    sql = """
    SELECT 
        k.symbol,
        k.trade_time,
        k.close,
        k.volume,
        k.amount,
        SUM(k.amount) OVER(PARTITION BY k.symbol ORDER BY k.trade_time) as cum_amount,
        SUM(k.volume) OVER(PARTITION BY k.symbol ORDER BY k.trade_time) as cum_volume
    FROM stk_min_kline k
    INNER JOIN (
        SELECT DISTINCT symbol 
        FROM stock_sector_relation r
        WHERE r.sector_name LIKE %s
    ) target_stocks ON k.symbol = target_stocks.symbol
    WHERE k.trade_time >= %s AND k.trade_time <= %s;
    """

    print(f"[*] 正在拉取 ['{sector_input}'] 在 {trade_date} 的数据...")

    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params=(sector_param, start_time, end_time))

    if df.empty:
        print("[X] 未能查到相关数据，请检查日期或板块名称。")
        return pd.DataFrame()

    # 单位换算：如果 volume 是“手”，乘以 100 换算为“股”
    if volume_in_lots:
        df['cum_volume_shares'] = df['cum_volume'] * 100
    else:
        df['cum_volume_shares'] = df['cum_volume']

    # 计算正确的分时均价 (分时均价 = 累计成交额(元) / 累计成交股数(股))
    df['vwap'] = np.where(df['cum_volume_shares'] > 0, df['cum_amount'] / df['cum_volume_shares'], 0)

    # 打印前 3 行数据进行单价与 VWAP 校验
    sample = df[['symbol', 'trade_time', 'close', 'vwap']].head(3)
    print("\n🔍 数据校验样本 (收盘价 vs 均价):")
    print(sample.to_string(index=False))
    print("-" * 50)

    # 判断收盘价是否大于等于均价
    df['is_above_vwap'] = (df['close'] >= df['vwap']).astype(int)

    # 汇总统计
    stats = df.groupby('symbol').agg(
        total_bars=('trade_time', 'count'),
        above_bars=('is_above_vwap', 'sum')
    ).reset_index()

    stats['above_ratio'] = (stats['above_bars'] / stats['total_bars']).round(4)
    stats['above_ratio_pct'] = (stats['above_ratio'] * 100).round(2).astype(str) + '%'

    # 过滤出符合条件的股票 (占比 >= 90% 且有效 K 线 >= 100 根)
    result = stats[(stats['above_ratio'] >= threshold) & (stats['total_bars'] >= 100)].copy()
    result = result.sort_values(by='above_ratio', ascending=False)

    print(f"\n[🎯] 筛选完成！共有 {len(result)} 只股票满足 {threshold*100}% 时间在均价线上方。")
    return result[['symbol', 'above_ratio_pct', 'above_bars', 'total_bars']]


if __name__ == '__main__':
    # 尝试运行修复后的代码（如果你的 volume 单位是“手”，保持 volume_in_lots=True）
    df_res = get_stocks_above_vwap(
        sector_input='工业金属', 
        trade_date='2026-09-08', 
        threshold=0.90,
        volume_in_lots=True
    )
    
    if not df_res.empty:
        print("\n满足条件的股票列表:")
        print(df_res.head(20).to_string(index=False))