import pandas as pd
from xtquant import xtdata
import datetime
import akshare as ak

def get_market_value_from_qmt(symbol_list):
    """
    通过 MiniQMT 计算总市值 (单位：亿元)
    """
    print(f"正在从 MiniQMT 提取 {len(symbol_list)} 只股票的股本与现价...")
    df_plan = ak.stock_减持计划_em()
    results = []
    
    # 1. 获取最新行情快照 (获取现价)
    ticks = xtdata.get_full_tick(symbol_list)
    
    for symbol in symbol_list:
        try:
            # 2. 获取静态详细信息 (获取总股本)
            detail = xtdata.get_instrument_detail(symbol)
            
            if detail and symbol in ticks:
                # QMT 中的 TotalVolume 通常是‘股’
                total_shares = detail.get('TotalVolume', 0)
                last_price = ticks[symbol].get('lastPrice', 0)
                
                if total_shares > 0 and last_price > 0:
                    # 计算总市值并转为‘亿元’
                    total_mv = (total_shares * last_price) / 1e8
                    
                    results.append({
                        'symbol': symbol,
                        'total_mv': round(total_mv, 2),
                        'last_price': last_price
                    })
        except:
            continue
            
    return pd.DataFrame(results)

if __name__ == "__main__":
    # 测试代码
    stocks = ['600519.SH', '000001.SZ', '300750.SZ']
    df_mv = get_market_value_from_qmt(stocks)
    print(df_mv)