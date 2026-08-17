-- 语义层测试库：只保留业务 SQL 直接使用的原始物理字段。
CREATE DATABASE IF NOT EXISTS `sqlbot_semantic_test`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `sqlbot_semantic_test`;

CREATE TABLE IF NOT EXISTS `track_event` (
    `event_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '事件记录ID',
    `sellerId` BIGINT NOT NULL COMMENT '商家ID',
    `user_unique_id` VARCHAR(64) DEFAULT NULL COMMENT '用户唯一标识',
    `user_id` VARCHAR(64) DEFAULT NULL COMMENT '用户ID',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    `platform` VARCHAR(32) NOT NULL COMMENT '平台',
    `event_type` VARCHAR(64) NOT NULL COMMENT '事件类型',
    `page_name` VARCHAR(128) DEFAULT NULL COMMENT '页面名称',
    `event_time` DATETIME NOT NULL COMMENT '事件发生时间',
    PRIMARY KEY (`event_id`),
    KEY `idx_track_event_seller_time` (`sellerId`, `event_time`),
    KEY `idx_track_event_type` (`event_type`, `platform`),
    KEY `idx_track_event_user` (`user_id`, `user_unique_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户行为事件';

CREATE TABLE IF NOT EXISTS `user_favorite` (
    `favorite_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '关注记录ID',
    `scene_id` BIGINT NOT NULL COMMENT '业务场景ID，scene_type=1时为商家ID',
    `scene_type` TINYINT NOT NULL COMMENT '业务场景类型',
    `user_id` VARCHAR(64) NOT NULL COMMENT '用户ID',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    `create_time` DATETIME NOT NULL COMMENT '关注时间',
    PRIMARY KEY (`favorite_id`),
    KEY `idx_user_favorite_scene_time` (`scene_type`, `scene_id`, `create_time`),
    KEY `idx_user_favorite_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户关注记录';

CREATE TABLE IF NOT EXISTS `order_sale` (
    `order_no` VARCHAR(64) NOT NULL COMMENT '销售订单号',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `customer_id` BIGINT DEFAULT NULL COMMENT '客户ID',
    `order_date` DATETIME NOT NULL COMMENT '下单时间',
    `total_quantity` BIGINT NOT NULL DEFAULT 0 COMMENT '订单商品总件数',
    `total_amount` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '订单总金额',
    `received_amount` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '已收金额',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    PRIMARY KEY (`order_no`),
    KEY `idx_order_sale_seller_date` (`seller_id`, `order_date`),
    KEY `idx_order_sale_customer` (`seller_id`, `customer_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='销售订单';

CREATE TABLE IF NOT EXISTS `order_sale_item` (
    `item_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '销售订单明细ID',
    `order_no` VARCHAR(64) NOT NULL COMMENT '销售订单号',
    `spu_id` BIGINT NOT NULL COMMENT '商品SPU ID',
    `style_no` VARCHAR(128) NOT NULL COMMENT '货号',
    `goods_name` VARCHAR(255) NOT NULL COMMENT '商品名称',
    `quantity` BIGINT NOT NULL DEFAULT 0 COMMENT '销售件数',
    `total_price` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '明细总金额',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    PRIMARY KEY (`item_id`),
    KEY `idx_order_sale_item_order` (`order_no`),
    KEY `idx_order_sale_item_spu` (`spu_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='销售订单明细';

CREATE TABLE IF NOT EXISTS `order_purchase` (
    `order_no` VARCHAR(64) NOT NULL COMMENT '订货订单号',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `customer_id` BIGINT DEFAULT NULL COMMENT '客户ID',
    `order_date` DATETIME NOT NULL COMMENT '下单时间',
    `total_quantity` BIGINT NOT NULL DEFAULT 0 COMMENT '订单商品总件数',
    `total_amount` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '订单总金额',
    `ship_status` TINYINT NOT NULL DEFAULT 0 COMMENT '发货状态',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    PRIMARY KEY (`order_no`),
    KEY `idx_order_purchase_seller_date` (`seller_id`, `order_date`),
    KEY `idx_order_purchase_customer` (`seller_id`, `customer_id`),
    KEY `idx_order_purchase_ship_status` (`seller_id`, `ship_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订货订单';

CREATE TABLE IF NOT EXISTS `order_purchase_item` (
    `item_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '订货订单明细ID',
    `order_no` VARCHAR(64) NOT NULL COMMENT '订货订单号',
    `spu_id` BIGINT NOT NULL COMMENT '商品SPU ID',
    `style_no` VARCHAR(128) NOT NULL COMMENT '货号',
    `goods_name` VARCHAR(255) NOT NULL COMMENT '商品名称',
    `total_price` DECIMAL(18, 2) NOT NULL DEFAULT 0 COMMENT '明细总金额',
    `order_quantity` BIGINT NOT NULL DEFAULT 0 COMMENT '订货件数',
    `shipped_quantity` BIGINT NOT NULL DEFAULT 0 COMMENT '已发件数',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    PRIMARY KEY (`item_id`),
    KEY `idx_order_purchase_item_order` (`order_no`),
    KEY `idx_order_purchase_item_spu` (`spu_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订货订单明细';

CREATE TABLE IF NOT EXISTS `seller_customer` (
    `id` BIGINT NOT NULL COMMENT '客户ID',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `name` VARCHAR(255) NOT NULL COMMENT '客户名称',
    `phone` VARCHAR(32) DEFAULT NULL COMMENT '客户手机号',
    `create_time` DATETIME NOT NULL COMMENT '客户创建时间',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    PRIMARY KEY (`id`),
    KEY `idx_seller_customer_seller` (`seller_id`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商家客户';

CREATE TABLE IF NOT EXISTS `goods_spu` (
    `id` BIGINT NOT NULL COMMENT '商品SPU ID',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `style_no` VARCHAR(128) NOT NULL COMMENT '货号',
    `goods_name` VARCHAR(255) NOT NULL COMMENT '商品名称',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    PRIMARY KEY (`id`),
    KEY `idx_goods_spu_seller_style` (`seller_id`, `style_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品SPU';

CREATE TABLE IF NOT EXISTS `goods_sku` (
    `sku_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '商品SKU ID',
    `spu_id` BIGINT NOT NULL COMMENT '商品SPU ID',
    `seller_id` BIGINT NOT NULL COMMENT '商家ID',
    `stock` BIGINT NOT NULL DEFAULT 0 COMMENT '当前库存件数',
    `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记',
    `snapshot_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '库存观测时间',
    PRIMARY KEY (`sku_id`),
    KEY `idx_goods_sku_spu` (`spu_id`),
    KEY `idx_goods_sku_seller` (`seller_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品SKU库存';
