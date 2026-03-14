# Supabase migrations

Run SQL migrations in the Supabase SQL Editor (Dashboard → SQL Editor → New query), or via `supabase db push` if using the Supabase CLI.

- **20260313000000_add_call_analysis_columns.sql** – Adds `ai_summary`, `promise_amount`, `promise_date`, `sentiment` to `call_logs` for storing Sara’s AI analysis (run once per environment).
