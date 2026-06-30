import akshare as ak
print(f"当前版本: {ak.__version__}")

# 再次检查接口
methods = [method for method in dir(ak) if 'stock_board' in method and 'ths' in method]
print("包含的接口:", methods)