# 商城店铺 Agent 新 20 题运行概览

- 生成时间：2026-08-15T17:04:52+0800
- 数据集：商城店铺数据集（ID 243）
- 主问题：20
- 澄清分支：28
- 实际 Run：48

| 问题 | 终态 | SQL执行 | 最终答案 | 基准命中 | 澄清轮数 | 分支数 | 耗时ms |
|---|---|---:|---:|---:|---:|---:|---:|
| N01 | finished | 是 | 是 | 2/2 | 0 | 0 | 23633 |
| N02 | failed | 否 | 否 | 0/2 | 0 | 0 | 10133 |
| N03 | finished | 是 | 是 | 4/4 | 0 | 0 | 16844 |
| N04 | failed | 否 | 否 | 0/4 | 0 | 0 | 10991 |
| N05 | finished | 是 | 是 | 0/3 | 0 | 0 | 28531 |
| N06 | finished | 是 | 是 | 0/2 | 0 | 0 | 20373 |
| N07 | finished | 是 | 是 | 1/3 | 0 | 0 | 19246 |
| N08 | finished | 是 | 是 | 1/3 | 0 | 0 | 18191 |
| N09 | failed | 否 | 否 | 0/3 | 0 | 0 | 11025 |
| N10 | finished | 是 | 是 | 0/2 | 0 | 0 | 24258 |
| N11 | finished | 是 | 是 | 4/4 | 0 | 0 | 20981 |
| N12 | finished | 是 | 是 | 4/4 | 0 | 0 | 18540 |
| N13 | finished | 是 | 是 | 3/3 | 0 | 0 | 39705 |
| N14 | failed | 否 | 否 | 0/3 | 0 | 0 | 33533 |
| N15 | failed | 否 | 否 | 0/6 | 0 | 0 | 36051 |
| N16 | failed | 否 | 否 | 0/4 | 0 | 0 | 18754 |
| N17 | failed | 否 | 否 | 0/2 | 1 | 3 | 24660 |
| N18 | finished | 是 | 是 | 1/3 | 0 | 0 | 20017 |
| N19 | finished | 是 | 是 | 0/0 | 0 | 0 | 14954 |
| N20 | finished | 是 | 是 | 0/0 | 1 | 25 | 31065 |

## 澄清覆盖

| 问题 | 路径 | 终态 | SQL执行 | 最终答案 | API错误 |
|---|---|---|---:|---:|---:|
| N17 | 按客户ID分组查看 | failed | 否 | 否 | 无 |
| N17 | 筛选某个具体客户ID | failed | 否 | 否 | 无 |
| N17 | 不使用客户ID维度，查看汇总结果 | failed | 否 | 否 | 无 |
| N20 | 总下单客户数 | finished | 是 | 是 | 无 |
| N20 | 订货下单客户数 | finished | 是 | 是 | 无 |
| N20 | 欠款客户数 | finished | 是 | 是 | 无 |
| N20 | 新增成交客户数 | finished | 是 | 是 | 无 |
| N20 | 销售下单客户数 | finished | 是 | 是 | 无 |
| N20 | 档口ID（stall_id） | failed | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） | finished | 是 | 是 | 无 |
| N20 | 档口ID（stall_id） | waiting_user | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） | finished | 是 | 是 | 无 |
| N20 | 档口ID（stall_id） | failed | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 订货下单客户数 | finished | 是 | 是 | 无 |
| N20 | 档口ID（stall_id） → 销售下单客户数 | finished | 是 | 是 | 无 |
| N20 | 档口ID（stall_id） → 总下单客户数 | failed | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 客户当日订单数 | waiting_user | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 订货下单客户数（order_customer_cnt_booking） | waiting_user | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 销售下单客户数（order_customer_cnt_sale） | waiting_user | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 总下单客户数（order_customer_cnt_total） | waiting_user | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 客户当日订单数（order_cnt） | waiting_user | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 订货下单客户数（order_customer_cnt_booking） | failed | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 销售下单客户数（order_customer_cnt_sale） | finished | 是 | 是 | 无 |
| N20 | 档口ID（stall_id） → 总下单客户数（order_customer_cnt_total） | failed | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 客户当日订单数（order_cnt） | waiting_user | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 订货下单客户数（order_customer_cnt_booking） | failed | 否 | 否 | 无 |
| N20 | 档口ID（stall_id） → 销售下单客户数（order_customer_cnt_sale） | finished | 是 | 是 | 无 |
| N20 | 档口ID（stall_id） → 总下单客户数（order_customer_cnt_total） | waiting_user | 否 | 否 | 无 |

## 汇总

- 主问题执行 SQL：13/20
- 主问题产生答案：13/20
- 主问题全部基准关键值命中：5/18
- Run 事件补拉完整：48/48
- Run 内 SSE 批次序号有序：48/48
