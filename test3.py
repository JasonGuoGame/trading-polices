import pandas as pd
import akshare as ak

# 设置显示选项
pd.set_option('display.max_rows', None)      # 显示所有行
pd.set_option('display.max_columns', None)   # 显示所有列
pd.set_option('display.width', None)         # 不限制输出宽度，防止列被折叠
pd.set_option('display.max_colwidth', None)  # 不限制单元格内容宽度

# 获取数据
df_ths_con = ak.stock_board_concept_name_ths()  

# 打印数据
print(df_ths_con)
