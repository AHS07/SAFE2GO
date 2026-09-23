"""Import all models so a single `from app.db.models import *` loads everything."""
from app.db.models.cloud import *  # noqa: F401, F403
from app.db.models.edge import *  # noqa: F401, F403
