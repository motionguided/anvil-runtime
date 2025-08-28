DELETE FROM anvil_config WHERE key = 'anvil.platform-runtime.tables/views-cleared';
INSERT INTO anvil_config (key, value) VALUES ('anvil.platform-runtime.tables/views-cleared', '{"val": true}'::jsonb);
