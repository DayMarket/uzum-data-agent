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
>
> **Кластер** — на каком из двух коннекторов ClickHouse искать таблицу:
> `WMS` (коннектор `clickhouse-wms`, склад и операционная аналитика) или
> `DWH` (коннектор `clickhouse-dwh`, продажи/финансы/маркетинг). Базы
> `golden`, `bronze` и им подобные бывают только на складском кластере, `gold`,
> `marts*`, `marketing` и им подобные — только на общем; это проверено списком
> баз обоих кластеров. Базы `dict`, `silver` и `sandbox` есть на обоих
> кластерах и по имени базы не разводятся — здесь каждая такая строка сверена
> отдельно, запросом к `system.tables` на обоих кластерах разом:
>   - `WMS, DWH` — таблица с таким именем есть на обоих кластерах (это
>     единичные случаи, `dict.account`/`dict.category`/`dict.delivery_point`/
>     `dict.sku` — это независимые таблицы с совпавшим именем, не одна и та же
>     витрина);
>   - «не найдена ни на одном» — таблицы с таким именем нет ни на WMS, ни на
>     DWH сейчас. Строка не удалена из реестра: это, вероятнее всего,
>     переименованная или снесённая витрина, и аналитику полезно это увидеть,
>     а не наткнуться на «нет такой таблицы» без объяснения;
>   - «не ClickHouse» — база `public` не существует ни на одном кластере
>     ClickHouse вообще (проверено по `system.databases`); судя по всему это
>     схема PostgreSQL, доступная через Trino, и в SQL для `clickhouse-wms`/
>     `clickhouse-dwh` эти пять строк не годятся.
>
> Если по строке реестра неясно, какой коннектор звать — не перебирай оба
> наугад: возьми `WMS`/`DWH` из этой колонки; для «не найдена»/«не ClickHouse»
> либо для витрины, которой в реестре нет вообще, спроси человека.

