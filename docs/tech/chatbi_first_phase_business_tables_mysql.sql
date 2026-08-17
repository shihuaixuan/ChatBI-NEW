-- ChatBI 首期验证业务表
-- 目标：为 Headless 模型提供物理展开、单一粒度、具备明确时间字段的业务表。

CREATE TABLE IF NOT EXISTS `fct_stall_order_daily` (
    `stat_date` DATE NOT NULL COMMENT '统计日期',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `stall_id` BIGINT NOT NULL COMMENT '档口ID',
    `order_cnt_sale` BIGINT NOT NULL DEFAULT 0 COMMENT '销售订单数',
    `order_cnt_booking` BIGINT NOT NULL DEFAULT 0 COMMENT '订货订单数',
    `order_cnt_total` BIGINT NOT NULL DEFAULT 0 COMMENT '总订单数',
    `item_qty_sale` BIGINT NOT NULL DEFAULT 0 COMMENT '销售商品件数',
    `item_qty_booking` BIGINT NOT NULL DEFAULT 0 COMMENT '订货商品件数',
    `item_qty_total` BIGINT NOT NULL DEFAULT 0 COMMENT '总商品件数',
    `gmv_sale` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '销售类GMV',
    `gmv_booking` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '订货类GMV',
    `gmv_total` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '总GMV',
    `order_customer_cnt_sale` BIGINT NOT NULL DEFAULT 0 COMMENT '销售下单客户数',
    `order_customer_cnt_booking` BIGINT NOT NULL DEFAULT 0 COMMENT '订货下单客户数',
    `order_customer_cnt_total` BIGINT NOT NULL DEFAULT 0 COMMENT '总下单客户数',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (`stat_date`, `seller_id`, `stall_id`),
    KEY `idx_order_daily_seller_stall_date` (`seller_id`, `stall_id`, `stat_date`),
    KEY `idx_order_daily_date` (`stat_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='档口订单日事实表';

CREATE TABLE IF NOT EXISTS `snap_unshipped_order` (
    `snapshot_date` DATE NOT NULL COMMENT '快照日期',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `stall_id` BIGINT NOT NULL COMMENT '档口ID',
    `order_no` VARCHAR(64) NOT NULL COMMENT '未发订单号',
    `customer_id` VARCHAR(64) DEFAULT NULL COMMENT '客户ID',
    `customer_name` VARCHAR(255) DEFAULT NULL COMMENT '客户名称',
    `order_amount` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '订单金额',
    `item_qty` BIGINT NOT NULL DEFAULT 0 COMMENT '订单商品总件数',
    `shipped_qty` BIGINT NOT NULL DEFAULT 0 COMMENT '已发件数',
    `unshipped_qty` BIGINT NOT NULL DEFAULT 0 COMMENT '未发件数',
    `pend_ship_time` DATETIME DEFAULT NULL COMMENT '进入待发货状态时间',
    `deadline_time` DATETIME DEFAULT NULL COMMENT '应发截止时间',
    `overtime_days` INT NOT NULL DEFAULT 0 COMMENT '超时天数',
    `is_overtime` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否超时',
    `product_summary` TEXT COMMENT '商品摘要，仅用于展示',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (`snapshot_date`, `seller_id`, `stall_id`, `order_no`),
    KEY `idx_unshipped_seller_stall_date` (`seller_id`, `stall_id`, `snapshot_date`),
    KEY `idx_unshipped_customer` (`customer_id`),
    KEY `idx_unshipped_overtime` (`snapshot_date`, `is_overtime`, `overtime_days`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='未发订单快照表';

CREATE TABLE IF NOT EXISTS `snap_product_inventory` (
    `snapshot_date` DATE NOT NULL COMMENT '库存快照日期',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `stall_id` BIGINT NOT NULL COMMENT '档口ID',
    `product_id` VARCHAR(64) NOT NULL COMMENT '商品ID',
    `goods_no` VARCHAR(128) DEFAULT NULL COMMENT '货号',
    `product_name` VARCHAR(255) DEFAULT NULL COMMENT '商品名称',
    `stock_qty` BIGINT NOT NULL DEFAULT 0 COMMENT '当前库存件数',
    `total_sales_qty` BIGINT NOT NULL DEFAULT 0 COMMENT '历史总销量',
    `sales_qty_7d` BIGINT NOT NULL DEFAULT 0 COMMENT '近7天销量',
    `sales_qty_30d` BIGINT NOT NULL DEFAULT 0 COMMENT '近30天销量',
    `last_sale_time` DATETIME DEFAULT NULL COMMENT '最后销售时间',
    `days_unsold` INT NOT NULL DEFAULT 0 COMMENT '未销售天数',
    `is_negative_stock` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否负库存',
    `is_dormant_30d` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否连续30天未动销',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (`snapshot_date`, `seller_id`, `stall_id`, `product_id`),
    KEY `idx_inventory_seller_stall_date` (`seller_id`, `stall_id`, `snapshot_date`),
    KEY `idx_inventory_goods_no` (`goods_no`),
    KEY `idx_inventory_status` (`snapshot_date`, `is_negative_stock`, `is_dormant_30d`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品库存快照表';

CREATE TABLE IF NOT EXISTS `fct_customer_trade_daily` (
    `stat_date` DATE NOT NULL COMMENT '交易统计日期',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `stall_id` BIGINT NOT NULL COMMENT '档口ID',
    `customer_id` VARCHAR(64) NOT NULL COMMENT '客户ID',
    `customer_name` VARCHAR(255) DEFAULT NULL COMMENT '客户名称',
    `channel_type` VARCHAR(32) DEFAULT NULL COMMENT '交易渠道，如线上或线下',
    `customer_gmv` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '客户当日GMV',
    `order_cnt` BIGINT NOT NULL DEFAULT 0 COMMENT '客户当日订单数',
    `item_qty` BIGINT NOT NULL DEFAULT 0 COMMENT '客户当日购买件数',
    `is_first_deal` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否首单成交',
    `is_active` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '当日是否活跃',
    `last_order_time` DATETIME DEFAULT NULL COMMENT '最近下单时间',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (`stat_date`, `seller_id`, `stall_id`, `customer_id`),
    KEY `idx_customer_trade_seller_stall_date` (`seller_id`, `stall_id`, `stat_date`),
    KEY `idx_customer_trade_customer_date` (`customer_id`, `stat_date`),
    KEY `idx_customer_trade_segment` (`stat_date`, `channel_type`, `is_first_deal`, `is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='客户交易日事实表';

CREATE TABLE IF NOT EXISTS `snap_customer_arrears` (
    `snapshot_date` DATE NOT NULL COMMENT '欠款快照日期',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `stall_id` BIGINT NOT NULL COMMENT '档口ID',
    `customer_id` VARCHAR(64) NOT NULL COMMENT '客户ID',
    `customer_name` VARCHAR(255) DEFAULT NULL COMMENT '客户名称',
    `arrears_amt` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '当前欠款余额',
    `arrears_bill_cnt` BIGINT NOT NULL DEFAULT 0 COMMENT '当前未结清欠款笔数',
    `earliest_arrears_date` DATE DEFAULT NULL COMMENT '最早未结清欠款日期',
    `latest_arrears_date` DATE DEFAULT NULL COMMENT '最近欠款发生日期',
    `arrears_days` INT NOT NULL DEFAULT 0 COMMENT '欠款账龄天数',
    `is_overdue` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否逾期',
    `overdue_amt` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '逾期欠款金额',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (`snapshot_date`, `seller_id`, `stall_id`, `customer_id`),
    KEY `idx_arrears_seller_stall_date` (`seller_id`, `stall_id`, `snapshot_date`),
    KEY `idx_arrears_customer_date` (`customer_id`, `snapshot_date`),
    KEY `idx_arrears_overdue` (`snapshot_date`, `is_overdue`, `arrears_days`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='客户欠款快照表';
