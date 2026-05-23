SELECT 
    h1.symbol, 
    h1.name, 
    h1.end_date as '最新一期', h1.change_rate as '变动1%',
    h2.end_date as '上一期', h2.change_rate as '变动2%',
    h3.end_date as '上上期', h3.change_rate as '变动3%'
FROM stk_holders_history h1
JOIN stk_holders_history h2 ON h1.symbol = h2.symbol AND h2.end_date < h1.end_date
JOIN stk_holders_history h3 ON h1.symbol = h3.symbol AND h3.end_date < h2.end_date
WHERE h1.change_rate < 0  -- 最新一期减少
  AND h2.change_rate < 0  -- 上一期也减少
  AND h3.change_rate < 0  -- 上上期还减少
  -- 确保是最近的数据
  AND h1.end_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
GROUP BY h1.symbol
ORDER BY (h1.change_rate + h2.change_rate + h3.change_rate) ASC;