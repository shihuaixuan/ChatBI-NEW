# ChatBI 首期 20 问 API 评估报告

- 数据集：`243`
- 完成：20/20
- 全链路通过：4/20

## 汇总

| # | 问题 | API | 意图 | 指标 | 维度 | 过滤 | 结果 | 总体 |
|---:|---|---|---|---|---|---|---|---|
| 1 | 2026 年 6 月各档口的总 GMV 分别是多少？ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 2 | 最近 30 天每天的总订单数和总 GMV 趋势如何？ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| 3 | 2026 年 6 月销售 GMV 最高的 5 个档口是哪些？ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4 | 各档口的销售订单平均客单价分别是多少？ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 5 | 当前共有多少笔未发订单？ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 6 | 当前超时未发订单有多少笔？ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| 7 | 超时天数最长的 10 笔订单是哪些？ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 8 | 各档口当前的未发件数和未发订单金额分别是多少？ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| 9 | 当前各档口的库存总量是多少？ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| 10 | 当前有哪些商品处于负库存状态？ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 11 | 连续 30 天未动销的商品有多少个？ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| 12 | 当前库存量最低的 10 个商品是哪些？ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| 13 | 2026 年 6 月消费金额最高的 10 位客户是谁？ | ❌ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| 14 | 最近 30 天每天的客户 GMV 趋势如何？ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| 15 | 各档口的新增成交客户数分别是多少？ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| 16 | 线上和线下渠道的 GMV、订单数分别是多少？ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| 17 | 当前客户欠款总金额是多少？ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| 18 | 当前有多少位客户已经逾期？ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 19 | 欠款金额最高的 10 位客户是谁？ | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| 20 | 各档口的欠款金额、逾期金额和欠款客户数分别是多少？ | ❌ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |

## 1. 2026 年 6 月各档口的总 GMV 分别是多少？

- 状态：`succeeded`
- Run ID：`eval-20q-01-1782878113`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：0
- 预期 SQL：`SELECT stall_id, ROUND(SUM(gmv_total), 2) AS gmv_total FROM fct_stall_order_daily WHERE stat_date >= '2026-06-01' AND stat_date < '2026-07-01' GROUP BY stall_id ORDER BY stall_id`
- 实际 SQL：`select fct_stall_order_daily.stall_id as stall_id, sum(fct_stall_order_daily.gmv_total) as gmv_total from fct_stall_order_daily fct_stall_order_daily where fct_stall_order_daily.stat_date >= '2026-06-01' and fct_stall_order_daily.stat_date < '2026-07-01' group by fct_stall_order_daily.stall_id limit 100`
- 预期结果：`[{"stall_id": 100011, "gmv_total": 458248.85}, {"stall_id": 100012, "gmv_total": 447178.97}, {"stall_id": 100013, "gmv_total": 548229.04}, {"stall_id": 100021, "gmv_total": 515352.97}, {"stall_id": 100022, "gmv_total": 583687.37}, {"stall_id": 100023, "gmv_total": 444028.7}]`
- 实际结果：`[{"stall_id": 100011, "gmv_total": 458248.85}, {"stall_id": 100012, "gmv_total": 447178.97}, {"stall_id": 100013, "gmv_total": 548229.04}, {"stall_id": 100021, "gmv_total": 515352.97}, {"stall_id": 100022, "gmv_total": 583687.37}, {"stall_id": 100023, "gmv_total": 444028.7}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": true, "result_equal": true, "overall": true}`

## 2. 最近 30 天每天的总订单数和总 GMV 趋势如何？

- 状态：`succeeded`
- Run ID：`eval-20q-02-1782878134`
- 预期意图：`trend_analysis`
- 实际意图：`trend_analysis`
- 交互次数：0
- 预期 SQL：`SELECT stat_date, SUM(order_cnt_total) AS order_cnt_total, ROUND(SUM(gmv_total), 2) AS gmv_total FROM fct_stall_order_daily WHERE stat_date >= '2026-06-01' AND stat_date <= '2026-06-30' GROUP BY stat_date ORDER BY stat_date`
- 实际 SQL：`select sum(fct_stall_order_daily.order_cnt_total) as order_cnt_total, sum(fct_stall_order_daily.gmv_total) as gmv_total from fct_stall_order_daily fct_stall_order_daily where fct_stall_order_daily.stat_date >= DATE_SUB(CURRENT_DATE, INTERVAL 29 DAY) and fct_stall_order_daily.stat_date <= CURRENT_DATE order by order_cnt_total asc limit 100`
- 预期结果：`[{"stat_date": "2026-06-01", "order_cnt_total": 45.0, "gmv_total": 67026.0}, {"stat_date": "2026-06-02", "order_cnt_total": 38.0, "gmv_total": 78243.69}, {"stat_date": "2026-06-03", "order_cnt_total": 37.0, "gmv_total": 78129.96}, {"stat_date": "2026-06-04", "order_cnt_total": 38.0, "gmv_total": 75280.64}, {"stat_date": "2026-06-05", "order_cnt_total": 43.0, "gmv_total": 89589.5}, {"stat_date": "2026-06-06", "order_cnt_total": 45.0, "gmv_total": 135736.12}, {"stat_date": "2026-06-07", "order_cnt_total": 39.0, "gmv_total": 66432.32}, {"stat_date": "2026-06-08", "order_cnt_total": 55.0, "gmv_total": 136159.64}, {"stat_date": "2026-06-09", "order_cnt_total": 37.0, "gmv_total": 67186.8}, {"stat_date": "2026-06-10", "order_cnt_total": 57.0, "gmv_total": 88070.91}, {"stat_date": "2026-06-11", "order_cnt_total": 43.0, "gmv_total": 100349.7}, {"stat_date": "2026-06-12", "order_cnt_total": 42.0, "gmv_total": 123554.1}, {"stat_date": "2026-06-13", "order_cnt_total": 41.0, "gmv_total": 100126.6}, {"stat_date": "2026-06-14", "order_cnt_total": 40.0, "gmv_total": 74112.18}, {"stat_date": "2026-06-15", "order_cnt_total": 50.0, "gmv_total": 80275.38}, {"stat_date": "2026-06-16", "order_cnt_total": 43.0, "gmv_total": 113013.95}, {"stat_date": "2026-06-17", "order_cnt_total": 49.0, "gmv_total": 103924.4}, {"stat_date": "2026-06-18", "order_cnt_total": 50.0, "gmv_total": 99526.05}, {"stat_date": "2026-06-19", "order_cnt_total": 42.0, "gmv_total": 111319.72}, {"stat_date": "2026-06-20", "order_cnt_total": 46.0, "gmv_total": 120524.68}, {"stat_date": "2026-06-21", "order_cnt_total": 45.0, "gmv_total": 88305.6}, {"stat_date": "2026-06-22", "order_cnt_total": 38.0, "gmv_total": 65603.78}, {"stat_date": "2026-06-23", "order_cnt_total": 51.0, "gmv_total": 148549.64}, {"stat_date": "2026-06-24", "order_cnt_total": 41.0, "gmv_total": 102126.9}, {"stat_date": "2026-06-25", "order_cnt_total": 54.0, "gmv_total": 96876.24}, {"stat_date": "2026-06-26", "order_cnt_total": 49.0, "gmv_total": 151490.61}, {"stat_date": "2026-06-27", "order_cnt_total": 34.0, "gmv_total": 71938.13}, {"stat_date": "2026-06-28", "order_cnt_total": 48.0, "gmv_total": 141225.27}, {"stat_date": "2026-06-29", "order_cnt_total": 49.0, "gmv_total": 99194.88}, {"stat_date": "2026-06-30", "order_cnt_total": 43.0, "gmv_total": 122832.51}]`
- 实际结果：`[{"order_cnt_total": 1287.0, "gmv_total": 2929699.9}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": true, "result_equal": false, "overall": false}`

