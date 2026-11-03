# health-tracker

A self-hosted daily health log in one SQLite file. What it tracks (symptoms, medicines, daily factors) is defined in `tracker.json`; copy `tracker.example.json` to start.

Work in progress.

## Files

```
tracker.example.json   what is tracked (copy to tracker.json)
tracker.py             loads the config
schema.sql             tables, the scales table and the `daily` view
```

Your database and `tracker.json` are git-ignored. Run `git config core.hooksPath .githooks` once so `scripts/check-secrets.sh` checks every commit.

## License

MIT, see [LICENSE](LICENSE).
