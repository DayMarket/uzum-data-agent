# Реестр витрин

> Источник строк — выгрузка покрытия по ядру OE (Superset API, 2026-07-17):
> таблицы вынуты из тела SQL датасетов, а не из их имён — из 547 датасетов
> физических таблиц лишь 24, остальные виртуальные. Колонки «доверие» и
> «комментарий» заполняются руками — это суждение, а не факт из каталога, и
> при пересборке они сохраняются.
>
> «Дашбордов» — на скольких дашбордах Superset ядра OE эта таблица
> используется. «Кто строит» — аналитики, у чьих дашбордов она под капотом;
> это не владелец витрины, а тот, кого имеет смысл спросить.
>
> Витрины нет в списке — это не запрет ею пользоваться: список покрывает
> Superset ядра OE, а не весь DWH. Напиши об этом в результате (см. скилл
> `data-sanity`) и проверь таблицу запросом.
>
> **Доверие «🚫 запрещено — правило 1»** — зарплатные данные. Правило 1 в
> `CLAUDE.md` и `context/rules.md` запрещает их трогать вообще: ни выгрузок,
> ни агрегатов, ни «просто посмотреть». Витрина остаётся в реестре, чтобы было
> видно, что она существует и почему агент к ней не идёт — но задачу, которая
> её касается, агент останавливает и отдаёт человеку, а не выполняет в обход
> (см. скиллы `adhoc-export`, `data-check`, `data-sanity`).

