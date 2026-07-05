import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_new_face_sectors_strict():
    """
    修正后的逻辑：
    1. 今日排名 <= 10
    2. 过去 5 天（第2到第6个交易日），【每天】的排名都必须 > 30 (即过去5天里的最高排名 MIN() > 30)
    """
    print(f"[{datetime.datetime.now()}] 🔎 正在扫描‘绝对新面孔’黑马板块 (过去5天每日均>30名)...")
    
    # 构建严格的 SQL
    def build_strict_query(table_name, extra_cond=""):
        return f"""
        SELECT sector_name FROM (
            SELECT 
                sector_name,
                -- 今天 (date_idx = 1) 的排名
                MAX(CASE WHEN date_idx = 1 THEN rank_pos END) as today_rank,
                -- 过去5天 (date_idx 2~6) 的【最好排名】(数值最小值)
                MIN(CASE WHEN date_idx BETWEEN 2 AND 6 THEN rank_pos END) as best_past_rank
            FROM (
                SELECT sector_name, rank_pos, trade_date,
                       DENSE_RANK() OVER (ORDER BY trade_date DESC) as date_idx
                FROM {table_name}
                WHERE 1=1 {extra_cond}
            ) t WHERE date_idx <= 6
            GROUP BY sector_name
        ) final 
        WHERE today_rank <= 10 
          -- 🌟 核心修正：过去5天的【最高/最好排名】依然大于30 (或者过去5天完全没上过榜 NULL)
          -- 这确保了过去 5 天的【每一天】排名都严格在 30 名开外！
          AND (best_past_rank > 30 OR best_past_rank IS NULL)
        """

    # 1. 扫描评分表
    query_scores = build_strict_query("trading_review.stk_sector_scores")
    # 2. 扫描宽度表
    query_breadth = build_strict_query("trading_review.stk_sector_breadths", "AND sector_type = 'industry'")

    with engine_review.connect() as conn:
        df1 = pd.read_sql(text(query_scores), conn)
        df2 = pd.read_sql(text(query_breadth), conn)

    # 合并两个表发现的板块并去重
    new_face_list = list(set(df1['sector_name'].tolist() + df2['sector_name'].tolist()))
    return new_face_list

def find_sector_backbone(target_sector, today_str):
    """
    定位指定板块的“中军”股票
    """
    query_sql = text("""
        SELECT 
            k.symbol, s.name, k.amount as '成交额', k.close,
            (k.close / k.open - 1) * 100 as '当日涨幅',
            f.capital_score as '资金分'
        FROM stk_daily_kline k
        JOIN stock_sector_relation r ON k.symbol = r.symbol
        JOIN stocks s ON k.symbol = s.symbol
        LEFT JOIN stk_stock_fund_flow f ON k.symbol = f.symbol AND f.trade_date = k.trade_date
        WHERE k.trade_date = :t
          AND (r.sector_name = :sec OR r.sector_name = CONCAT('行业-', :sec) OR r.sector_name = CONCAT('概念-', :sec))
          AND s.name NOT LIKE '%%ST%%' AND s.name NOT LIKE '%%退%%'
    """)

    df = pd.read_sql(query_sql, engine_quant, params={"t": today_str, "sec": target_sector})
    if df.empty: return None

    df = df.drop_duplicates(subset=['symbol'])

    # 权重模型：成交额(60%) + 资金分(30%) + 涨幅稳定性(10%)
    df['amt_score'] = (df['成交额'] / df['成交额'].max()) * 60
    df['cap_score'] = (df['资金分'].fillna(0) / 100) * 30
    df['stable_score'] = df['当日涨幅'].apply(lambda x: 10 if 2 <= x <= 7 else (5 if x > 0 else 0))
    df['backbone_score'] = df['amt_score'] + df['cap_score'] + df['stable_score']
    
    return df.sort_values('backbone_score', ascending=False).head(3)

def run_strategy_pipeline():
    # 1. 抓取严格的黑马板块
    sectors = get_new_face_sectors_strict()
    
    if not sectors:
        print("🕒 今日暂未发现符合‘过去5天严格位于30名外，今日冲进前10’的冰点黑马板块。")
        return

    print(f"🔥 发现 {len(sectors)} 个‘冰点突围’黑马板块: {', '.join(sectors)}")

    # 2. 获取最新日期
    with engine_quant.connect() as conn:
        today = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).scalar()

    # 3. 循环板块找中军
    for sec in sectors:
        print(f"\n--- 正在分析【绝对新面孔】板块: {sec} ---")
        top_stocks = find_sector_backbone(sec, today)
        
        if top_stocks is not None:
            top_stocks['所属板块'] = sec
            display = top_stocks[['所属板块', 'name', '成交额', '资金分', '当日涨幅', 'backbone_score']]
            display['成交额'] = (display['成交额'] / 1e8).round(2).map(lambda x: f"{x}亿")
            print(display.to_string(index=False))
        else:
            print(f"⚠️ 未能找到该板块的有效个股。")

if __name__ == "__main__":
    run_strategy_pipeline()