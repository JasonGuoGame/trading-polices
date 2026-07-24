import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def find_sector_backbone(target_sector):
    """
    定位指定板块的“中军”股票
    逻辑：权重 = 成交额(60%) + 资金评分(30%) + 涨跌幅稳定性(10%)
    """
    print(f"🔍 正在深度扫描板块 [{target_sector}] 的核心中军...")

    # 1. 获取最新交易日
    with engine_quant.connect() as conn:
        today = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).scalar()

    # 2. SQL：关联 K线、关系表、资金流表
    # 逻辑：必须排除 ST 股，且板块名称需要模糊匹配
    query_sql = text("""
        SELECT 
            k.symbol, 
            s.name, 
            k.amount as '成交额', 
            k.close,
            (k.close / k.open - 1) * 100 as '当日涨幅',
            f.capital_score as '资金分',
            f.main_net_inflow as '主力流入'
        FROM stk_daily_kline k
        JOIN stock_sector_relation r ON k.symbol = r.symbol
        JOIN stocks s ON k.symbol = s.symbol
        LEFT JOIN stk_stock_fund_flow f ON k.symbol = f.symbol AND f.trade_date = k.trade_date
        WHERE k.trade_date = :t
          AND (r.sector_name = :sec OR r.sector_name = CONCAT('行业-', :sec) OR r.sector_name = CONCAT('概念-', :sec))
          AND s.name NOT LIKE '%%ST%%'
          AND s.name NOT LIKE '%%退%%'
    """)

    df = pd.read_sql(query_sql, engine_quant, params={"t": today, "sec": target_sector})

    if df.empty:
        print(f"❌ 未能找到板块 [{target_sector}] 的个股数据。")
        return None

    # 🌟 核心：去重（防止一只票在多个子板块标签中出现）
    df = df.drop_duplicates(subset=['symbol'])

    # 3. 计算“中军权重分”
    # A. 成交额得分 (归一化到 0-60)
    df['amt_score'] = (df['成交额'] / df['成交额'].max()) * 60
    
    # B. 资金评分 (归一化到 0-30)
    df['cap_score'] = (df['资金分'] / 100) * 30
    
    # C. 稳定性/影响力 (当日涨幅不能太差且不能波动过激，给固定分或根据表现给 0-10)
    df['stable_score'] = df['当日涨幅'].apply(lambda x: 10 if 2 <= x <= 7 else (5 if x > 0 else 0))

    # 最终汇总
    df['backbone_score'] = df['amt_score'] + df['cap_score'] + df['stable_score']
    
    # 4. 结果排序
    df = df.sort_values('backbone_score', ascending=False).head(5)

    print("\n" + "🛡️" * 5 + f" [{target_sector}] 板块核心中军排行榜 " + "🛡️" * 5)
    print("-" * 75)
    display_cols = ['symbol', 'name', '成交额', '资金分', '当日涨幅', 'backbone_score']
    # 格式化成交额显示为亿
    df['成交额'] = (df['成交额'] / 1e8).round(2).map(lambda x: f"{x}亿")
    df['backbone_score'] = df['backbone_score'].round(2)
    
    print(df[display_cols].to_string(index=False))
    print("-" * 75)
    
    top_one = df.iloc[0]['name']
    print(f"💡 结论：[{target_sector}] 的灵魂中军是 【{top_one}】。")
    print(f"提示：中军不倒，板块不止。如果 {top_one} 开始放量下杀，整个板块大概率进入退潮期。")

    return df

if __name__ == "__main__":
    # 示例：查找半导体或证券的中军
    find_sector_backbone("国资云")
    # find_sector_backbone("半导体")