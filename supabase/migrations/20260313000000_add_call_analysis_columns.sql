-- Store Sara's AI analysis on call_logs so we fetch from DB instead of re-calling the model.
-- Run once (e.g. in Supabase SQL editor or via supabase db push).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'call_logs' AND column_name = 'ai_summary') THEN
    ALTER TABLE call_logs ADD COLUMN ai_summary TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'call_logs' AND column_name = 'promise_amount') THEN
    ALTER TABLE call_logs ADD COLUMN promise_amount TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'call_logs' AND column_name = 'promise_date') THEN
    ALTER TABLE call_logs ADD COLUMN promise_date TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'call_logs' AND column_name = 'sentiment') THEN
    ALTER TABLE call_logs ADD COLUMN sentiment TEXT;
  END IF;
END $$;