## 3. 2026 年 6 月销售 GMV 最高的 5 个档口是哪些？

- 状态：`succeeded`
- Run ID：`eval-20q-03-1782878166`
- 预期意图：`ranking_analysis`
- 实际意图：`ranking_analysis`
- 交互次数：1
- 预期 SQL：`SELECT stall_id, ROUND(SUM(gmv_sale), 2) AS gmv_sale FROM fct_stall_order_daily WHERE stat_date >= '2026-06-01' AND stat_date < '2026-07-01' GROUP BY stall_id ORDER BY gmv_sale DESC LIMIT 5`
- 实际 SQL：`select fct_stall_order_daily.stall_id as stall_id, sum(fct_stall_order_daily.gmv_sale) as gmv_sale from fct_stall_order_daily fct_stall_order_daily where fct_stall_order_daily.stat_date >= '2026-06-01' and fct_stall_order_daily.stat_date < '2026-07-01' group by fct_stall_order_daily.stall_id order by gmv_sale desc limit 5`
- 预期结果：`[{"stall_id": 100022, "gmv_sale": 468008.05}, {"stall_id": 100013, "gmv_sale": 465102.38}, {"stall_id": 100021, "gmv_sale": 435802.98}, {"stall_id": 100011, "gmv_sale": 384538.33}, {"stall_id": 100023, "gmv_sale": 335172.52}]`
- 实际结果：`[{"stall_id": 100022, "gmv_sale": 468008.05}, {"stall_id": 100013, "gmv_sale": 465102.38}, {"stall_id": 100021, "gmv_sale": 435802.98}, {"stall_id": 100011, "gmv_sale": 384538.33}, {"stall_id": 100023, "gmv_sale": 335172.52}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": true, "result_equal": true, "overall": true}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "销售类GMV", "value": 269}, {"label": "订货类GMV", "value": 270}, {"label": "总GMV", "value": 271}, {"label": "客户当日GMV", "value": 286}, {"label": "销售订单平均客单价", "value": 275}, {"label": "销售订单数", "value": 263}, {"label": "销售商品件数", "value": 266}, {"label": "销售下单客户数", "value": 272}], "selected_response": {"metric": "269"}}]`

## 4. 各档口的销售订单平均客单价分别是多少？

- 状态：`succeeded`
- Run ID：`eval-20q-04-1782878194`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：0
- 预期 SQL：`SELECT stall_id, ROUND(SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0), 2) AS aov_sale FROM fct_stall_order_daily GROUP BY stall_id ORDER BY stall_id`
- 实际 SQL：`select fct_stall_order_daily.stall_id as stall_id, ROUND(SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0), 2) as aov_sale from fct_stall_order_daily fct_stall_order_daily group by fct_stall_order_daily.stall_id limit 100`
- 预期结果：`[{"stall_id": 100011, "aov_sale": 2316.5}, {"stall_id": 100012, "aov_sale": 2202.34}, {"stall_id": 100013, "aov_sale": 2268.79}, {"stall_id": 100021, "aov_sale": 2212.2}, {"stall_id": 100022, "aov_sale": 2340.04}, {"stall_id": 100023, "aov_sale": 2007.02}]`
- 实际结果：`[{"stall_id": 100011, "aov_sale": 2316.5}, {"stall_id": 100012, "aov_sale": 2202.34}, {"stall_id": 100013, "aov_sale": 2268.79}, {"stall_id": 100021, "aov_sale": 2212.2}, {"stall_id": 100022, "aov_sale": 2340.04}, {"stall_id": 100023, "aov_sale": 2007.02}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": true, "result_equal": true, "overall": true}`

## 5. 当前共有多少笔未发订单？

- 状态：`succeeded`
- Run ID：`eval-20q-05-1782878215`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：1
- 预期 SQL：`SELECT COUNT(DISTINCT order_no) AS unshipped_order_cnt FROM snap_unshipped_order`
- 实际 SQL：`select COUNT(DISTINCT order_no) as unshipped_order_cnt from snap_unshipped_order snap_unshipped_order limit 100`
- 预期结果：`[{"unshipped_order_cnt": 40}]`
- 实际结果：`[{"unshipped_order_cnt": 40}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": true, "result_equal": true, "overall": true}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "未发订单数", "value": 304}, {"label": "超时未发订单数", "value": 305}, {"label": "超时未发订单数", "value": 311}, {"label": "销售订单数", "value": 263}, {"label": "订货订单数", "value": 264}, {"label": "总订单数", "value": 265}, {"label": "销售订单平均客单价", "value": 275}, {"label": "订单金额", "value": 276}, {"label": "订单商品总件数", "value": 277}, {"label": "客户当日订单数", "value": 287}, {"label": "订货订单平均客单价", "value": 302}, {"label": "订单平均客单价", "value": 303}, {"label": "未发件数", "value": 279}], "selected_response": {"metric": "304"}}]`

## 6. 当前超时未发订单有多少笔？

- 状态：`failed`
- Run ID：`eval-20q-06-1782878238`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：2
- 预期 SQL：`SELECT COUNT(DISTINCT order_no) AS overtime_order_cnt FROM snap_unshipped_order WHERE is_overtime = 1`
- 实际 SQL：``
- 预期结果：`[{"overtime_order_cnt": 11}]`
- 实际结果：`[]`
- 检查：`{"api_succeeded": false, "intent_type_correct": true, "metric_binding_complete": false, "dimension_binding_complete": true, "table_correct": false, "time_or_filter_present": false, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_slot_clarification", "prompt": "请确认“是否超时”这个维度的使用方式。", "options": [{"label": "按是否超时分组查看", "value": {"dimension": "是否超时", "dimension_usage": "group_by"}}, {"label": "筛选某个具体是否超时", "value": {"dimension": "是否超时", "dimension_usage": "filter_value_required", "dimension_value_fields": ["是否超时"]}}, {"label": "不使用是否超时维度", "value": {"dimension": "是否超时", "dimension_usage": "ignore"}}], "selected_response": {"dimension": "是否超时", "dimension_usage": "group_by"}}, {"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "当前未结清欠款笔数", "value": 292}, {"label": "超时未发订单数", "value": 305}, {"label": "超时未发订单数", "value": 311}, {"label": "未发订单数", "value": 304}, {"label": "销售订单数", "value": 263}, {"label": "订货订单数", "value": 264}, {"label": "总订单数", "value": 265}, {"label": "销售订单平均客单价", "value": 275}, {"label": "订单金额", "value": 276}, {"label": "订单商品总件数", "value": 277}, {"label": "客户当日订单数", "value": 287}, {"label": "订货订单平均客单价", "value": 302}, {"label": "订单平均客单价", "value": 303}, {"label": "未发件数", "value": 279}], "selected_response": {"metric": "292"}}]`