| Витрина | Дашбордов | Кто строит | Доверие | Комментарий |
|---|--:|---|---|---|
| bronze.accepted_sku_cell | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.accepted_sku_item | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 |  |  |
| bronze.assembly_packed | 2 | Valerii Merkulov, Pavel Sokal, Yevgeniy Atroshenko + ещё 1 |  |  |
| bronze.cargo_place_events_v2 | 1 | Ramil Gilfanov |  |  |
| bronze.cc_hire_funnel_kpi | 1 | Rosina Karimova |  |  |
| bronze.cc_hire_funnel_kpi_targets | 1 | Rosina Karimova |  |  |
| bronze.cc_hire_funnel_raw | 1 | Rosina Karimova |  |  |
| bronze.cc_hire_funnel_study | 1 | Rosina Karimova |  |  |
| bronze.clickstream_events | 3 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 |  |  |
| bronze.compensation_info_google_sheet_transfer | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.daily_gmv | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.delivery_db_delivery_point | 1 | Yevgeniy Atroshenko, Denis Platon |  |  |
| bronze.invoice_data_for_balance | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 |  |  |
| bronze.invoice_events | 1 | Nikita Lyubchenko |  |  |
| bronze.missing_history | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 |  |  |
| bronze.missing_recovery_history | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 |  |  |
| bronze.movement_sku_item_history | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.one_c_shortage_compensation | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.ops_wms_assembly_events_v2 | 2 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.order_return_v2 | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.pvz_hire_funnel | 1 | Rosina Karimova |  |  |
| bronze.pvz_hire_funnel_study | 1 | Rosina Karimova |  |  |
| bronze.seller_return_completed | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.seller_return_utilization | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.sku_item_history_events_v2 | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 |  |  |
| bronze.stock_balance | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| bronze.study_csat_raw | 1 | Rosina Karimova |  |  |
| bronze.ticket_changes | 2 | Rosina Karimova |  |  |
| clickstream_b2b.events | 5 | Rida Zabirova |  |  |
| clickstream_b2b.sessions | 1 | Rida Zabirova |  |  |
| dict.account | 12 | Rida Zabirova, Pavel Sokal, Rosina Karimova + ещё 4 |  |  |
| dict.category | 12 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 |  |  |
| dict.cell | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 |  |  |
| dict.city | 17 | Rida Zabirova, Pavel Sokal, Kristina Silina + ещё 5 |  |  |
| dict.currency_rates | 1 | Rida Zabirova |  |  |
| dict.delivery_delivery_point | 1 | Valerii Merkulov, Ramil Gilfanov |  |  |
| dict.delivery_point | 15 | Rida Zabirova, Kristina Silina, Artem Voronov + ещё 4 |  |  |
| dict.department | 1 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov |  |  |
| dict.department_history_work_structure | 1 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov |  |  |
| dict.drop_off_point | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| dict.drop_off_point_timeslot | 1 | Rida Zabirova |  |  |
| dict.fbs_seller_lock | 1 | Rida Zabirova |  |  |
| dict.fbs_seller_lock_history | 1 | Rida Zabirova |  |  |
| dict.flight_delivery_item | 1 | Yevgeniy Atroshenko, Denis Platon |  |  |
| dict.geo_h3_to_territories | 2 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| dict.geo_territories | 2 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| dict.invoice_sku | 1 | Rida Zabirova |  |  |
| dict.ops_logistics_delivery_orders | 4 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 |  |  |
| dict.ops_logistics_pallet | 1 | Kristina Silina, Yevgeniy Atroshenko |  |  |
| dict.order_return | 3 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 |  |  |
| dict.orgchart | 8 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov + ещё 5 |  |  |
| dict.place | 1 | Ramil Gilfanov |  |  |
| dict.product | 13 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 3 |  |  |
| dict.promo | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko + ещё 1 |  |  |
| dict.seller | 7 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 3 |  |  |
| dict.seller_personal_data | 9 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 3 |  |  |
| dict.seller_return_drop | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| dict.shipment | 1 | Yevgeniy Atroshenko, Denis Platon |  |  |
| dict.shipment_cargo_place | 1 | Kristina Silina, Yevgeniy Atroshenko |  |  |
| dict.shipment_delivery_item | 2 | Kristina Silina, Yevgeniy Atroshenko, Denis Platon |  |  |
| dict.shipment_delivery_point | 4 | Ilya Kadochnikov, Anton Bykov, Aleksandra Petrukhina + ещё 2 |  |  |
| dict.shop | 6 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 3 |  |  |
| dict.sku | 16 | Rida Zabirova, Pavel Sokal, Kristina Silina + ещё 4 |  |  |
| dict.stock | 2 | Pavel Sokal, Yevgeniy Atroshenko, Denis Platon + ещё 1 |  |  |
| dict.stock_uuid_pk | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| dict.wave | 1 | Pavel Sokal |  |  |
| dict.wms_assembly | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| dict.wms_orders_v2 | 4 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 |  |  |
| dict.wms_packed_unit | 1 | Kristina Silina, Yevgeniy Atroshenko |  |  |
| dict.wms_wms_order | 2 | Kristina Silina, Yevgeniy Atroshenko, Denis Platon |  |  |
| dict.zone | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 |  |  |
| gold.active_sku_detailed | 1 | Rida Zabirova |  |  |
| gold.adrev_active_bids_statistic_daily | 1 | Rida Zabirova |  |  |
| gold.attraction_sellers | 1 | Rida Zabirova |  |  |
| gold.csi_customers_responses | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina |  |  |
| gold.currency_rates | 4 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 2 |  |  |
| gold.daily_sellers_metrics | 1 | Rida Zabirova |  |  |
| gold.geo_distance_metric_hist | 1 | Ilya Kadochnikov, Ivan Ilichev, Aleksandra Petrukhina |  |  |
| gold.geo_dq_checks_hist | 1 | Ilya Kadochnikov, Ivan Ilichev, Aleksandra Petrukhina |  |  |
| gold.geo_dq_hist | 1 | Ilya Kadochnikov, Ivan Ilichev, Aleksandra Petrukhina |  |  |
| gold.growthbook_clickhouse_migration_inventory | 1 | Anton Bykov |  |  |
| gold.growthbook_configuration_state | 1 | Anton Bykov |  |  |
| gold.installs_sellers_funnel | 5 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| gold.joom_financial_measures | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko + ещё 1 |  |  |
| gold.joom_orders | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.meta_sku_id | 1 | Rida Zabirova |  |  |
| gold.new_sku_on_stock | 2 | Rida Zabirova |  |  |
| gold.oos_by_products_data | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.order_items | 2 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| gold.product_creation_time | 1 | Rida Zabirova |  |  |
| gold.product_funnel_weekly | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.pvz_funnel | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| gold.pvz_integral_rating | 1 | Rida Zabirova |  |  |
| gold.query_conversions_weekly | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.sellers_lk_visits | 1 | Rida Zabirova |  |  |
| gold.sellers_partners_metrics_last | 1 | Rida Zabirova |  |  |
| gold.sellers_partners_united_stock | 1 | Rida Zabirova |  |  |
| gold.sellers_sales | 1 | Rida Zabirova |  |  |
| gold.skus_filters | 1 | Rida Zabirova |  |  |
| gold.stock_with_sales_info | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.users_daily_by_auth | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.users_daily_by_platform | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.weekly_region_funnel | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.weekly_retention | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.weekly_user_funnel_by_l1_categories | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.weekly_user_funnel_by_l2_categories | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| gold.weekly_users | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| golden.account_work_structure | 3 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov |  |  |
| golden.courier_order_items | 2 | Yevgeniy Atroshenko, Denis Platon |  |  |
| golden.dp_dict_extended_hist | 5 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 |  |  |
| golden.drop_off_type_list | 4 | Ilya Kadochnikov, Anton Bykov, Aleksandra Petrukhina + ещё 2 |  |  |
| golden.efficiency_mart | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | доверяем | основной источник производительности |
| golden.extended_incidents | 2 | Valerii Merkulov, Pavel Sokal, Yevgeniy Atroshenko + ещё 1 |  |  |
| golden.hrops_main_metrics | 0 |  | с оговоркой | всегда дедуплицируй по worker_id |
| golden.nasiya_marts_reports_rep_pvz_stat | 1 | Rida Zabirova, Yevgeniy Atroshenko |  |  |
| golden.nearest_timeslot_hist | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| golden.order_return_from_assembly | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| golden.order_timeline | 4 | Ilya Kadochnikov, Anton Bykov, Aleksandra Petrukhina + ещё 2 |  |  |
| golden.orgchart | 4 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov |  |  |
| golden.routes_data | 4 | Ilya Kadochnikov, Anton Bykov, Aleksandra Petrukhina + ещё 2 |  |  |
| golden.salary_dashboard_table | 1 | Rosina Karimova | 🚫 запрещено — правило 1 | Зарплатные данные, агент не трогает — см. `CLAUDE.md`, правило 1 |
| golden.salary_norms_for_productivity | 3 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | 🚫 запрещено — правило 1 | Зарплатные данные, агент не трогает — см. `CLAUDE.md`, правило 1 |
| golden.sku_balance_v2 | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| golden.table_by_okz_efficiency_selection | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon |  |  |
| golden.table_by_okz_efficiency_sort | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon |  |  |
| golden.table_by_okz_efficiency_sort_2 | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon |  |  |
| golden.vchl_employee_metrics_monthly | 3 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 |  |  |
| golden.warehouse_working_hours | 1 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov |  |  |
| golden.workers_salary_by_process | 3 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | 🚫 запрещено — правило 1 | Зарплатные данные, агент не трогает — см. `CLAUDE.md`, правило 1 |
| golden.workers_workdays_absence | 1 | Rosina Karimova, Anton Bykov, Denis Platon |  |  |
| marketing.cohort_mart | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marketing.daily_metrics_plan_fact | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marketing.daily_plan_fact | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marketing.orders_with_attribution | 2 | Kristina Silina, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| marketing.pepsi_gifts_data | 1 | Rida Zabirova, Yevgeniy Atroshenko |  |  |
| marketing.promo_product_dict_days | 1 | Rida Zabirova, Yevgeniy Atroshenko |  |  |
| marketing.promo_product_metrics | 1 | Rida Zabirova |  |  |
| marketing.promo_products | 3 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 2 |  |  |
| marts.account_dict | 3 | Rida Zabirova, Yevgeniy Atroshenko |  |  |
| marts.appfollow_reviews | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.business_support_metrics | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.category_dict | 1 | Rida Zabirova |  |  |
| marts.city_dict | 2 | Kristina Silina, Yevgeniy Atroshenko, Denis Platon |  |  |
| marts.counted_placed_inbound_info | 1 | Ilya Kadochnikov |  |  |
| marts.courier_quality_check | 1 | Yevgeniy Atroshenko |  |  |
| marts.currency_rates_official | 1 | Rida Zabirova |  |  |
| marts.daily_product_funnel | 2 | Rida Zabirova |  |  |
| marts.daily_sku_quantity_eod | 6 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| marts.daily_sku_quantity_eod_extended | 2 | Rida Zabirova |  |  |
| marts.delivery_point_dict | 9 | Rida Zabirova, Pavel Sokal, Kristina Silina + ещё 4 |  |  |
| marts.dispatch_pivot_by_directions_info | 1 | Ilya Kadochnikov |  |  |
| marts.dispatch_pivot_info | 1 | Ilya Kadochnikov |  |  |
| marts.dispatch_pivot_info_stock_names | 1 | Ilya Kadochnikov |  |  |
| marts.dp_dict_extended_hist | 8 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 |  |  |
| marts.dp_logistics_sla | 1 | Yevgeniy Atroshenko, Denis Platon |  |  |
| marts.external_reviews | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.feedback | 5 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| marts.franchise_plan_by_segment | 1 | Rida Zabirova, Yevgeniy Atroshenko |  |  |
| marts.google_delivery_point | 4 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 |  |  |
| marts.google_dp_reviews | 4 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 |  |  |
| marts.hotmaps_reviews | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.invoice | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| marts.invoices_photostudio | 1 | Rida Zabirova |  |  |
| marts.low_prices_guarantee_sku_level_stats | 1 | Rida Zabirova |  |  |
| marts.master_seller_id_final | 1 | Rida Zabirova |  |  |
| marts.matrix_selling_sku_in_stock | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.metrics_for_weekly_report | 3 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko + ещё 1 |  |  |
| marts.oos_analysis | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.operation_backlog | 1 | Ilya Kadochnikov |  |  |
| marts.order_items | 18 | Rida Zabirova, Kristina Silina, Artem Voronov + ещё 4 |  |  |
| marts.partner_dp_list | 1 | Rida Zabirova, Yevgeniy Atroshenko |  |  |
| marts.partner_reward_costs | 1 | Rida Zabirova, Yevgeniy Atroshenko |  |  |
| marts.plan_fact_sheet | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.product_moderation | 1 | Rida Zabirova |  |  |
| marts.product_moderation_log | 2 | Rida Zabirova |  |  |
| marts.product_moderation_queue | 2 | Rida Zabirova |  |  |
| marts.product_original_flag | 1 | Rida Zabirova |  |  |
| marts.return_items | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.rk_assortment_report_seller_list | 1 | Rida Zabirova |  |  |
| marts.seller_manager | 1 | Rida Zabirova |  |  |
| marts.sellers_info | 7 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| marts.sku_dict | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| marts.tagged_reviews | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.uncollected_orders | 1 | Ilya Kadochnikov |  |  |
| marts.user_daily_metrics | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts.yandex_dp_reviews | 4 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 |  |  |
| marts_b2b.sx_daily_marts | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts_b2c.all_limits_nasiya_dau | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 |  |  |
| marts_b2c.apidb_ke_delivery_point | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| marts_b2c.apidb_ke_sku | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon |  |  |
| marts_b2c.experiment_metrics_weekly | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts_b2c.finance_margin_by_order_item | 14 | Rida Zabirova, Pavel Sokal, Artem Voronov + ещё 5 |  |  |
| marts_b2c.finance_margin_stable_with_corrections | 2 | Rida Zabirova, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts_b2c.nasiya_user_daily_metrics | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| marts_b2c.uzumcard_user_daily_metrics | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| matching.competitors_data | 1 | Anton Bykov |  |  |
| public.dag | 1 | Anton Bykov |  |  |
| public.dag_run | 1 | Anton Bykov |  |  |
| public.delivery_point | 1 | Valerii Merkulov, Ramil Gilfanov |  |  |
| public.post_paid_restrict | 1 | Ilya Kadochnikov |  |  |
| public.sku | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| sandbox.daily_balance | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| sandbox.exit_interview_results | 1 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov |  |  |
| sandbox.franchise_lk_analytics_events_detail | 1 | Ivan Ilichev |  |  |
| sandbox.oe_3268_pvz_client_cogorts_grouped | 1 | Ivan Ilichev |  |  |
| sandbox.workers_mistakes_in_lk | 2 | Rosina Karimova |  |  |
| silver.account_workday | 1 | Rosina Karimova |  |  |
| silver.account_workday_clean | 3 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov |  |  |
| silver.airflow_dag | 1 | Anton Bykov |  |  |
| silver.airflow_dag_run | 1 | Anton Bykov |  |  |
| silver.airflow_log | 1 | Anton Bykov |  |  |
| silver.airflow_task_instance_history | 1 | Anton Bykov |  |  |
| silver.assembly_packed | 1 | Valerii Merkulov, Ramil Gilfanov |  |  |
| silver.bitrix_contacts_icb | 1 | Rida Zabirova |  |  |
| silver.bitrix_pvz_leads | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| silver.bitrix_pvz_stages | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| silver.calendar | 1 | Anton Bykov |  |  |
| silver.ci_llm_review_log | 1 | Anton Bykov |  |  |
| silver.ci_merge_log | 1 | Anton Bykov |  |  |
| silver.clickstream_my_penalties_events | 1 | Rosina Karimova, Anton Bykov, Denis Platon |  |  |
| silver.clickstream_worker_profile_events | 2 | Rosina Karimova |  |  |
| silver.compensation_price | 3 | Valerii Merkulov, Pavel Sokal, Ilya Kadochnikov + ещё 4 |  |  |
| silver.customer_oe_not_picked_up_order_survey_result | 1 | Ilya Kadochnikov |  |  |
| silver.daily_seller_return_utilization | 2 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| silver.daily_stock_balance | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| silver.delivery_item_events | 1 | Kristina Silina, Yevgeniy Atroshenko |  |  |
| silver.delivery_point_orders_daily | 1 | Kristina Silina, Yevgeniy Atroshenko |  |  |
| silver.dict_seller | 1 | Rida Zabirova |  |  |
| silver.dp_location_scoring | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| silver.dpa_metrics | 1 | Anton Bykov |  |  |
| silver.dpa_metrics_inventory | 1 | Anton Bykov |  |  |
| silver.dpa_metrics_new_specs | 1 | Anton Bykov |  |  |
| silver.dpa_metrics_new_topics | 1 | Anton Bykov |  |  |
| silver.dynrouting_next_day_pvz_planned | 1 | Kristina Silina |  |  |
| silver.growthbook_datasources | 1 | Anton Bykov |  |  |
| silver.growthbook_dimensions | 1 | Anton Bykov |  |  |
| silver.growthbook_fact_metrics | 1 | Anton Bykov |  |  |
| silver.growthbook_fact_tables | 1 | Anton Bykov |  |  |
| silver.growthbook_metrics | 1 | Anton Bykov |  |  |
| silver.growthbook_segments | 1 | Anton Bykov |  |  |
| silver.hire_funnel | 1 | Rosina Karimova |  |  |
| silver.iceberg_table_metrics | 1 | Anton Bykov |  |  |
| silver.incident_logs | 1 | Valerii Merkulov, Ramil Gilfanov |  |  |
| silver.incident_tables | 1 | Valerii Merkulov, Ramil Gilfanov |  |  |
| silver.logistics_hire_funnel | 1 | Rosina Karimova |  |  |
| silver.logistics_schedule_last_mile_hist | 2 | Kristina Silina, Yevgeniy Atroshenko, Denis Platon |  |  |
| silver.marketing_daily_crm_cdp_stats | 1 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina |  |  |
| silver.matching_total_stats | 1 | Rida Zabirova |  |  |
| silver.mlgrowth_cannibalisation_prediction | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| silver.order_items | 2 | Valerii Merkulov, Kristina Silina, Ilya Kadochnikov + ещё 2 |  |  |
| silver.potentially_missing | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov |  |  |
| silver.preliminary_cm2_by_order_item | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko |  |  |
| silver.product | 1 | Rida Zabirova |  |  |
| silver.product_moderation_session | 1 | Rida Zabirova |  |  |
| silver.product_moderation_session_event | 1 | Rida Zabirova |  |  |
| silver.pvz_employee_day_agg | 1 | Kristina Silina, Yevgeniy Atroshenko |  |  |
| silver.pvz_map_best_points_export | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 |  |  |
| silver.pvz_processes_nonoverlap | 1 | Kristina Silina, Yevgeniy Atroshenko |  |  |
| silver.sku | 3 | Rida Zabirova, Ilya Kadochnikov |  |  |
| silver.sku_dict | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon |  |  |
| silver.survey_no_show_funnel_daily | 1 | Ilya Kadochnikov |  |  |
| silver.trino_queries | 1 | Anton Bykov |  |  |
| silver.wms_order_public_order_return_ice | 1 | Valerii Merkulov, Ramil Gilfanov |  |  |
| silver.wms_order_public_order_return_item_ice | 1 | Valerii Merkulov, Ramil Gilfanov |  |  |
| silver.workers_csat | 1 | Rosina Karimova |  |  |