| Витрина | Дашбордов | Кто строит | Кластер | Доверие | Комментарий |
|---|--:|---|---|---|---|
| bronze.accepted_sku_cell | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.accepted_sku_item | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 | WMS |  |  |
| bronze.assembly_packed | 2 | Valerii Merkulov, Pavel Sokal, Yevgeniy Atroshenko + ещё 1 | WMS |  |  |
| bronze.cargo_place_events_v2 | 1 | Ramil Gilfanov | WMS |  |  |
| bronze.cc_hire_funnel_kpi | 1 | Rosina Karimova | WMS |  |  |
| bronze.cc_hire_funnel_kpi_targets | 1 | Rosina Karimova | WMS |  |  |
| bronze.cc_hire_funnel_raw | 1 | Rosina Karimova | WMS |  |  |
| bronze.cc_hire_funnel_study | 1 | Rosina Karimova | WMS |  |  |
| bronze.clickstream_events | 3 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 | WMS |  |  |
| bronze.compensation_info_google_sheet_transfer | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.daily_gmv | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.delivery_db_delivery_point | 1 | Yevgeniy Atroshenko, Denis Platon | WMS |  |  |
| bronze.invoice_data_for_balance | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 | WMS |  |  |
| bronze.invoice_events | 1 | Nikita Lyubchenko | WMS |  |  |
| bronze.missing_history | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 | WMS |  |  |
| bronze.missing_recovery_history | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 | WMS |  |  |
| bronze.movement_sku_item_history | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.one_c_shortage_compensation | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.ops_wms_assembly_events_v2 | 2 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.order_return_v2 | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.pvz_hire_funnel | 1 | Rosina Karimova | WMS |  |  |
| bronze.pvz_hire_funnel_study | 1 | Rosina Karimova | WMS |  |  |
| bronze.seller_return_completed | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.seller_return_utilization | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.sku_item_history_events_v2 | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 | WMS |  |  |
| bronze.stock_balance | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| bronze.study_csat_raw | 1 | Rosina Karimova | WMS |  |  |
| bronze.ticket_changes | 2 | Rosina Karimova | WMS |  |  |
| clickstream_b2b.events | 5 | Rida Zabirova | DWH |  |  |
| clickstream_b2b.sessions | 1 | Rida Zabirova | DWH |  |  |
| dict.account | 12 | Rida Zabirova, Pavel Sokal, Rosina Karimova + ещё 4 | WMS, DWH |  |  |
| dict.category | 12 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 | WMS, DWH |  |  |
| dict.cell | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 | WMS |  |  |
| dict.city | 17 | Rida Zabirova, Pavel Sokal, Kristina Silina + ещё 5 | DWH |  |  |
| dict.currency_rates | 1 | Rida Zabirova | DWH |  |  |
| dict.delivery_delivery_point | 1 | Valerii Merkulov, Ramil Gilfanov | WMS |  |  |
| dict.delivery_point | 15 | Rida Zabirova, Kristina Silina, Artem Voronov + ещё 4 | WMS, DWH |  |  |
| dict.department | 1 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS |  |  |
| dict.department_history_work_structure | 1 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS |  |  |
| dict.drop_off_point | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| dict.drop_off_point_timeslot | 1 | Rida Zabirova | DWH |  |  |
| dict.fbs_seller_lock | 1 | Rida Zabirova | DWH |  |  |
| dict.fbs_seller_lock_history | 1 | Rida Zabirova | DWH |  |  |
| dict.flight_delivery_item | 1 | Yevgeniy Atroshenko, Denis Platon | WMS |  |  |
| dict.geo_h3_to_territories | 2 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | DWH |  |  |
| dict.geo_territories | 2 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | DWH |  |  |
| dict.invoice_sku | 1 | Rida Zabirova | DWH |  |  |
| dict.ops_logistics_delivery_orders | 4 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 | WMS |  |  |
| dict.ops_logistics_pallet | 1 | Kristina Silina, Yevgeniy Atroshenko | WMS |  |  |
| dict.order_return | 3 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 | WMS |  |  |
| dict.orgchart | 8 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov + ещё 5 | WMS |  |  |
| dict.place | 1 | Ramil Gilfanov | WMS |  |  |
| dict.product | 13 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 3 | DWH |  |  |
| dict.promo | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko + ещё 1 | DWH |  |  |
| dict.seller | 7 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 3 | DWH |  |  |
| dict.seller_personal_data | 9 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 3 | DWH |  |  |
| dict.seller_return_drop | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| dict.shipment | 1 | Yevgeniy Atroshenko, Denis Platon | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| dict.shipment_cargo_place | 1 | Kristina Silina, Yevgeniy Atroshenko | WMS |  |  |
| dict.shipment_delivery_item | 2 | Kristina Silina, Yevgeniy Atroshenko, Denis Platon | WMS |  |  |
| dict.shipment_delivery_point | 4 | Ilya Kadochnikov, Anton Bykov, Aleksandra Petrukhina + ещё 2 | WMS |  |  |
| dict.shop | 6 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 3 | DWH |  |  |
| dict.sku | 16 | Rida Zabirova, Pavel Sokal, Kristina Silina + ещё 4 | WMS, DWH |  |  |
| dict.stock | 2 | Pavel Sokal, Yevgeniy Atroshenko, Denis Platon + ещё 1 | WMS |  |  |
| dict.stock_uuid_pk | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| dict.wave | 1 | Pavel Sokal | WMS |  |  |
| dict.wms_assembly | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| dict.wms_orders_v2 | 4 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 | WMS |  |  |
| dict.wms_packed_unit | 1 | Kristina Silina, Yevgeniy Atroshenko | WMS |  |  |
| dict.wms_wms_order | 2 | Kristina Silina, Yevgeniy Atroshenko, Denis Platon | WMS |  |  |
| dict.zone | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 3 | WMS |  |  |
| gold.active_sku_detailed | 1 | Rida Zabirova | DWH |  |  |
| gold.adrev_active_bids_statistic_daily | 1 | Rida Zabirova | DWH |  |  |
| gold.attraction_sellers | 1 | Rida Zabirova | DWH |  |  |
| gold.csi_customers_responses | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina | DWH |  |  |
| gold.currency_rates | 4 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 2 | DWH |  |  |
| gold.daily_sellers_metrics | 1 | Rida Zabirova | DWH |  |  |
| gold.geo_distance_metric_hist | 1 | Ilya Kadochnikov, Ivan Ilichev, Aleksandra Petrukhina | DWH |  |  |
| gold.geo_dq_checks_hist | 1 | Ilya Kadochnikov, Ivan Ilichev, Aleksandra Petrukhina | DWH |  |  |
| gold.geo_dq_hist | 1 | Ilya Kadochnikov, Ivan Ilichev, Aleksandra Petrukhina | DWH |  |  |
| gold.growthbook_clickhouse_migration_inventory | 1 | Anton Bykov | DWH |  |  |
| gold.growthbook_configuration_state | 1 | Anton Bykov | DWH |  |  |
| gold.installs_sellers_funnel | 5 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| gold.joom_financial_measures | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko + ещё 1 | DWH |  |  |
| gold.joom_orders | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.meta_sku_id | 1 | Rida Zabirova | DWH |  |  |
| gold.new_sku_on_stock | 2 | Rida Zabirova | DWH |  |  |
| gold.oos_by_products_data | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.order_items | 2 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | DWH |  |  |
| gold.product_creation_time | 1 | Rida Zabirova | DWH |  |  |
| gold.product_funnel_weekly | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.pvz_funnel | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | DWH |  |  |
| gold.pvz_integral_rating | 1 | Rida Zabirova | DWH |  |  |
| gold.query_conversions_weekly | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.sellers_lk_visits | 1 | Rida Zabirova | DWH |  |  |
| gold.sellers_partners_metrics_last | 1 | Rida Zabirova | DWH |  |  |
| gold.sellers_partners_united_stock | 1 | Rida Zabirova | DWH |  |  |
| gold.sellers_sales | 1 | Rida Zabirova | DWH |  |  |
| gold.skus_filters | 1 | Rida Zabirova | DWH |  |  |
| gold.stock_with_sales_info | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.users_daily_by_auth | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.users_daily_by_platform | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.weekly_region_funnel | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.weekly_retention | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.weekly_user_funnel_by_l1_categories | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.weekly_user_funnel_by_l2_categories | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| gold.weekly_users | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| golden.account_work_structure | 3 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS |  |  |
| golden.courier_order_items | 2 | Yevgeniy Atroshenko, Denis Platon | WMS |  |  |
| golden.dp_dict_extended_hist | 5 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 | WMS |  |  |
| golden.drop_off_type_list | 4 | Ilya Kadochnikov, Anton Bykov, Aleksandra Petrukhina + ещё 2 | WMS |  |  |
| golden.efficiency_mart | 2 | Pavel Sokal, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | WMS | доверяем | основной источник производительности |
| golden.extended_incidents | 2 | Valerii Merkulov, Pavel Sokal, Yevgeniy Atroshenko + ещё 1 | WMS |  |  |
| golden.hrops_main_metrics | 0 |  | WMS | с оговоркой | всегда дедуплицируй по worker_id |
| golden.nasiya_marts_reports_rep_pvz_stat | 1 | Rida Zabirova, Yevgeniy Atroshenko | WMS |  |  |
| golden.nearest_timeslot_hist | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | WMS |  |  |
| golden.order_return_from_assembly | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| golden.order_timeline | 4 | Ilya Kadochnikov, Anton Bykov, Aleksandra Petrukhina + ещё 2 | WMS |  |  |
| golden.orgchart | 4 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS |  |  |
| golden.routes_data | 4 | Ilya Kadochnikov, Anton Bykov, Aleksandra Petrukhina + ещё 2 | WMS |  |  |
| golden.salary_dashboard_table | 1 | Rosina Karimova | WMS | 🚫 запрещено — правило 1 | Зарплатные данные, агент не трогает — см. `CLAUDE.md`, правило 1 |
| golden.salary_norms_for_productivity | 3 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS | 🚫 запрещено — правило 1 | Зарплатные данные, агент не трогает — см. `CLAUDE.md`, правило 1 |
| golden.sku_balance_v2 | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| golden.table_by_okz_efficiency_selection | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon | WMS |  |  |
| golden.table_by_okz_efficiency_sort | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon | WMS |  |  |
| golden.table_by_okz_efficiency_sort_2 | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon | WMS |  |  |
| golden.vchl_employee_metrics_monthly | 3 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 | WMS |  |  |
| golden.warehouse_working_hours | 1 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS |  |  |
| golden.workers_salary_by_process | 3 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS | 🚫 запрещено — правило 1 | Зарплатные данные, агент не трогает — см. `CLAUDE.md`, правило 1 |
| golden.workers_workdays_absence | 1 | Rosina Karimova, Anton Bykov, Denis Platon | WMS |  |  |
| marketing.cohort_mart | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marketing.daily_metrics_plan_fact | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marketing.daily_plan_fact | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marketing.orders_with_attribution | 2 | Kristina Silina, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| marketing.pepsi_gifts_data | 1 | Rida Zabirova, Yevgeniy Atroshenko | DWH |  |  |
| marketing.promo_product_dict_days | 1 | Rida Zabirova, Yevgeniy Atroshenko | DWH |  |  |
| marketing.promo_product_metrics | 1 | Rida Zabirova | DWH |  |  |
| marketing.promo_products | 3 | Rida Zabirova, Pavel Sokal, Ilya Kadochnikov + ещё 2 | DWH |  |  |
| marts.account_dict | 3 | Rida Zabirova, Yevgeniy Atroshenko | DWH |  |  |
| marts.appfollow_reviews | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.business_support_metrics | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.category_dict | 1 | Rida Zabirova | DWH |  |  |
| marts.city_dict | 2 | Kristina Silina, Yevgeniy Atroshenko, Denis Platon | DWH |  |  |
| marts.counted_placed_inbound_info | 1 | Ilya Kadochnikov | DWH |  |  |
| marts.courier_quality_check | 1 | Yevgeniy Atroshenko | DWH |  |  |
| marts.currency_rates_official | 1 | Rida Zabirova | DWH |  |  |
| marts.daily_product_funnel | 2 | Rida Zabirova | DWH |  |  |
| marts.daily_sku_quantity_eod | 6 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| marts.daily_sku_quantity_eod_extended | 2 | Rida Zabirova | DWH |  |  |
| marts.delivery_point_dict | 9 | Rida Zabirova, Pavel Sokal, Kristina Silina + ещё 4 | DWH |  |  |
| marts.dispatch_pivot_by_directions_info | 1 | Ilya Kadochnikov | DWH |  |  |
| marts.dispatch_pivot_info | 1 | Ilya Kadochnikov | DWH |  |  |
| marts.dispatch_pivot_info_stock_names | 1 | Ilya Kadochnikov | DWH |  |  |
| marts.dp_dict_extended_hist | 8 | Rida Zabirova, Kristina Silina, Ilya Kadochnikov + ещё 3 | DWH |  |  |
| marts.dp_logistics_sla | 1 | Yevgeniy Atroshenko, Denis Platon | DWH |  |  |
| marts.external_reviews | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.feedback | 5 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| marts.franchise_plan_by_segment | 1 | Rida Zabirova, Yevgeniy Atroshenko | DWH |  |  |
| marts.google_delivery_point | 4 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 | DWH |  |  |
| marts.google_dp_reviews | 4 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 | DWH |  |  |
| marts.hotmaps_reviews | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.invoice | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| marts.invoices_photostudio | 1 | Rida Zabirova | DWH |  |  |
| marts.low_prices_guarantee_sku_level_stats | 1 | Rida Zabirova | DWH |  |  |
| marts.master_seller_id_final | 1 | Rida Zabirova | DWH |  |  |
| marts.matrix_selling_sku_in_stock | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.metrics_for_weekly_report | 3 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko + ещё 1 | DWH |  |  |
| marts.oos_analysis | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.operation_backlog | 1 | Ilya Kadochnikov | DWH |  |  |
| marts.order_items | 18 | Rida Zabirova, Kristina Silina, Artem Voronov + ещё 4 | DWH |  |  |
| marts.partner_dp_list | 1 | Rida Zabirova, Yevgeniy Atroshenko | DWH |  |  |
| marts.partner_reward_costs | 1 | Rida Zabirova, Yevgeniy Atroshenko | DWH |  |  |
| marts.plan_fact_sheet | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.product_moderation | 1 | Rida Zabirova | DWH |  |  |
| marts.product_moderation_log | 2 | Rida Zabirova | DWH |  |  |
| marts.product_moderation_queue | 2 | Rida Zabirova | DWH |  |  |
| marts.product_original_flag | 1 | Rida Zabirova | DWH |  |  |
| marts.return_items | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.rk_assortment_report_seller_list | 1 | Rida Zabirova | DWH |  |  |
| marts.seller_manager | 1 | Rida Zabirova | DWH |  |  |
| marts.sellers_info | 7 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| marts.sku_dict | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | DWH |  |  |
| marts.tagged_reviews | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.uncollected_orders | 1 | Ilya Kadochnikov | DWH |  |  |
| marts.user_daily_metrics | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts.yandex_dp_reviews | 4 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 2 | DWH |  |  |
| marts_b2b.sx_daily_marts | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts_b2c.all_limits_nasiya_dau | 2 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina + ещё 1 | DWH |  |  |
| marts_b2c.apidb_ke_delivery_point | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | DWH |  |  |
| marts_b2c.apidb_ke_sku | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon | DWH |  |  |
| marts_b2c.experiment_metrics_weekly | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts_b2c.finance_margin_by_order_item | 14 | Rida Zabirova, Pavel Sokal, Artem Voronov + ещё 5 | DWH |  |  |
| marts_b2c.finance_margin_stable_with_corrections | 2 | Rida Zabirova, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts_b2c.nasiya_user_daily_metrics | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| marts_b2c.uzumcard_user_daily_metrics | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| matching.competitors_data | 1 | Anton Bykov | DWH |  |  |
| public.dag | 1 | Anton Bykov | не ClickHouse (вероятно Trino/PostgreSQL) |  |  |
| public.dag_run | 1 | Anton Bykov | не ClickHouse (вероятно Trino/PostgreSQL) |  |  |
| public.delivery_point | 1 | Valerii Merkulov, Ramil Gilfanov | не ClickHouse (вероятно Trino/PostgreSQL) |  |  |
| public.post_paid_restrict | 1 | Ilya Kadochnikov | не ClickHouse (вероятно Trino/PostgreSQL) |  |  |
| public.sku | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | не ClickHouse (вероятно Trino/PostgreSQL) |  |  |
| sandbox.daily_balance | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| sandbox.exit_interview_results | 1 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS |  |  |
| sandbox.franchise_lk_analytics_events_detail | 1 | Ivan Ilichev | DWH |  |  |
| sandbox.oe_3268_pvz_client_cogorts_grouped | 1 | Ivan Ilichev | DWH |  |  |
| sandbox.workers_mistakes_in_lk | 2 | Rosina Karimova | WMS |  |  |
| silver.account_workday | 1 | Rosina Karimova | WMS |  |  |
| silver.account_workday_clean | 3 | Pavel Sokal, Rosina Karimova, Ilya Kadochnikov | WMS |  |  |
| silver.airflow_dag | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.airflow_dag_run | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.airflow_log | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.airflow_task_instance_history | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.assembly_packed | 1 | Valerii Merkulov, Ramil Gilfanov | WMS |  |  |
| silver.bitrix_contacts_icb | 1 | Rida Zabirova | DWH |  |  |
| silver.bitrix_pvz_leads | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.bitrix_pvz_stages | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.calendar | 1 | Anton Bykov | DWH |  |  |
| silver.ci_llm_review_log | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.ci_merge_log | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.clickstream_my_penalties_events | 1 | Rosina Karimova, Anton Bykov, Denis Platon | WMS |  |  |
| silver.clickstream_worker_profile_events | 2 | Rosina Karimova | WMS |  |  |
| silver.compensation_price | 3 | Valerii Merkulov, Pavel Sokal, Ilya Kadochnikov + ещё 4 | WMS |  |  |
| silver.customer_oe_not_picked_up_order_survey_result | 1 | Ilya Kadochnikov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.daily_seller_return_utilization | 2 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| silver.daily_stock_balance | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| silver.delivery_item_events | 1 | Kristina Silina, Yevgeniy Atroshenko | WMS |  |  |
| silver.delivery_point_orders_daily | 1 | Kristina Silina, Yevgeniy Atroshenko | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.dict_seller | 1 | Rida Zabirova | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.dp_location_scoring | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | DWH |  |  |
| silver.dpa_metrics | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.dpa_metrics_inventory | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.dpa_metrics_new_specs | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.dpa_metrics_new_topics | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.dynrouting_next_day_pvz_planned | 1 | Kristina Silina | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.growthbook_datasources | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.growthbook_dimensions | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.growthbook_fact_metrics | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.growthbook_fact_tables | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.growthbook_metrics | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.growthbook_segments | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.hire_funnel | 1 | Rosina Karimova | WMS |  |  |
| silver.iceberg_table_metrics | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.incident_logs | 1 | Valerii Merkulov, Ramil Gilfanov | WMS |  |  |
| silver.incident_tables | 1 | Valerii Merkulov, Ramil Gilfanov | WMS |  |  |
| silver.logistics_hire_funnel | 1 | Rosina Karimova | WMS |  |  |
| silver.logistics_schedule_last_mile_hist | 2 | Kristina Silina, Yevgeniy Atroshenko, Denis Platon | WMS |  |  |
| silver.marketing_daily_crm_cdp_stats | 1 | Rida Zabirova, Ilya Kadochnikov, Aleksandra Petrukhina | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.matching_total_stats | 1 | Rida Zabirova | DWH |  |  |
| silver.mlgrowth_cannibalisation_prediction | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | DWH |  |  |
| silver.order_items | 2 | Valerii Merkulov, Kristina Silina, Ilya Kadochnikov + ещё 2 | DWH |  |  |
| silver.potentially_missing | 1 | Pavel Sokal, Yevgeniy Atroshenko, Ramil Gilfanov | WMS |  |  |
| silver.preliminary_cm2_by_order_item | 2 | Ilya Kadochnikov, Aleksandra Petrukhina, Yevgeniy Atroshenko | DWH |  |  |
| silver.product | 1 | Rida Zabirova | DWH |  |  |
| silver.product_moderation_session | 1 | Rida Zabirova | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.product_moderation_session_event | 1 | Rida Zabirova | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.pvz_employee_day_agg | 1 | Kristina Silina, Yevgeniy Atroshenko | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.pvz_map_best_points_export | 1 | Rida Zabirova, Artem Voronov, Ilya Kadochnikov + ещё 1 | DWH |  |  |
| silver.pvz_processes_nonoverlap | 1 | Kristina Silina, Yevgeniy Atroshenko | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.sku | 3 | Rida Zabirova, Ilya Kadochnikov | DWH |  |  |
| silver.sku_dict | 1 | Ilya Kadochnikov, Aleksandra Petrukhina, Denis Platon | DWH |  |  |
| silver.survey_no_show_funnel_daily | 1 | Ilya Kadochnikov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.trino_queries | 1 | Anton Bykov | не найдена ни на одном — проверь, не переименована/удалена ли |  |  |
| silver.wms_order_public_order_return_ice | 1 | Valerii Merkulov, Ramil Gilfanov | WMS |  |  |
| silver.wms_order_public_order_return_item_ice | 1 | Valerii Merkulov, Ramil Gilfanov | WMS |  |  |
| silver.workers_csat | 1 | Rosina Karimova | WMS |  |  |
