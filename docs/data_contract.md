# KeystrokeID data contract (v1, schema_version = 1)

Working draft by person B (data). **To be agreed with A (GUI) and C (ML) on day 1.**

## 1. Sample: GUI -> DATA

```python
{
  'user_id': 'U03',              # pseudonym, not personal data
  'session_id': 'S01',           # label of this user's session: S01, S02, ...
  'prompt_id': 'fixed_01',       # key in data/schemas.py PROMPTS
  'entered_text': 'biometria2026',   # plain TEST text, not a password
  'valid': True,
  'events': [
    {'key': 'b', 'type': 'down', 'time_ms': 0.0},
    {'key': 'b', 'type': 'up',   'time_ms': 94.2},
    ...
  ]
}
```

| Field | Rule |
|---|---|
| `time_ms` | float >= 0, **ms since the start of the attempt** (from `time.perf_counter_ns()` on the GUI side). The database does not compute time itself. |
| `type` | only `'down'` / `'up'` |
| `key` | a single character (for ASCII text), case matching the reference text |
| order | events **in order of occurrence**, `time_ms` never decreases |
| overlap | next key `down` before the previous key `up` is **allowed** (UD < 0) |

### Valid attempt (`valid=True`)
- `entered_text` == reference text from `PROMPTS[prompt_id]`,
- every `down` has its `up` (paired by key name, FIFO),
- press order matches the characters of the text in sequence,
- no Backspace, autorepeat or focus loss (the GUI detects these and must then set `valid=False`).

### Invalid attempt (`valid=False`)
The database **stores** it (so the report can state the number of rejected entries) and only requires
a correct event structure. It never reaches training or the CSV export.

### Rejection on save
`save_sample` raises `SampleError` (nothing is written) when: a field is missing, the event type is wrong,
time is negative/decreasing, `valid=True` with the wrong text or unpaired keys,
or `user_id`/`session_id`/`prompt_id` does not match the session in the database.

## 2. Functions (data/database.py)

```python
init_db(db_path='keystrokeid.db')
user_id    = add_user('U03')                       # ValueError if it already exists
sid        = create_session(user_id, 'fixed_01')   # sid = internal int id; label S01, S02... assigned automatically
sample_id  = save_sample(sid, sample_dict)         # atomic: sample + events or nothing
sample     = load_sample(sample_id)                # returns a dict as above (+ 'sample_id')
samples    = get_user_samples('U03', only_valid=True)
X, y, g    = get_samples_for_training('fixed_01', session_numbers=[1])
n          = export_csv('out.csv')
list_users()
```

**Note for A:** `sample['session_id']` is a **label** (`'S01'`), while `save_sample` takes the **int** returned
by `create_session`. The next session label of a user is `f"S{n:02d}"`, so the GUI must know the session number
(it can be read from `load_sample` of a previous attempt, or a helper can be added if A prefers).

## 3. Features: DATA -> ML (data/features.py)

For a text of `n` characters (`biometria2026` -> n = 13):

```
H_i  = release(i)   - press(i)      i = 1..n      (n values)
DD_i = press(i+1)   - press(i)      i = 1..n-1    (n-1)
UD_i = press(i+1)   - release(i)    i = 1..n-1    (n-1, may be negative)
```

**Fixed vector order:** `[H_1..H_n, DD_1..DD_{n-1}, UD_1..UD_{n-1}]`, length `3n-2` (= 37 for n=13).
Unit: **ms**, type float. Column names: `feature_names(text)`.

`get_samples_for_training` returns:
- `X` - list of vectors (can be wrapped in `numpy.array`),
- `y` - `user_id` labels (strings),
- `groups` - session number of each sample (for the session-based split: train/validation/test).

CSV: columns `user_id, session_number, H_.., DD_.., UD_..`.

## 4. SQLite schema

`users(user_id PK, created_at)` ·
`sessions(id PK, user_id FK, session_number, prompt_id, started_at, device_note, UNIQUE(user_id, session_number))` ·
`samples(sample_id PK, session_id FK, prompt_id, entered_text, valid, created_at, schema_version)` ·
`events(event_id PK, sample_id FK, event_index, key_name, event_type, time_ms, UNIQUE(sample_id, event_index))`.
`PRAGMA foreign_keys=ON`, indexes on `user_id`, `session_id`, `sample_id`.
`created_at`/`started_at` are metadata only (UTC); they are not used to compute features.

## 5. Privacy
The `*.db` file, CSV exports and raw data are in `.gitignore`. Do not commit participant data.
