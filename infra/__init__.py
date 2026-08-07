# =============================================================================
# Infra — infra/
# =============================================================================
# WHAT: Infrastructure code that isn't a product service: the backup service
#       (infra/backup), the shared gRPC contracts + generated code (infra/proto),
#       and operational shell scripts (infra/scripts).
#
# WHY grouped: keeps the repo root to product code (brain/, agents/, modules/,
#       tools/) while everything operational lives here; deployment manifests
#       (docker/compose, future k8s) live in deploy/.
#
# HOW: python packages import as infra.backup.* / infra.proto.gen.*; scripts run
#       from anywhere (they cd to the repo root themselves).
# =============================================================================