## 7. 超时天数最长的 10 笔订单是哪些？

- 状态：`succeeded`
- Run ID：`eval-20q-07-1782878272`
- 预期意图：`ranking_analysis, detail_query`
- 实际意图：`detail_query`
- 交互次数：2
- 预期 SQL：`SELECT order_no, seller_id, stall_id, customer_name, overtime_days, unshipped_qty, order_amount FROM snap_unshipped_order WHERE is_overtime = 1 ORDER BY overtime_days DESC, order_no LIMIT 10`
- 实际 SQL：`select snap_unshipped_order.overtime_days as overtime_days, COUNT(DISTINCT CASE WHEN is_overtime = 1 THEN order_no END) as overtime_unshipped_order_cnt from snap_unshipped_order snap_unshipped_order group by snap_unshipped_order.overtime_days order by overtime_unshipped_order_cnt desc limit 10`
- 预期结果：`[{"order_no": "USO202606300004", "seller_id": 10002, "stall_id": 100021, "customer_name": "合肥优品", "overtime_days": 13, "unshipped_qty": 4, "order_amount": 8178.0}, {"order_no": "USO202606300011", "seller_id": 10002, "stall_id": 100022, "customer_name": "合肥优品", "overtime_days": 13, "unshipped_qty": 3, "order_amount": 2880.0}, {"order_no": "USO202606300036", "seller_id": 10002, "stall_id": 100023, "customer_name": "合肥优品", "overtime_days": 13, "unshipped_qty": 2, "order_amount": 1275.0}, {"order_no": "USO202606300001", "seller_id": 10001, "stall_id": 100011, "customer_name": "杭州衣阁", "overtime_days": 5, "unshipped_qty": 3, "order_amount": 4752.0}, {"order_no": "USO202606300024", "seller_id": 10002, "stall_id": 100023, "customer_name": "合肥优品", "overtime_days": 5, "unshipped_qty": 1, "order_amount": 112.0}, {"order_no": "USO202606300002", "seller_id": 10001, "stall_id": 100012, "customer_name": "合肥优品", "overtime_days": 1, "unshipped_qty": 6, "order_amount": 16296.0}, {"order_no": "USO202606300005", "seller_id": 10002, "stall_id": 100022, "customer_name": "温州名品", "overtime_days": 1, "unshipped_qty": 7, "order_amount": 8288.0}, {"order_no": "USO202606300021", "seller_id": 10001, "stall_id": 100013, "customer_name": "合肥优品", "overtime_days": 1, "unshipped_qty": 2, "order_amount": 7650.0}, {"order_no": "USO202606300022", "seller_id": 10002, "stall_id": 100021, "customer_name": "苏州云裳", "overtime_days": 1, "unshipped_qty": 17, "order_amount": 11178.0}, {"order_no": "USO202606300023", "seller_id": 10002, "stall_id": 100022, "customer_name": "温州名品", "overtime_days": 1, "unshipped_qty": 6, "order_amount": 3612.0}]`
- 实际结果：`[{"overtime_days": 1, "overtime_unshipped_order_cnt": 6}, {"overtime_days": 13, "overtime_unshipped_order_cnt": 3}, {"overtime_days": 5, "overtime_unshipped_order_cnt": 2}, {"overtime_days": 0, "overtime_unshipped_order_cnt": 0}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": false, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_slot_clarification", "prompt": "请确认“超时天数”这个维度的使用方式。", "options": [{"label": "按超时天数分组查看", "value": {"dimension": "超时天数", "dimension_usage": "group_by"}}, {"label": "筛选某个具体超时天数", "value": {"dimension": "超时天数", "dimension_usage": "filter_value_required", "dimension_value_fields": ["超时天数"]}}, {"label": "不使用超时天数维度", "value": {"dimension": "超时天数", "dimension_usage": "ignore"}}], "selected_response": {"dimension": "超时天数", "dimension_usage": "group_by"}}, {"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "超时未发订单数", "value": 305}, {"label": "超时未发订单数", "value": 311}], "selected_response": {"metric": "305"}}]`

## 8. 各档口当前的未发件数和未发订单金额分别是多少？

- 状态：`succeeded`
- Run ID：`eval-20q-08-1782878304`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：0
- 预期 SQL：`SELECT stall_id, SUM(unshipped_qty) AS unshipped_qty, ROUND(SUM(order_amount), 2) AS order_amount FROM snap_unshipped_order GROUP BY stall_id ORDER BY stall_id`
- 实际 SQL：`select snap_unshipped_order.snapshot_date as snapshot_date, snap_unshipped_order.stall_id as stall_id, sum(snap_unshipped_order.unshipped_qty) as unshipped_qty from snap_unshipped_order snap_unshipped_order group by snap_unshipped_order.snapshot_date, snap_unshipped_order.stall_id limit 100`
- 预期结果：`[{"stall_id": 100011, "unshipped_qty": 30.0, "order_amount": 16130.0}, {"stall_id": 100012, "unshipped_qty": 63.0, "order_amount": 54022.0}, {"stall_id": 100013, "unshipped_qty": 31.0, "order_amount": 37062.0}, {"stall_id": 100021, "unshipped_qty": 83.0, "order_amount": 41444.0}, {"stall_id": 100022, "unshipped_qty": 33.0, "order_amount": 27653.0}, {"stall_id": 100023, "unshipped_qty": 42.0, "order_amount": 30007.0}]`
- 实际结果：`[{"snapshot_date": "2026-06-30", "stall_id": 100011, "unshipped_qty": 30.0}, {"snapshot_date": "2026-06-30", "stall_id": 100012, "unshipped_qty": 63.0}, {"snapshot_date": "2026-06-30", "stall_id": 100013, "unshipped_qty": 31.0}, {"snapshot_date": "2026-06-30", "stall_id": 100021, "unshipped_qty": 83.0}, {"snapshot_date": "2026-06-30", "stall_id": 100022, "unshipped_qty": 33.0}, {"snapshot_date": "2026-06-30", "stall_id": 100023, "unshipped_qty": 42.0}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": false, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": true, "result_equal": false, "overall": false}`

## 9. 当前各档口的库存总量是多少？

