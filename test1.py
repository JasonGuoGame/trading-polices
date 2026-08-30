import pywencai
import pandas as pd

def get_ths_sectors():
    """
    通过问财获取同花顺二级行业/概念板块列表
    """
    # 比如查询同花顺所有概念板块或行业板块
    res = pywencai.get(query="同花顺概念板块列表", loop=True)
    
    if res is not None and not res.empty:
        print("获取成功，同花顺板块样例：")
        print(res.head())
        return res
    else:
        print("未获取到板块数据")
        return None

# 获取具体板块内的个股，并转换为 QMT 代码格式
def format_to_qmt_code(code_list):
    """
    将 6 位代码转换为 QMT 要求的 000001.SZ / 600001.SH 格式
    """
    qmt_codes = []
    for code in code_list:
        code_str = str(code).zfill(6)
        if code_str.startswith(('60', '68', '900')):
            qmt_codes.append(f"{code_str}.SH")
        elif code_str.startswith(('00', '30', '200')):
            qmt_codes.append(f"{code_str}.SZ")
        elif code_str.startswith(('8', '4', '920')):
            qmt_codes.append(f"{code_str}.BJ")
    return qmt_codes

if __name__ == '__main__':
    # 1. 查询某个同花顺概念下的成分股
    df_stocks = pywencai.get(query="固态电池概念股", loop=True)
    
    # 2. 提取股票代码并格式化为 QMT 格式
    raw_codes = df_stocks['股票代码'].tolist()
    qmt_codes = format_to_qmt_code(raw_codes)
    
    print("QMT 格式股票池：", qmt_codes[:5])