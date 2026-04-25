import akshare as ak

# 查询单只股票的历史股东人数（以平安银行为例）
df = ak.stock_zh_a_gdhs_em(symbol="000001")

# 按披露日期降序排序，提取最新一期数据
df_latest = df.sort_values("截止日期", ascending=False).head(1)
print(df_latest)