Repair the slug utility in this workspace.

Requirements:

1. Read `slugger.py` and `test_slugger.py`.
2. Run `python3.11 -m unittest -v` once before editing. This failure is
   required and expected; after observing it, continue to the edit instead of
   retrying the unchanged tests.
3. Edit only `slugger.py`. Keep its public function signature, import `re`, and
   return exactly this expression from `slugify`:
   `re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")`.
4. Run the same test command again and observe a passing suite.
5. Finish with `done`.

Use files in the current working directory only. Do not create other files.
