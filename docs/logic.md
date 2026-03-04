# Rent & Overdue Logic

How rent due date, grace period, overdue status, and amount are determined.

---

## Config (source)

From `app/config.py`:

| Setting | Default | Meaning |
|---------|---------|---------|
| `rent_due_day` | 1 | Day of month rent is due (e.g. 1 = 1st) |
| `grace_period_days` | 5 | Days after due date before tenant is "overdue" |

---

## Amount Due

**Source:** `units.rent_amount`

- The amount due for a month is **fixed** (monthly rent).
- **No per-day or late-fee calculation** in the current implementation.
- Whether the tenant is 1 day or 30 days overdue, the amount due is the same (e.g. Rs 18,000).

---

## Grace Period & Overdue

### Timeline (example: February 2026)

```
Feb 1  → due_date         (rent due)
Feb 6  → grace_date       (last day of grace = due_date + grace_period_days)
Feb 7+ → overdue          (tenant is overdue; days_overdue = today - grace_date)
```

### Days Overdue

```
days_overdue = max(0, (today - grace_date).days)
```

- Before grace_date: `days_overdue = 0`, `is_overdue = False`
- After grace_date: `days_overdue` = number of days past grace, `is_overdue = True`

### Example: Kartik (Feb 27, 2026)

| Field | Value |
|-------|-------|
| Rent | Rs 18,000 (from `units.rent_amount`) |
| due_date | 2026-02-01 |
| grace_date | 2026-02-06 |
| today | 2026-02-27 |
| days_overdue | 21 |
| is_overdue | Yes |
| amount_due | Rs 18,000 (unchanged) |

---

## When is a `rent_cycles` row used vs date-based fallback?

| Scenario | Logic used |
|----------|------------|
| **No `rent_cycles` row** for tenancy + current month | Date-based: `due_date + grace_period_days` → overdue threshold |
| **`rent_cycles.status = 'paid'`** | Never overdue |
| **`rent_cycles.status` in `unpaid`, `partially_paid`, `overdue`** | Use `rent_cycles.grace_date` for days_overdue; status drives is_overdue |

---

## Code References

- Config: `app/config.py` — `rent_due_day`, `grace_period_days`
- Logic: `app/tools/rent_tools.py` — `_fetch_tenancies()` (lines ~28–82)
- Rent cycles created/updated: `app/routers/payments.py` — webhook handler calls `_update_rent_cycle_on_payment`