- 状态：`failed`
- Run ID：`eval-20q-09-1782878327`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：1
- 预期 SQL：`SELECT stall_id, SUM(stock_qty) AS stock_qty FROM snap_product_inventory GROUP BY stall_id ORDER BY stall_id`
- 实际 SQL：``
- 预期结果：`[{"stall_id": 100011, "stock_qty": 7023.0}, {"stall_id": 100012, "stock_qty": 8504.0}, {"stall_id": 100013, "stock_qty": 7208.0}, {"stall_id": 100021, "stock_qty": 7396.0}, {"stall_id": 100022, "stock_qty": 8717.0}, {"stall_id": 100023, "stock_qty": 6410.0}]`
- 实际结果：`[]`
- 检查：`{"api_succeeded": false, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": false, "time_or_filter_present": true, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "当前库存件数", "value": 280}, {"label": "是否负库存", "value": 284}, {"label": "负库存商品数", "value": 306}], "selected_response": {"metric": "280"}}]`

## 10. 当前有哪些商品处于负库存状态？

- 状态：`succeeded`
- Run ID：`eval-20q-10-1782878342`
- 预期意图：`detail_query`
- 实际意图：`detail_query`
- 交互次数：1
- 预期 SQL：`SELECT product_id, goods_no, product_name, seller_id, stall_id, stock_qty FROM snap_product_inventory WHERE stock_qty < 0 ORDER BY stock_qty, product_id`
- 实际 SQL：`select snap_product_inventory.snapshot_date as snapshot_date, snap_product_inventory.product_id as product_id, snap_product_inventory.product_name as product_name, sum(snap_product_inventory.stock_qty) as stock_qty from snap_product_inventory snap_product_inventory group by snap_product_inventory.snapshot_date, snap_product_inventory.product_id, snap_product_inventory.product_name limit 100`
- 预期结果：`[{"product_id": "P1000101005", "goods_no": "G010005", "product_name": "复古牛仔夹克", "seller_id": 10001, "stall_id": 100011, "stock_qty": -21}, {"product_id": "P1000103008", "goods_no": "G030008", "product_name": "法式碎花连衣裙", "seller_id": 10001, "stall_id": 100013, "stock_qty": -12}, {"product_id": "P1000202025", "goods_no": "G020025", "product_name": "复古牛仔夹克", "seller_id": 10002, "stall_id": 100022, "stock_qty": -5}, {"product_id": "P1000203005", "goods_no": "G030005", "product_name": "复古牛仔夹克", "seller_id": 10002, "stall_id": 100023, "stock_qty": -2}]`
- 实际结果：`[{"snapshot_date": "2026-06-30", "product_id": "P1000101001", "product_name": "春款针织打底", "stock_qty": 460.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101002", "product_name": "羊毛混纺外套", "stock_qty": 51.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101003", "product_name": "轻薄防晒衫", "stock_qty": 354.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101004", "product_name": "高腰阔腿裤", "stock_qty": 387.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101005", "product_name": "复古牛仔夹克", "stock_qty": -21.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101006", "product_name": "纯棉基础短袖", "stock_qty": 144.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101007", "product_name": "通勤西装套装", "stock_qty": 373.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101008", "product_name": "法式碎花连衣裙", "stock_qty": 99.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101009", "product_name": "休闲连帽卫衣", "stock_qty": 372.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101010", "product_name": "简约针织开衫", "stock_qty": 196.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101011", "product_name": "春款针织打底", "stock_qty": 454.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101012", "product_name": "羊毛混纺外套", "stock_qty": 191.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101013", "product_name": "轻薄防晒衫", "stock_qty": 362.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101014", "product_name": "高腰阔腿裤", "stock_qty": 515.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101015", "product_name": "复古牛仔夹克", "stock_qty": 500.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101016", "product_name": "纯棉基础短袖", "stock_qty": 504.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101017", "product_name": "通勤西装套装", "stock_qty": 5.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101018", "product_name": "法式碎花连衣裙", "stock_qty": 478.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101019", "product_name": "休闲连帽卫衣", "stock_qty": 72.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101020", "product_name": "简约针织开衫", "stock_qty": 31.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101021", "product_name": "春款针织打底", "stock_qty": 107.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101022", "product_name": "羊毛混纺外套", "stock_qty": 81.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101023", "product_name": "轻薄防晒衫", "stock_qty": 452.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101024", "product_name": "高腰阔腿裤", "stock_qty": 438.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000101025", "product_name": "复古牛仔夹克", "stock_qty": 418.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102001", "product_name": "春款针织打底", "stock_qty": 240.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102002", "product_name": "羊毛混纺外套", "stock_qty": 494.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102003", "product_name": "轻薄防晒衫", "stock_qty": 279.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102004", "product_name": "高腰阔腿裤", "stock_qty": 453.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102005", "product_name": "复古牛仔夹克", "stock_qty": 245.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102006", "product_name": "纯棉基础短袖", "stock_qty": 581.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102007", "product_name": "通勤西装套装", "stock_qty": 164.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102008", "product_name": "法式碎花连衣裙", "stock_qty": 567.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102009", "product_name": "休闲连帽卫衣", "stock_qty": 3.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102010", "product_name": "简约针织开衫", "stock_qty": 460.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102011", "product_name": "春款针织打底", "stock_qty": 541.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102012", "product_name": "羊毛混纺外套", "stock_qty": 350.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102013", "product_name": "轻薄防晒衫", "stock_qty": 182.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102014", "product_name": "高腰阔腿裤", "stock_qty": 39.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102015", "product_name": "复古牛仔夹克", "stock_qty": 438.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102016", "product_name": "纯棉基础短袖", "stock_qty": 366.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102017", "product_name": "通勤西装套装", "stock_qty": 46.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102018", "product_name": "法式碎花连衣裙", "stock_qty": 426.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102019", "product_name": "休闲连帽卫衣", "stock_qty": 269.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102020", "product_name": "简约针织开衫", "stock_qty": 246.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102021", "product_name": "春款针织打底", "stock_qty": 523.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102022", "product_name": "羊毛混纺外套", "stock_qty": 264.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102023", "product_name": "轻薄防晒衫", "stock_qty": 503.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102024", "product_name": "高腰阔腿裤", "stock_qty": 278.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000102025", "product_name": "复古牛仔夹克", "stock_qty": 547.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103001", "product_name": "春款针织打底", "stock_qty": 416.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103002", "product_name": "羊毛混纺外套", "stock_qty": 67.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103003", "product_name": "轻薄防晒衫", "stock_qty": 113.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103004", "product_name": "高腰阔腿裤", "stock_qty": 517.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103005", "product_name": "复古牛仔夹克", "stock_qty": 3.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103006", "product_name": "纯棉基础短袖", "stock_qty": 374.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103007", "product_name": "通勤西装套装", "stock_qty": 261.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103008", "product_name": "法式碎花连衣裙", "stock_qty": -12.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103009", "product_name": "休闲连帽卫衣", "stock_qty": 0.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103010", "product_name": "简约针织开衫", "stock_qty": 500.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103011", "product_name": "春款针织打底", "stock_qty": 400.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103012", "product_name": "羊毛混纺外套", "stock_qty": 392.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103013", "product_name": "轻薄防晒衫", "stock_qty": 263.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103014", "product_name": "高腰阔腿裤", "stock_qty": 195.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103015", "product_name": "复古牛仔夹克", "stock_qty": 456.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103016", "product_name": "纯棉基础短袖", "stock_qty": 159.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103017", "product_name": "通勤西装套装", "stock_qty": 433.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103018", "product_name": "法式碎花连衣裙", "stock_qty": 292.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103019", "product_name": "休闲连帽卫衣", "stock_qty": 159.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103020", "product_name": "简约针织开衫", "stock_qty": 402.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103021", "product_name": "春款针织打底", "stock_qty": 534.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103022", "product_name": "羊毛混纺外套", "stock_qty": 183.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103023", "product_name": "轻薄防晒衫", "stock_qty": 485.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103024", "product_name": "高腰阔腿裤", "stock_qty": 100.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000103025", "product_name": "复古牛仔夹克", "stock_qty": 516.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201001", "product_name": "春款针织打底", "stock_qty": 233.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201002", "product_name": "羊毛混纺外套", "stock_qty": 432.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201003", "product_name": "轻薄防晒衫", "stock_qty": 175.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201004", "product_name": "高腰阔腿裤", "stock_qty": 388.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201005", "product_name": "复古牛仔夹克", "stock_qty": 544.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201006", "product_name": "纯棉基础短袖", "stock_qty": 351.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201007", "product_name": "通勤西装套装", "stock_qty": 473.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201008", "product_name": "法式碎花连衣裙", "stock_qty": 405.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201009", "product_name": "休闲连帽卫衣", "stock_qty": 26.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201010", "product_name": "简约针织开衫", "stock_qty": 467.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201011", "product_name": "春款针织打底", "stock_qty": 291.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201012", "product_name": "羊毛混纺外套", "stock_qty": 500.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201013", "product_name": "轻薄防晒衫", "stock_qty": 452.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201014", "product_name": "高腰阔腿裤", "stock_qty": 308.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201015", "product_name": "复古牛仔夹克", "stock_qty": 173.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201016", "product_name": "纯棉基础短袖", "stock_qty": 61.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201017", "product_name": "通勤西装套装", "stock_qty": 493.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201018", "product_name": "法式碎花连衣裙", "stock_qty": 62.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201019", "product_name": "休闲连帽卫衣", "stock_qty": 76.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201020", "product_name": "简约针织开衫", "stock_qty": 86.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201021", "product_name": "春款针织打底", "stock_qty": 150.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201022", "product_name": "羊毛混纺外套", "stock_qty": 375.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201023", "product_name": "轻薄防晒衫", "stock_qty": 400.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201024", "product_name": "高腰阔腿裤", "stock_qty": 162.0}, {"snapshot_date": "2026-06-30", "product_id": "P1000201025", "product_name": "复古牛仔夹克", "stock_qty": 313.0}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": false, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "销售商品件数", "value": 266}, {"label": "订货商品件数", "value": 267}, {"label": "总商品件数", "value": 268}, {"label": "订单商品总件数", "value": 277}, {"label": "负库存商品数", "value": 306}, {"label": "连续30天未动销商品数", "value": 307}, {"label": "连续30天未动销商品数", "value": 312}, {"label": "是否负库存", "value": 284}, {"label": "当前库存件数", "value": 280}], "selected_response": {"metric": "280"}}]`

