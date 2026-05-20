-- Align existing databases with the agents table default introduced by 17_agents.sql.
ALTER TABLE agents
    ALTER COLUMN model SET DEFAULT 'claude-sonnet-4-6';

UPDATE agents
SET model = 'claude-sonnet-4-6'
WHERE model = 'default';
