DROP SCHEMA IF EXISTS data_tables CASCADE; -- Data Table view recreation will be triggered by 2025-06-09-recreate-table-views in the runtime DB.
CREATE SCHEMA data_tables;

ALTER TABLE app_storage_data ALTER id TYPE BIGINT;
ALTER SEQUENCE app_storage_data_id_seq AS BIGINT;
ALTER TABLE app_storage_media ALTER row_id TYPE BIGINT;

--[GRANTS]--
ALTER SCHEMA data_tables OWNER TO $ANVIL_USER;
--[/GRANTS]--