-- Extension to user_sessions to support security center
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS user_agent TEXT;
