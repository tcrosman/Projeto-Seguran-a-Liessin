import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 's2e-api'))

from app.core.database import migrate_database
from app.core.migrations import run_migrations
from app import create_app

print("🔄 Rodando migrações...")
app = create_app()
with app.app_context():
    migrate_database()
    run_migrations()
print("✅ Migrações concluídas!")
