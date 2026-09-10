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
    max_pct_chg: float = 3.0,   # 新增：最大涨幅限制 (%)
    exact_match: bool = False
) -> pd.DataFrame:
    """
    筛选指定板块中，全天指定比例以上时间位于均价线上，且全天涨幅不超过 max_pct_chg 的股票。

    :param sector_input: 板块名称或关键词 (例如: '半导体')
    :param trade_date: 交易日期 (格式 'YYYY-MM-DD')
    :param threshold: 处于均价线之上的时间占比阈值，默认 0.90 (90%)
    :param max_pct_chg: 全天最大允许涨幅 (%)，默认 3.0 (不超过 3%)
    :param exact_match: 是否精确匹配板块名称
    :return: 包含符合条件股票信息的 DataFrame
    """
    start_time = f"{trade_date} 00:00:00"
    end_time = f"{trade_date} 23:59:59"

    if exact_match:
        sector_condition = "r.sector_name = %s"
        sector_param = sector_input
    else:
        sector_condition = "r.sector_name LIKE %s"
        sector_param = f"%{sector_input}%"

    # SQL 升级：
    # 关联 stk_daily_kline 获取 trade_date 当天的开盘价/收盘价与 turnover_rate 等信息
    sql = f"""
    SELECT 
        k.symbol,
        k.trade_time,
        k.close,
        k.volume,
        k.amount,
        d.close AS daily_close,
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

    print(f"[*] 正在查询板块 ['{sector_input}'] 在 {trade_date} 的数据 (限制涨幅 <= {max_pct_chg}%)...")

    try:
        with engine.connect() as conn:
            # 传入参数: 板块关键词, 交易日期(用于日线关联), 开始时间, 结束时间
            df = pd.read_sql(sql, conn, params=(sector_param, trade_date, start_time, end_time))

        if df.empty:
            print(f"[!] 未找到匹配板块 ['{sector_input}'] 或当天没有 K 线数据。")
            return pd.DataFrame()

        # 2. 计算分时均价 (分时均价 = 累计成交额 / 累计成交股数)
        df['vwap'] = np.where(df['cum_volume'] > 0, df['cum_amount'] / df['cum_volume'], 0)

        # 3. 判断收盘价是否大于等于当前分时均价
        df['is_above_vwap'] = (df['close'] >= df['vwap']).astype(int)

        # 4. 按股票维度统计
        # 获取每只股票全天的最新收盘价（即最后一条分钟线）和日线开盘价
        stats = df.groupby('symbol').agg(
            total_bars=('trade_time', 'count'),
            above_bars=('is_above_vwap', 'sum'),
            last_close=('close', 'last'),       # 全天最终收盘价
            daily_open=('daily_open', 'first')   # 日线开盘价
        ).reset_index()

        # 5. 计算均线上占比
        stats['above_ratio'] = round(stats['above_bars'] / stats['total_bars'], 4)
        stats['above_ratio_pct'] = (stats['above_ratio'] * 100).round(2).astype(str) + '%'

        # 6. 计算全天涨跌幅 (%) -> 这里以相对于开盘价的涨幅为例；若有昨收盘价，可替换为 (last_close - prev_close)/prev_close
        # 此处展示相较于今日开盘价的涨跌幅:
        stats['pct_chg'] = round(((stats['last_close'] - stats['daily_open']) / stats['daily_open']) * 100, 2)

        # 7. 过滤条件组拼：
        #    a) 均线上时间占比 >= 90% (threshold)
        #    b) 全天涨幅 <= 3% (max_pct_chg)
        #    c) 总分钟 K 线数 >= 100 根 (过滤停牌/异常数据)
        cond_ratio = stats['above_ratio'] >= threshold
        cond_pct = stats['pct_chg'] <= max_pct_chg
        cond_bars = stats['total_bars'] >= 100

        result = stats[cond_ratio & cond_pct & cond_bars].copy()
        result = result.sort_values(by=['above_ratio', 'pct_chg'], ascending=[False, True])

        print(f"[✔] 筛选完成，共匹配到 {len(result)} 只符合条件的股票。")
        return result[['symbol', 'pct_chg', 'above_ratio_pct', 'last_close', 'daily_open']]

    except Exception as e:
        print(f"[X] 数据库查询或计算报错: {e}")
        return pd.DataFrame()


if __name__ == '__main__':
    TARGET_DATE = '2026-09-08'     
    
    # 筛选：全天 90% 以上时间在均线上 + 全天涨幅 <= 3.0%
    df_res = get_stocks_above_vwap(
        sector_input='工业金属', 
        trade_date=TARGET_DATE, 
        threshold=0.90,      # 90% 均线上
        max_pct_chg=3.0,     # 涨幅不超过 3%
        exact_match=False
    )
    
    if not df_res.empty:
        print("\n" + "="*60)
        print("📊 符合条件的股票列表 (全天 90% 时间在均线上 & 涨幅 <= 3%):")
        print("="*60)
        print(df_res.to_string(index=False))