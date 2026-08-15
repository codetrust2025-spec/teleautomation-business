# teleautomation-operations

Independent TeleAutomation Operations service. Its hostname is supplied per environment; no production domain is assumed by this repository.

## Local run

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn main:app --reload
cd dashboard; npm ci; npm run dev
```

Runtime data and secrets are intentionally excluded. See `docs/migration/` for frozen contracts and cutover plans.
