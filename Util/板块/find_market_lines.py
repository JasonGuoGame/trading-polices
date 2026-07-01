import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def identify_market_lines():
    print(f"[{datetime.datetime.now()}] 🔍 正在进行‘主线/暗线’深度挖掘...")

    # 1. 获取最近 10 个交易日的数据
    # 我们需要足够长的样本来判断“持续性”
    query_sql = """
        SELECT trade_date, sector_name, total_score, money_score, rank_pos, leader_score
        FROM stk_sector_scores
        WHERE trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM stk_sector_scores), INTERVAL 20 DAY)
        ORDER BY trade_date DESC
    """
    df = pd.read_sql(query_sql, engine_review)
    
    if df.empty:
        print("❌ 数据库中没有评分数据，请先运行 calculate_sector_scores 脚本。")
        return

    # 获取最新日期和历史日期列表
    all_dates = sorted(df['trade_date'].unique(), reverse=True)
    today = all_dates[0]
    last_5_dates = all_dates[1:6]
    
    print(f"📅 分析基准日: {today}")

    # 2. 数据透视与特征提取
    # 提取今日数据
    df_today = df[df['trade_date'] == today].copy()
    
    # 统计过去 10 个交易日的表现
    df_hist = df[df['trade_date'] != today]
    
    # 计算持续性特征
    # A. 过去10天进入前10名的次数
    persistence = df_hist[df_hist['rank_pos'] <= 10].groupby('sector_name').size().rename('top10_count')
    # B. 过去5天的平均排名
    avg_rank_5d = df[df['trade_date'].isin(last_5_dates)].groupby('sector_name')['rank_pos'].mean().rename('avg_rank_5d')
    # C. 过去5天的平均资金分
    avg_money_5d = df[df['trade_date'].isin(last_5_dates)].groupby('sector_name')['money_score'].mean().rename('avg_money_5d')

    # 合并特征到今日数据
    df_analysis = df_today.merge(persistence, on='sector_name', how='left').fillna(0)
    df_analysis = df_analysis.merge(avg_rank_5d, on='sector_name', how='left').fillna(50) # 没出现的默认50名
    df_analysis = df_analysis.merge(avg_money_5d, on='sector_name', how='left').fillna(0)

    # 3. 核心分类算法
    results = []

    for _, row in df_analysis.iterrows():
        label = "未知"
        reason = ""
        
        # --- 逻辑 1：识别绝对主线 ---
        # 条件：过去10天至少有4天进前10，且今天还在前10，且评分够高
        if row['top10_count'] >= 4 and row['rank_pos'] <= 10:
            label = "🔥 绝对主线"
            reason = f"持续高热，10日内{int(row['top10_count'])}次入围Top10"
        
        # --- 逻辑 2：识别潜力暗线 ---
        # 条件：今天排名比过去5天平均排名提升了 15 名以上，且资金评分在增长
        elif row['rank_pos'] <= 20 and (row['avg_rank_5d'] - row['rank_pos']) >= 15:
            if row['money_score'] > row['avg_money_5d']:
                label = "🚀 潜力暗线"
                reason = f"排名从{row['avg_rank_5d']:.0f}位急速跃升至{row['rank_pos']}位，资金入场明显"
        
        # --- 逻辑 3：识别一日游 ---
        # 条件：今天排名极高（前5），但过去几乎没出现过，且资金分一般
        elif row['rank_pos'] <= 5 and row['top10_count'] <= 1:
            label = "🌀 一日游预警"
            reason = "突发性上涨，缺乏历史资金护盘，警惕明日虹吸"

        if label != "未知":
            results.append({
                '板块': row['sector_name'],
                '类型': label,
                '今日排名': int(row['rank_pos']),
                '今日总分': row['total_score'],
                '分析逻辑': reason
            })

    # 4. 打印报告
    df_res = pd.DataFrame(results)
    if not df_res.empty:
        # 按照类型和排名排序
        df_res['type_order'] = df_res['类型'].map({"🔥 绝对主线": 1, "🚀 潜力暗线": 2, "🌀 一日游预警": 3})
        df_res = df_res.sort_values(['type_order', '今日排名'])

        print("\n" + "=================" * 5)
        print(f"📊 A股双线作战地图 ({today})")
        print("=================" * 5)
        
        for label in ["🔥 绝对主线", "🚀 潜力暗线", "🌀 一日游预警"]:
            subset = df_res[df_res['类型'] == label]
            if not subset.empty:
                print(f"\n【{label}】")
                print("-" * 80)
                print(subset[['板块', '今日排名', '今日总分', '分析逻辑']].to_string(index=False))
        
        print("\n" + "=================" * 5)
    else:
        print("今日市场格局混沌，未发现明确的主暗线。")

if __name__ == "__main__":
    identify_market_lines()