CREATE TABLE stk_daily_kline (
    symbol VARCHAR(20) NOT NULL,
    trade_date DATE NOT NULL,
    open DECIMAL(18, 4),
    high DECIMAL(18, 4),
    low DECIMAL(18, 4),
    close DECIMAL(18, 4),
    volume BIGINT,                        -- 成交量
    amount DECIMAL(20, 4),                -- 成交额
    turnover_rate DECIMAL(10, 4),         -- 换手率
    per_factor DECIMAL(18, 8),            -- 复权因子
    PRIMARY KEY (symbol, trade_date),
    KEY idx_date (trade_date)             -- 方便按日期范围查询
) ENGINE=InnoDB;

CREATE TABLE stock_info (
    symbol VARCHAR(20) PRIMARY KEY,       -- 证券代码 (如: 600519.SH)
    display_name VARCHAR(50),             -- 中文名称
    listing_date DATE,                    -- 上市日期
    sector VARCHAR(50),                   -- 所属行业/板块
    is_suspended TINYINT(1) DEFAULT 0,    -- 是否停牌
    instrument_type VARCHAR(20)           -- 证券类型 (STOCK/INDEX/FUND)
) ENGINE=InnoDB;z