## 11. 连续 30 天未动销的商品有多少个？

- 状态：`failed`
- Run ID：`eval-20q-11-1782878380`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：2
- 预期 SQL：`SELECT COUNT(DISTINCT product_id) AS dormant_product_cnt FROM snap_product_inventory WHERE days_unsold >= 30`
- 实际 SQL：``
- 预期结果：`[{"dormant_product_cnt": 45}]`
- 实际结果：`[]`
- 检查：`{"api_succeeded": false, "intent_type_correct": true, "metric_binding_complete": false, "dimension_binding_complete": true, "table_correct": false, "time_or_filter_present": false, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_slot_clarification", "prompt": "请确认“商品ID”这个维度的使用方式。", "options": [{"label": "按商品ID分组查看", "value": {"dimension": "商品ID", "dimension_usage": "group_by"}}, {"label": "筛选某个具体商品ID", "value": {"dimension": "商品ID", "dimension_usage": "filter_value_required", "dimension_value_fields": ["商品ID", "商品名称"]}}, {"label": "不使用商品ID维度", "value": {"dimension": "商品ID", "dimension_usage": "ignore"}}], "selected_response": {"dimension": "商品ID", "dimension_usage": "group_by"}}, {"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "销售商品件数", "value": 266}, {"label": "订货商品件数", "value": 267}, {"label": "总商品件数", "value": 268}, {"label": "订单商品总件数", "value": 277}, {"label": "负库存商品数", "value": 306}, {"label": "连续30天未动销商品数", "value": 307}, {"label": "连续30天未动销商品数", "value": 312}, {"label": "是否连续30天未动销", "value": 285}], "selected_response": {"metric": "266"}}]`

## 12. 当前库存量最低的 10 个商品是哪些？

- 状态：`succeeded`
- Run ID：`eval-20q-12-1782878415`
- 预期意图：`ranking_analysis`
- 实际意图：`ranking_analysis`
- 交互次数：1
- 预期 SQL：`SELECT product_id, goods_no, product_name, seller_id, stall_id, stock_qty FROM snap_product_inventory ORDER BY stock_qty, product_id LIMIT 10`
- 实际 SQL：`select snap_product_inventory.product_id as product_id, sum(snap_product_inventory.stock_qty) as stock_qty from snap_product_inventory snap_product_inventory group by snap_product_inventory.product_id order by stock_qty asc limit 10`
- 预期结果：`[{"product_id": "P1000101005", "goods_no": "G010005", "product_name": "复古牛仔夹克", "seller_id": 10001, "stall_id": 100011, "stock_qty": -21}, {"product_id": "P1000103008", "goods_no": "G030008", "product_name": "法式碎花连衣裙", "seller_id": 10001, "stall_id": 100013, "stock_qty": -12}, {"product_id": "P1000202025", "goods_no": "G020025", "product_name": "复古牛仔夹克", "seller_id": 10002, "stall_id": 100022, "stock_qty": -5}, {"product_id": "P1000203005", "goods_no": "G030005", "product_name": "复古牛仔夹克", "seller_id": 10002, "stall_id": 100023, "stock_qty": -2}, {"product_id": "P1000103009", "goods_no": "G030009", "product_name": "休闲连帽卫衣", "seller_id": 10001, "stall_id": 100013, "stock_qty": 0}, {"product_id": "P1000102009", "goods_no": "G020009", "product_name": "休闲连帽卫衣", "seller_id": 10001, "stall_id": 100012, "stock_qty": 3}, {"product_id": "P1000103005", "goods_no": "G030005", "product_name": "复古牛仔夹克", "seller_id": 10001, "stall_id": 100013, "stock_qty": 3}, {"product_id": "P1000101017", "goods_no": "G010017", "product_name": "通勤西装套装", "seller_id": 10001, "stall_id": 100011, "stock_qty": 5}, {"product_id": "P1000203024", "goods_no": "G030024", "product_name": "高腰阔腿裤", "seller_id": 10002, "stall_id": 100023, "stock_qty": 16}, {"product_id": "P1000203017", "goods_no": "G030017", "product_name": "通勤西装套装", "seller_id": 10002, "stall_id": 100023, "stock_qty": 21}]`
- 实际结果：`[{"product_id": "P1000101005", "stock_qty": -21.0}, {"product_id": "P1000103008", "stock_qty": -12.0}, {"product_id": "P1000202025", "stock_qty": -5.0}, {"product_id": "P1000203005", "stock_qty": -2.0}, {"product_id": "P1000103009", "stock_qty": 0.0}, {"product_id": "P1000102009", "stock_qty": 3.0}, {"product_id": "P1000103005", "stock_qty": 3.0}, {"product_id": "P1000101017", "stock_qty": 5.0}, {"product_id": "P1000203024", "stock_qty": 16.0}, {"product_id": "P1000203017", "stock_qty": 21.0}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": true, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "当前库存件数", "value": 280}, {"label": "是否负库存", "value": 284}, {"label": "负库存商品数", "value": 306}], "selected_response": {"metric": "280"}}]`

