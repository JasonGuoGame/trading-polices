from xtquant import xtdata

# ⚠️ 请务必修改为你电脑上正常 QMT 的实际安装路径
# 注意：Windows 路径中的反斜杠需要双写 '\\' 或在字符串前加 'r'
qmt_path = r"C:\国金证券QMT交易端\userdata" 

# 1. 指定路径初始化 xtdata
xtdata.set_data_home_dir(qmt_path)

# 2. 尝试下载数据（以平安银行为例）
print("正在尝试下载数据...")
res = xtdata.download_history_data('000001.SZ', '1d', '20260101', '20260820')

if res == 0 or res is True:
    print("✅ 下载成功！正常 QMT 连接正常。")
    # 3. 读取测试
    data = xtdata.get_market_data(stock_list=['000001.SZ'], period='1d', count=5)
    print(data)
else:
    print("❌ 下载失败，请检查正常 QMT 是否已开启“开放接口”或“Mini模式”。")
