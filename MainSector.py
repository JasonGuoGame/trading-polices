import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine

# --- 配置 ---
DB_URL = 'mysql+pymysql://root:root@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def identify_main_line():
    xtdata.enable_hello = False
    
    # 1. 关键：下载板块数据（只需运行一次，数据会同步到本地）
    print("正在从服务器更新板块和行业数据，请稍候...")
    xtdata.download_sector_data()
    
    # 2. 获取所有板块列表并打印出来看看
    all_sectors = xtdata.get_sector_list()
    print(f"当前可用板块总数: {len(all_sectors)}")
    
    # 看看行业板块到底叫什么名字 (有些券商叫 '证监会行业-', 有些叫 '申万行业-')
    # 我们打印前30个来看看规则
    print("板块名称示例 (前20个):", all_sectors[:20])
    
    # 灵活匹配行业板块 (匹配包含 '行业'、'申万' 或 '概念' 的名称)
    industry_sectors = [s for s in all_sectors if any(k in s for k in ['行业-', '申万-', '证监会-'])]
    
    if not industry_sectors:
        print("警告：依然没有找到细分行业板块。尝试使用‘概念’板块...")
        industry_sectors = [s for s in all_sectors if '概念-' in s]

    if not industry_sectors:
        print("错误：无法获取行业或概念板块。请确认 QMT 客户端中‘数据管理’里的行业数据已下载。")
        return

    # 3. 读取数据库最新行情
    query = """
    SELECT symbol, trade_date, close, open, turnover_rate, amount 
    FROM stk_daily_kline 
    WHERE trade_date = (SELECT MAX(trade_date) FROM stk_daily_kline)
    """
    df_market = pd.read_sql(query, engine)
    if df_market.empty:
        print("错误：数据库行情为空。")
        return

    df_market['pct_chg'] = (df_market['close'] - df_market['open']) / df_market['open'] * 100

    # 4. 计算板块热力
    sector_results = []
    print(f"正在分析 {len(industry_sectors)} 个细分板块...")
    
    for sector in industry_sectors:
        stocks_in_sector = xtdata.get_stock_list_in_sector(sector)
        sector_df = df_market[df_market['symbol'].isin(stocks_in_sector)]
        
        if len(sector_df) < 5: # 板块内至少要有5只股才有代表性
            continue
            
        avg_ret = sector_df['pct_chg'].mean()
        avg_turnover = sector_df['turnover_rate'].fillna(0).mean()
        strong_stocks = len(sector_df[sector_df['pct_chg'] > 3])
        total_stocks = len(sector_df)
        breadth = (strong_stocks / total_stocks) * 100
        
        # 综合热力评分
        score = (avg_ret * 0.4) + (breadth * 0.4) + (avg_turnover * 0.2)
        
        sector_results.append({
            '板块名称': sector.split('-')[-1], # 只保留短名字
            '平均涨幅%': round(avg_ret, 2),
            '赚钱广度%': round(breadth, 2),
            '平均换手%': round(avg_turnover, 2),
            '样本数': total_stocks,
            '综合热力值': round(score, 3)
        })

    if not sector_results:
        print("分析结果为空。")
        return

    result_df = pd.DataFrame(sector_results).sort_values('综合热力值', ascending=False)
    print("\n--- 今日板块热力排行榜 (主线筛选) ---")
    print(result_df.head(20).to_string(index=False))

if __name__ == "__main__":
    identify_main_line()