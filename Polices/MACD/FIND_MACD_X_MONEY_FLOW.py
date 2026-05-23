import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

# --- 2. 板块黑名单 ---
SECTOR_BLACKLIST = [
    "融资融券", "沪股通", "深股通", "MSCI", "标准普尔", "富时罗素", "央国企改革", "中证", "上证", 
    "昨日", "小盘", "大盘", "权重", "两融", "证金", "汇金", "基金重仓", "预盈预增", "标普", 
    "深证", "创业板", "科创板", "活跃", "高振幅", "昨日涨停", "转债", "破净", "机构重仓", 
    "股权转让", "中盘股", "深成500", "最近多板", "东方财富", "年报预增", "电子", "HS300", 
    "百元股", "中盘成长", "近期新高", "创业成份", "百日新高", "2025年报", "一带一路", "AH股", 
    "专精特新", "历史新高", "次新股", "QFII重仓", "中盘价值", "价值股", "中字头", "股权激励", 
    "2026—季报预减", "中特估","中俄贸易概念","长江三角","2026—季报预增","参股银行","央视50_"
]

def clean_and_pick_two_sectors(sector_str):
    """从全量板块中过滤黑名单并挑选前两个核心板块"""
    if not sector_str: return "其他"
    raw_list = sector_str.split(',')
    filtered = []
    for s in raw_list:
        # 排除包含黑名单关键词的板块
        if not any(noise in s for noise in SECTOR_BLACKLIST):
            # 去除前缀
            clean_s = s.replace('行业-', '').replace('概念-', '')
            filtered.append(clean_s)
    
    # 返回前两个，若只有一个则返回一个，没有则返回其他
    if len(filtered) >= 2:
        return f"{filtered[0]} / {filtered[1]}"
    elif len(filtered) == 1:
        return filtered[0]
    return "其他题材"

def calculate_macd_res_score(row):
    """评分逻辑：板块流入(40) + 量能(30) + MACD红柱(30)"""
    s1 = np.clip((row['板块流入(亿)'] - 2) * 5 + 10, 10, 40)
    s2 = np.clip((row['量能倍数'] - 1) * 15, 0, 30)
    s3 = np.clip(row['HIST'] * 100, 5, 30)
    return int(s1 + s2 + s3)

def save_to_review_pool(df, trade_date):
    """将结果存入 trading_review.stock_pools"""
    if df.empty: return
    now = datetime.datetime.now()
    records = []
    for _, row in df.iterrows():
        tags_dict = {
            "strategy": "MACD_Hot_Sector",
            "hist": round(float(row['HIST']), 4),
            "vol_ratio": round(float(row['量能倍数']), 2),
            "sector_inflow": round(float(row['板块流入(亿)']), 2)
        }
        records.append({
            'symbol': row['代码'],
            'trade_date': trade_date,
            'stock_name': row['名称'],
            'pool_type': 'short',
            'sector_name': row['所属板块'], # 存储脱水后的双板块
            'score': int(row['综合评分']),
            'status': '资金共振金叉',
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"信号日主力净流入{row['板块流入(亿)']}亿，MACD水上金叉。",
            'created_at': now,
            'updated_at': now
        })
    df_save = pd.DataFrame(records)
    with engine_review.begin() as conn:
        df_save.to_sql('temp_macd_pool', con=conn, if_exists='replace', index=False)
        upsert_sql = text("""
            INSERT INTO stock_pools (symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at)
            SELECT symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at FROM temp_macd_pool
            ON DUPLICATE KEY UPDATE 
                score = VALUES(score), 
                sector_name = VALUES(sector_name),
                tags = VALUES(tags), 
                updated_at = VALUES(updated_at);
        """)
        conn.execute(upsert_sql)
        conn.execute(text("DROP TABLE IF EXISTS temp_macd_pool;"))

def run_macd_resonance_pipeline():
    print(f"[{datetime.datetime.now()}] 启动带【双板块脱水】功能的 MACD 金叉探测系统...")

    # 1. 获取日期
    with engine_quant.connect() as conn:
        date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_factors")).fetchone()
        today = date_res[0]
        yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_factors WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
        yesterday = yest_res[0]

    # 2. 核心 SQL：联表并获取 GROUP_CONCAT 的全量板块用于 Python 清洗
    query_sql = text("""
        SELECT 
            t.symbol as '代码', 
            s.name as '名称', 
            flow.sector_name as 'main_sector',
            flow.net_inflow_amount as '板块流入(亿)',
            t.f_macd_dif as DIF, 
            t.f_macd_hist as HIST,
            f.f_vol_ratio as '量能倍数',
            (SELECT GROUP_CONCAT(sector_name) FROM stock_sector_relation WHERE symbol = t.symbol) as all_sectors
        FROM stk_factors t
        JOIN stk_factors y ON t.symbol = y.symbol AND y.trade_date = :yesterday
        JOIN stocks s ON t.symbol = s.symbol
        JOIN stock_sector_relation r ON t.symbol = r.symbol
        JOIN stk_sector_fund_flow flow ON (r.sector_name = CONCAT('行业-', flow.sector_name) OR r.sector_name = CONCAT('概念-', flow.sector_name))
        LEFT JOIN stk_factors f ON t.symbol = f.symbol AND f.trade_date = t.trade_date
        WHERE t.trade_date = :today AND flow.trade_date = :today
          AND flow.net_inflow_amount > 2.0                    
          AND (t.symbol LIKE '60%' OR t.symbol LIKE '00%' OR t.symbol LIKE '30%')
          AND s.name NOT LIKE '%%ST%%'
          AND t.f_macd_dif > t.f_macd_dea                       
          AND y.f_macd_dif <= y.f_macd_dea                     
          AND t.f_macd_dif > 0                                 
        GROUP BY t.symbol, s.name, flow.sector_name            
        ORDER BY flow.net_inflow_amount DESC;
    """)

    try:
        with engine_quant.connect() as conn:
            df_results = pd.read_sql(query_sql, conn, params={"today": today, "yesterday": yesterday})

        if not df_results.empty:
            # --- 步骤 3: 数据加工（打分 + 板块清洗） ---
            df_results['综合评分'] = df_results.apply(calculate_macd_res_score, axis=1)
            # 应用黑名单并取两个板块
            df_results['所属板块'] = df_results['all_sectors'].apply(clean_and_pick_two_sectors)
            
            df_results = df_results.sort_values('综合评分', ascending=False)

            # --- 输出报告部分 ---
            print("\n" + "💰" * 12 + " 资金面 + 技术面【强共振】报告 " + "💰" * 12)
            print(f"信号日期: {today} | 模式: 吸金主线 + 0轴上金叉")
            print("-" * 120)
            display_cols = ['代码', '名称', '所属板块', '综合评分', '板块流入(亿)', '量能倍数', 'DIF', 'HIST']
            print(df_results[display_cols].to_string(index=False))
            print("-" * 120)
            
            top_one = df_results.iloc[0]
            print(f"💡 研判：今日最强共振标的是【{top_one['名称']}】，所属板块【{top_one['所属板块']}】流入资金高达 {top_one['板块流入(亿)']} 亿。")
            print("💡 操作建议：已剔除噪音板块。重点关注评分 > 85 且量能倍数 > 2.0 的标的。")
            print("💰" * 38 + "\n")

            # 4. 存入数据库
            save_to_review_pool(df_results, today)
        else:
            print(f"\n今日 ({today}) 暂未发现符合资金共振形态的个股。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    run_macd_resonance_pipeline()