## 13. 2026 年 6 月消费金额最高的 10 位客户是谁？

- 状态：`failed`
- Run ID：`eval-20q-13-1782878448`
- 预期意图：`ranking_analysis`
- 实际意图：`ranking_analysis`
- 交互次数：1
- 预期 SQL：`SELECT customer_id, MAX(customer_name) AS customer_name, ROUND(SUM(customer_gmv), 2) AS customer_gmv FROM fct_customer_trade_daily WHERE stat_date >= '2026-06-01' AND stat_date < '2026-07-01' GROUP BY customer_id ORDER BY customer_gmv DESC, customer_id LIMIT 10`
- 实际 SQL：``
- 预期结果：`[{"customer_id": "C1000203007", "customer_name": "合肥优品", "customer_gmv": 87312.05}, {"customer_id": "C1000202010", "customer_name": "绍兴纺客", "customer_gmv": 86055.86}, {"customer_id": "C1000202002", "customer_name": "新客小周", "customer_gmv": 83984.85}, {"customer_id": "C1000101011", "customer_name": "温州名品", "customer_gmv": 81258.1}, {"customer_id": "C1000103005", "customer_name": "上海风尚", "customer_gmv": 76732.87}, {"customer_id": "C1000201004", "customer_name": "苏州云裳", "customer_gmv": 74808.38}, {"customer_id": "C1000103006", "customer_name": "南京布语", "customer_gmv": 74463.96}, {"customer_id": "C1000202009", "customer_name": "嘉兴衣仓", "customer_gmv": 67196.18}, {"customer_id": "C1000102011", "customer_name": "温州名品", "customer_gmv": 65136.74}, {"customer_id": "C1000103004", "customer_name": "苏州云裳", "customer_gmv": 63434.36}]`
- 实际结果：`[]`
- 检查：`{"api_succeeded": false, "intent_type_correct": true, "metric_binding_complete": false, "dimension_binding_complete": true, "table_correct": false, "time_or_filter_present": true, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "订单金额", "value": 276}, {"label": "逾期欠款金额", "value": 294}], "selected_response": {"metric": "276"}}]`

## 14. 最近 30 天每天的客户 GMV 趋势如何？

- 状态：`failed`
- Run ID：`eval-20q-14-1782878481`
- 预期意图：`trend_analysis`
- 实际意图：`trend_analysis`
- 交互次数：2
- 预期 SQL：`SELECT stat_date, ROUND(SUM(customer_gmv), 2) AS customer_gmv FROM fct_customer_trade_daily WHERE stat_date >= '2026-06-01' AND stat_date <= '2026-06-30' GROUP BY stat_date ORDER BY stat_date`
- 实际 SQL：``
- 预期结果：`[{"stat_date": "2026-06-01", "customer_gmv": 67026.0}, {"stat_date": "2026-06-02", "customer_gmv": 78243.69}, {"stat_date": "2026-06-03", "customer_gmv": 78129.96}, {"stat_date": "2026-06-04", "customer_gmv": 75280.64}, {"stat_date": "2026-06-05", "customer_gmv": 89589.5}, {"stat_date": "2026-06-06", "customer_gmv": 135736.12}, {"stat_date": "2026-06-07", "customer_gmv": 66432.32}, {"stat_date": "2026-06-08", "customer_gmv": 136159.64}, {"stat_date": "2026-06-09", "customer_gmv": 67186.8}, {"stat_date": "2026-06-10", "customer_gmv": 88070.91}, {"stat_date": "2026-06-11", "customer_gmv": 100349.7}, {"stat_date": "2026-06-12", "customer_gmv": 123554.1}, {"stat_date": "2026-06-13", "customer_gmv": 100126.6}, {"stat_date": "2026-06-14", "customer_gmv": 74112.18}, {"stat_date": "2026-06-15", "customer_gmv": 80275.38}, {"stat_date": "2026-06-16", "customer_gmv": 113013.95}, {"stat_date": "2026-06-17", "customer_gmv": 103924.4}, {"stat_date": "2026-06-18", "customer_gmv": 99526.05}, {"stat_date": "2026-06-19", "customer_gmv": 111319.72}, {"stat_date": "2026-06-20", "customer_gmv": 120524.68}, {"stat_date": "2026-06-21", "customer_gmv": 88305.6}, {"stat_date": "2026-06-22", "customer_gmv": 65603.78}, {"stat_date": "2026-06-23", "customer_gmv": 148549.64}, {"stat_date": "2026-06-24", "customer_gmv": 102126.9}, {"stat_date": "2026-06-25", "customer_gmv": 96876.24}, {"stat_date": "2026-06-26", "customer_gmv": 151490.61}, {"stat_date": "2026-06-27", "customer_gmv": 71938.13}, {"stat_date": "2026-06-28", "customer_gmv": 141225.27}, {"stat_date": "2026-06-29", "customer_gmv": 99194.88}, {"stat_date": "2026-06-30", "customer_gmv": 122832.51}]`
- 实际结果：`[]`
- 检查：`{"api_succeeded": false, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": false, "time_or_filter_present": true, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_slot_clarification", "prompt": "请确认“客户ID”这个维度的使用方式。", "options": [{"label": "按客户ID分组查看", "value": {"dimension": "客户ID", "dimension_usage": "group_by"}}, {"label": "筛选某个具体客户ID", "value": {"dimension": "客户ID", "dimension_usage": "filter_value_required", "dimension_value_fields": ["客户ID", "客户名称"]}}, {"label": "不使用客户ID维度", "value": {"dimension": "客户ID", "dimension_usage": "ignore"}}], "selected_response": {"dimension": "客户ID", "dimension_usage": "group_by"}}, {"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "销售下单客户数", "value": 272}, {"label": "订货下单客户数", "value": 273}, {"label": "总下单客户数", "value": 274}, {"label": "客户当日GMV", "value": 286}, {"label": "客户当日订单数", "value": 287}, {"label": "客户当日购买件数", "value": 288}, {"label": "新增成交客户数", "value": 308}, {"label": "逾期客户数", "value": 309}, {"label": "欠款客户数", "value": 310}, {"label": "新增成交客户数", "value": 313}, {"label": "销售类GMV", "value": 269}, {"label": "订货类GMV", "value": 270}, {"label": "总GMV", "value": 271}], "selected_response": {"metric": "286"}}]`

