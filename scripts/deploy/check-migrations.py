# Read-only: list applied migrations whose dependencies are NOT recorded as applied.
# Run through manage.py shell (see deploy docs). Changes nothing.
from django.db import connection
from django.db.migrations.loader import MigrationLoader

loader = MigrationLoader(connection)
applied = loader.applied_migrations
problems = []
for key in sorted(applied):
    node = loader.graph.node_map.get(key)
    if node is None:
        print("UNKNOWN (applied but no file):", key)
        continue
    for parent in node.parents:
        if parent.key not in applied:
            problems.append((key, parent.key))
            print("INCONSISTENT:", key, "is applied but its dependency", parent.key, "is not")
print("leaf migrations not applied:", len([k for k in loader.graph.leaf_nodes() if k not in applied]))
print("inconsistencies:", len(problems))
