import akshare as ak
import pandas as pd
import time

def get_sector_hotness():
    print("正在从东方财富获取行业板块数据，请稍候...")
    print("正在拉取东方财富实时股东人数数据...")
    # df = ak.stock_zh_a_gdhs_em()
    # 1. 获取行业板块实时行情
    # 包含字段：板块名称, 涨跌幅, 总市值, 换手率, 成交额, 领涨股票, 等
    stock_zh_a_gdhs_detail_em_df = ak.stock_zh_a_gdhs_detail_em(symbol="000001")
    print(stock_zh_a_gdhs_detail_em_df)

    # for i in range(5):
    #     try:
    #         df_industry = ak.stock_board_industry_name_em()
    #     except Exception as e:
    #         print(f"失败第{i+1}次:", e)
    #         time.sleep(2)

    # 2. 数据清洗
    # 确保数值型字段为浮点数
    cols_to_fix = ['涨跌幅', '换手率', '成交额', '总市值']
    for col in cols_to_fix:
        df_industry[col] = pd.to_numeric(df_industry[col], errors='coerce')
    
    df_industry.dropna(subset=['涨跌幅', '换手率', '成交额'], inplace=True)

    # 3. 计算热度指数 (归一化处理)
    # 归一化函数：将数据缩放到 0-1 之间，方便加权比较
    def normalize(series):
        return (series - series.min()) / (series.max() - series.min())

    # 涨跌幅越好、换手率越高、成交额越大，代表热度越高
    df_industry['norm_change'] = normalize(df_industry['涨跌幅'])
    df_industry['norm_turnover'] = normalize(df_industry['换手率'])
    df_industry['norm_amount'] = normalize(df_industry['成交额'])

    # 自定义热度公式 (可以根据需求调整比例)
    # 这里给予 涨跌幅 40% 权重，换手率 40% 权重，成交额 20% 权重
    df_industry['热度指数'] = (
        df_industry['norm_change'] * 0.4 + 
        df_industry['norm_turnover'] * 0.4 + 
        df_industry['norm_amount'] * 0.2
    ) * 100

    # 4. 排序并取前 15 名
    hot_sectors = df_industry[[
        '板块名称', '涨跌幅', '换手率', '成交额', '总市值', '热度指数'
    ]].sort_values(by='热度指数', ascending=False).reset_index(drop=True)

    return hot_sectors

if __name__ == "__main__":
    try:
        result = get_sector_hotness()
        
        print("\n--- 当前最热门行业板块排名 (基于涨幅与成交活跃度) ---")
        # 格式化输出，成交额单位转为亿元
        result['成交额'] = (result['成交额'] / 100000000).round(2)
        result['热度指数'] = result['热度指数'].round(2)
        
        print(result.head(15).to_string(index=False))
        
    except Exception as e:
        print(f"获取数据失败: {e}")