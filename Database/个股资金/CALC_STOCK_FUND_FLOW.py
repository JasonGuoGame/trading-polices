import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def calculate_stock_fund_flow():
    print(f"[{datetime.datetime.now()}] 启动个股资金流向拟合模型...")

    # 1. 获取最新交易日
    with engine.connect() as conn:
        today = conn.execute(text("SELECT MAX(DATE(trade_time)) FROM stk_min_kline")).fetchone()[0]
        if not today: return
        print(f"分析目标日: {today}")

    # 2. 从分时表提取今日全市场分钟线
    # 只需要代码、时间、收盘、开盘、成交额
    query = text("""
        SELECT symbol, close, open, amount 
        FROM stk_min_kline 
        WHERE DATE(trade_time) = :t
    """)
    with engine.connect() as conn:
        df_min = pd.read_sql(query, conn, params={"t": today})

    if df_min.empty:
        print("今日分时数据尚未同步。")
        return

    # 3. 核心算法：资金强度分类
    print("正在进行量价分布拟合计算...")
    
    # 计算每一分钟的净流向：收红为正，收绿为负
    df_min['direction'] = np.sign(df_min['close'] - df_min['open'])
    df_min['net_flow'] = df_min['amount'] * df_min['direction'] / 10000.0 # 转为万元

    # 按股票分组，确定该股今日的成交强度分位数
    def classify_and_sum(group):
        # 排除不波动的分钟
        total_amt = group['amount'].sum() / 10000.0
        if total_amt == 0: return None
        
        # 计算该股票今日每分钟的成交额排名分位数
        # quantile 是量化中的精髓：用来模拟单笔交易的‘重量’
        q95 = group['amount'].quantile(0.95)
        q80 = group['amount'].quantile(0.80)
        q50 = group['amount'].quantile(0.50)

        # 分类
        super_large = group[group['amount'] >= q95]['net_flow'].sum()
        large = group[(group['amount'] < q95) & (group['amount'] >= q80)]['net_flow'].sum()
        medium = group[(group['amount'] < q80) & (group['amount'] >= q50)]['net_flow'].sum()
        small = group[group['amount'] < q50]['net_flow'].sum()

        main_inflow = super_large + large
        
        return pd.Series({
            'main_net_inflow': main_inflow,
            'super_large_net_inflow': super_large,
            'large_net_inflow': large,
            'medium_net_inflow': medium,
            'small_net_inflow': small,
            'main_net_ratio': (main_inflow / total_amt * 100) if total_amt > 0 else 0,
            'total_amount': total_amt
        })

    # 执行聚合计算
    df_daily = df_min.groupby('symbol').apply(classify_and_sum).dropna().reset_index()

    # 4. 计算多日累计与排名 (3d, 5d, 10d)
    # 这部分需要关联历史上的 stk_stock_fund_flow 表
    print("正在计算历史多日累计流入与评分...")
    
    # 临时计算评分：以主力流入占比和绝对金额综合打分
    df_daily['capital_score'] = (
        df_daily['main_net_ratio'].rank(pct=True) * 50 + 
        df_daily['main_net_inflow'].rank(pct=True) * 50
    ).astype(int)
    
    df_daily['rank_market'] = df_daily['main_net_inflow'].rank(ascending=False, method='min').astype(int)
    df_daily['trade_date'] = today

    # 5. 关联股票名称
    with engine.connect() as conn:
        df_names = pd.read_sql("SELECT symbol, name as stock_name FROM stocks", conn)
    df_final = pd.merge(df_daily, df_names, on='symbol', how='left')

    # 6. 写入数据库 (UPSERT)
    if not df_final.empty:
        print(f"准备入库 {len(df_final)} 条个股资金流数据...")
        with engine.begin() as conn:
            df_final.to_sql('temp_stock_flow', con=conn, if_exists='replace', index=False)
            upsert_sql = text("""
                INSERT INTO stk_stock_fund_flow (
                    trade_date, symbol, stock_name, main_net_inflow, super_large_net_inflow, 
                    large_net_inflow, medium_net_inflow, small_net_inflow, main_net_ratio, 
                    rank_market, capital_score
                )
                SELECT trade_date, symbol, stock_name, main_net_inflow, super_large_net_inflow, 
                       large_net_inflow, medium_net_inflow, small_net_inflow, main_net_ratio, 
                       rank_market, capital_score FROM temp_stock_flow
                ON DUPLICATE KEY UPDATE 
                    main_net_inflow = VALUES(main_net_inflow),
                    main_net_ratio = VALUES(main_net_ratio),
                    capital_score = VALUES(capital_score),
                    rank_market = VALUES(rank_market);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_stock_flow;"))
        print("✅ 个股资金流向计算同步完成。")

if __name__ == "__main__":
    calculate_stock_fund_flow()