## 15. 各档口的新增成交客户数分别是多少？

- 状态：`failed`
- Run ID：`eval-20q-15-1782878508`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：1
- 预期 SQL：`SELECT stall_id, COUNT(DISTINCT customer_id) AS new_customer_cnt FROM fct_customer_trade_daily WHERE is_first_deal = 1 GROUP BY stall_id ORDER BY stall_id`
- 实际 SQL：``
- 预期结果：`[{"stall_id": 100011, "new_customer_cnt": 12}, {"stall_id": 100012, "new_customer_cnt": 12}, {"stall_id": 100013, "new_customer_cnt": 12}, {"stall_id": 100021, "new_customer_cnt": 12}, {"stall_id": 100022, "new_customer_cnt": 12}, {"stall_id": 100023, "new_customer_cnt": 12}]`
- 实际结果：`[]`
- 检查：`{"api_succeeded": false, "intent_type_correct": true, "metric_binding_complete": false, "dimension_binding_complete": true, "table_correct": false, "time_or_filter_present": false, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "新增成交客户数", "value": 308}, {"label": "新增成交客户数", "value": 313}, {"label": "销售下单客户数", "value": 272}, {"label": "订货下单客户数", "value": 273}, {"label": "总下单客户数", "value": 274}, {"label": "客户当日GMV", "value": 286}, {"label": "客户当日订单数", "value": 287}, {"label": "客户当日购买件数", "value": 288}, {"label": "是否首单成交", "value": 289}, {"label": "逾期客户数", "value": 309}, {"label": "欠款客户数", "value": 310}], "selected_response": {"metric": "308"}}]`

## 16. 线上和线下渠道的 GMV、订单数分别是多少？

- 状态：`succeeded`
- Run ID：`eval-20q-16-1782878524`
- 预期意图：`comparison_analysis, metric_query`
- 实际意图：`metric_query`
- 交互次数：1
- 预期 SQL：`SELECT channel_type, ROUND(SUM(customer_gmv), 2) AS customer_gmv, SUM(order_cnt) AS order_cnt FROM fct_customer_trade_daily GROUP BY channel_type ORDER BY channel_type`
- 实际 SQL：`select fct_customer_trade_daily.channel_type as channel_type, sum(fct_customer_trade_daily.customer_gmv) as customer_gmv from fct_customer_trade_daily fct_customer_trade_daily group by fct_customer_trade_daily.channel_type limit 100`
- 预期结果：`[{"channel_type": "线上", "customer_gmv": 2288266.68, "order_cnt": 964.0}, {"channel_type": "线下", "customer_gmv": 708459.22, "order_cnt": 368.0}]`
- 实际结果：`[{"channel_type": "线下", "customer_gmv": 708459.22}, {"channel_type": "线上", "customer_gmv": 2288266.68}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": false, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": true, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "销售订单数", "value": 263}, {"label": "订货订单数", "value": 264}, {"label": "总订单数", "value": 265}, {"label": "销售类GMV", "value": 269}, {"label": "订货类GMV", "value": 270}, {"label": "总GMV", "value": 271}, {"label": "客户当日GMV", "value": 286}, {"label": "客户当日订单数", "value": 287}, {"label": "未发订单数", "value": 304}, {"label": "超时未发订单数", "value": 305}, {"label": "超时未发订单数", "value": 311}, {"label": "销售订单平均客单价", "value": 275}, {"label": "订货订单平均客单价", "value": 302}, {"label": "订单平均客单价", "value": 303}, {"label": "订单金额", "value": 276}, {"label": "订单商品总件数", "value": 277}], "selected_response": {"metric": "286"}}]`

## 17. 当前客户欠款总金额是多少？

- 状态：`failed`
- Run ID：`eval-20q-17-1782878564`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：2
- 预期 SQL：`SELECT ROUND(SUM(arrears_amt), 2) AS arrears_amt FROM snap_customer_arrears`
- 实际 SQL：``
- 预期结果：`[{"arrears_amt": 3295938.0}]`
- 实际结果：`[]`
- 检查：`{"api_succeeded": false, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": false, "time_or_filter_present": true, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_slot_clarification", "prompt": "请确认“客户ID”这个维度的使用方式。", "options": [{"label": "按客户ID分组查看", "value": {"dimension": "客户ID", "dimension_usage": "group_by"}}, {"label": "筛选某个具体客户ID", "value": {"dimension": "客户ID", "dimension_usage": "filter_value_required", "dimension_value_fields": ["客户ID", "客户名称"]}}, {"label": "不使用客户ID维度", "value": {"dimension": "客户ID", "dimension_usage": "ignore"}}], "selected_response": {"dimension": "客户ID", "dimension_usage": "group_by"}}, {"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "销售下单客户数", "value": 272}, {"label": "订货下单客户数", "value": 273}, {"label": "总下单客户数", "value": 274}, {"label": "订单金额", "value": 276}, {"label": "客户当日GMV", "value": 286}, {"label": "客户当日订单数", "value": 287}, {"label": "客户当日购买件数", "value": 288}, {"label": "逾期欠款金额", "value": 294}, {"label": "当前欠款余额", "value": 291}, {"label": "当前未结清欠款笔数", "value": 292}], "selected_response": {"metric": "291"}}]`

## 18. 当前有多少位客户已经逾期？

