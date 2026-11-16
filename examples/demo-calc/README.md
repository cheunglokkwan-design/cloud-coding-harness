# demo-calc

A deliberately buggy package used to demo the harness: `median()` is wrong for
even-length input and raises `IndexError` instead of `ValueError` on empty input.
`.harness/script.json` is a scripted agent trace that fixes it without an API key.
