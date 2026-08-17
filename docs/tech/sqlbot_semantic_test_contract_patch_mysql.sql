-- 为语义契约补充可验证的行粒度、商品关联键和库存观测时间。
USE `sqlbot_semantic_test`;

ALTER TABLE `track_event`
    ADD COLUMN `event_id` BIGINT NOT NULL AUTO_INCREMENT FIRST,
    ADD PRIMARY KEY (`event_id`);

ALTER TABLE `user_favorite`
    ADD COLUMN `favorite_id` BIGINT NOT NULL AUTO_INCREMENT FIRST,
    ADD PRIMARY KEY (`favorite_id`);

ALTER TABLE `order_sale_item`
    ADD COLUMN `item_id` BIGINT NOT NULL AUTO_INCREMENT FIRST,
    ADD COLUMN `del_flag` CHAR(1) NOT NULL DEFAULT '0' COMMENT '删除标记' AFTER `total_price`,
    ADD PRIMARY KEY (`item_id`);

ALTER TABLE `order_purchase_item`
    ADD COLUMN `item_id` BIGINT NOT NULL AUTO_INCREMENT FIRST,
    ADD COLUMN `spu_id` BIGINT NOT NULL COMMENT '商品SPU ID' AFTER `order_no`,
    ADD PRIMARY KEY (`item_id`),
    ADD KEY `idx_order_purchase_item_spu` (`spu_id`);

ALTER TABLE `goods_sku`
    ADD COLUMN `sku_id` BIGINT NOT NULL AUTO_INCREMENT FIRST,
    ADD COLUMN `snapshot_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '库存观测时间' AFTER `del_flag`,
    ADD PRIMARY KEY (`sku_id`);
