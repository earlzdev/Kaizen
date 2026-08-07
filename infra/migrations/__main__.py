# Entry point so `python -m infra.migrations <action> <service>` works — see
# runner.py for why migrations are driven from Python rather than the CLI.
from infra.migrations.runner import main

raise SystemExit(main())