- 状态：`succeeded`
- Run ID：`eval-20q-18-1782878593`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：1
- 预期 SQL：`SELECT COUNT(DISTINCT customer_id) AS overdue_customer_cnt FROM snap_customer_arrears WHERE is_overdue = 1`
- 实际 SQL：`select snap_customer_arrears.snapshot_date as snapshot_date, snap_customer_arrears.customer_id as customer_id, COUNT(DISTINCT CASE WHEN overdue_amt > 0 THEN customer_id END) as overdue_customer_cnt from snap_customer_arrears snap_customer_arrears group by snap_customer_arrears.snapshot_date, snap_customer_arrears.customer_id limit 100`
- 预期结果：`[{"overdue_customer_cnt": 30}]`
- 实际结果：`[{"snapshot_date": "2026-06-30", "customer_id": "C1000101002", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000101003", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000101004", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000101005", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000101007", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000101008", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000101009", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000101010", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000101012", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000102004", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000102005", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000102007", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000102009", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000102011", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000102012", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103001", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103002", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103003", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103006", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103008", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103009", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103010", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103011", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000103012", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201002", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201003", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201004", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201005", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201006", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201007", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201008", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201009", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201011", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000201012", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000202001", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000202003", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000202004", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000202005", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000202006", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000202008", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000202010", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203002", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203004", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203006", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203007", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203008", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203009", "overdue_customer_cnt": 1}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203010", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203011", "overdue_customer_cnt": 0}, {"snapshot_date": "2026-06-30", "customer_id": "C1000203012", "overdue_customer_cnt": 1}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": true, "dimension_binding_complete": true, "table_correct": true, "time_or_filter_present": false, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_slot_clarification", "prompt": "请确认“客户ID”这个维度的使用方式。", "options": [{"label": "按客户ID分组查看", "value": {"dimension": "客户ID", "dimension_usage": "group_by"}}, {"label": "筛选某个具体客户ID", "value": {"dimension": "客户ID", "dimension_usage": "filter_value_required", "dimension_value_fields": ["客户ID"]}}, {"label": "不使用客户ID维度", "value": {"dimension": "客户ID", "dimension_usage": "ignore"}}], "selected_response": {"dimension": "客户ID", "dimension_usage": "group_by"}}]`

## 19. 欠款金额最高的 10 位客户是谁？

- 状态：`succeeded`
- Run ID：`eval-20q-19-1782878638`
- 预期意图：`ranking_analysis`
- 实际意图：`ranking_analysis`
- 交互次数：0
- 预期 SQL：`SELECT customer_id, customer_name, arrears_amt FROM snap_customer_arrears ORDER BY arrears_amt DESC, customer_id LIMIT 10`
- 实际 SQL：`select snap_customer_arrears.customer_name as customer_name, sum(snap_customer_arrears.overdue_amt) as overdue_amt from snap_customer_arrears snap_customer_arrears group by snap_customer_arrears.customer_name order by overdue_amt desc limit 10`
- 预期结果：`[{"customer_id": "C1000101008", "customer_name": "宁波潮集", "arrears_amt": 119430.0}, {"customer_id": "C1000103008", "customer_name": "宁波潮集", "arrears_amt": 118781.0}, {"customer_id": "C1000101012", "customer_name": "义乌精选", "arrears_amt": 118655.0}, {"customer_id": "C1000101003", "customer_name": "杭州衣阁", "arrears_amt": 117995.0}, {"customer_id": "C1000103012", "customer_name": "义乌精选", "arrears_amt": 115434.0}, {"customer_id": "C1000201007", "customer_name": "合肥优品", "arrears_amt": 114707.0}, {"customer_id": "C1000201005", "customer_name": "上海风尚", "arrears_amt": 110975.0}, {"customer_id": "C1000201009", "customer_name": "嘉兴衣仓", "arrears_amt": 109571.0}, {"customer_id": "C1000102011", "customer_name": "温州名品", "arrears_amt": 108688.0}, {"customer_id": "C1000102007", "customer_name": "合肥优品", "arrears_amt": 106314.0}]`
- 实际结果：`[{"customer_name": "嘉兴衣仓", "overdue_amt": 280933.49}, {"customer_name": "合肥优品", "overdue_amt": 208398.32}, {"customer_name": "苏州云裳", "overdue_amt": 187249.13}, {"customer_name": "南京布语", "overdue_amt": 181378.58}, {"customer_name": "义乌精选", "overdue_amt": 137448.69}, {"customer_name": "杭州衣阁", "overdue_amt": 117984.19}, {"customer_name": "上海风尚", "overdue_amt": 104933.57}, {"customer_name": "绍兴纺客", "overdue_amt": 76466.98}, {"customer_name": "宁波潮集", "overdue_amt": 44518.01}, {"customer_name": "华北大客", "overdue_amt": 34629.71}]`
- 检查：`{"api_succeeded": true, "intent_type_correct": true, "metric_binding_complete": false, "dimension_binding_complete": false, "table_correct": true, "time_or_filter_present": true, "result_equal": false, "overall": false}`

## 20. 各档口的欠款金额、逾期金额和欠款客户数分别是多少？

- 状态：`failed`
- Run ID：`eval-20q-20-1782878673`
- 预期意图：`metric_query`
- 实际意图：`metric_query`
- 交互次数：1
- 预期 SQL：`SELECT stall_id, ROUND(SUM(arrears_amt), 2) AS arrears_amt, ROUND(SUM(overdue_amt), 2) AS overdue_amt, COUNT(DISTINCT customer_id) AS arrears_customer_cnt FROM snap_customer_arrears GROUP BY stall_id ORDER BY stall_id`
- 实际 SQL：``
- 预期结果：`[{"stall_id": 100011, "arrears_amt": 726830.0, "overdue_amt": 370179.45, "arrears_customer_cnt": 9}, {"stall_id": 100012, "arrears_amt": 426912.0, "overdue_amt": 61749.58, "arrears_customer_cnt": 6}, {"stall_id": 100013, "arrears_amt": 673074.0, "overdue_amt": 225517.34, "arrears_customer_cnt": 9}, {"stall_id": 100021, "arrears_amt": 719581.0, "overdue_amt": 434047.22, "arrears_customer_cnt": 10}, {"stall_id": 100022, "arrears_amt": 265996.0, "overdue_amt": 119577.71, "arrears_customer_cnt": 7}, {"stall_id": 100023, "arrears_amt": 483545.0, "overdue_amt": 175179.45, "arrears_customer_cnt": 9}]`
- 实际结果：`[]`
- 检查：`{"api_succeeded": false, "intent_type_correct": true, "metric_binding_complete": false, "dimension_binding_complete": true, "table_correct": false, "time_or_filter_present": true, "result_equal": false, "overall": false}`
- 交互：`[{"node_name": "ask_metric_selection", "prompt": "请选择要分析的指标。", "options": [{"label": "逾期欠款金额", "value": 294}, {"label": "欠款客户数", "value": 310}, {"label": "销售下单客户数", "value": 272}, {"label": "订货下单客户数", "value": 273}, {"label": "总下单客户数", "value": 274}, {"label": "订单金额", "value": 276}, {"label": "客户当日GMV", "value": 286}, {"label": "客户当日订单数", "value": 287}, {"label": "客户当日购买件数", "value": 288}, {"label": "新增成交客户数", "value": 308}, {"label": "逾期客户数", "value": 309}, {"label": "当前欠款余额", "value": 291}, {"label": "当前未结清欠款笔数", "value": 292}, {"label": "是否逾期", "value": 293}], "selected_response": {"metric": "291"}}]`
